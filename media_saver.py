"""Download newly captured attachments through the pinned, receive-only bridge."""
from __future__ import annotations

import base64
import binascii
import hashlib
import mimetypes
import os
from pathlib import Path
import queue
import re
import tempfile
import threading

from bridge_rpc import BridgeFailure
from event_store import DOWNLOAD_TYPES, StoreFailure, number, text_value

NATIVE_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_BYTES = 25 * 1024 * 1024
TRANSIENT_FIELDS = {
    "type", "url", "fileName", "mimeType", "fileSize", "directPath",
    "mediaKey", "mediaSha256", "mediaEncSha256",
}


class MediaError(ValueError):
    """Static failure category; never a URL, a key, or provider text."""


def media_limit():
    value = os.environ.get("LOGGER_MEDIA_MAX_MB", "25")
    if not re.fullmatch(r"[0-9]{1,3}", value) or not 1 <= int(value) <= 100:
        raise StoreFailure("LOGGER_MEDIA_MAX_MB must be an integer from 1 to 100.")
    return int(value) * 1024 * 1024


def download_request(attachment, encrypted, max_bytes):
    size = number(attachment.get("fileSize"))
    if size < 0:
        raise MediaError("invalid_metadata")
    if size > max_bytes:
        raise MediaError("too_large")
    if encrypted or attachment.get("mediaKey") or attachment.get("directPath"):
        params = {key: text_value(attachment.get(key)) for key in (
            "directPath", "mediaKey", "mediaSha256", "mediaEncSha256", "mimeType",
        )}
        if not params["directPath"]:
            raise MediaError("missing_download_metadata")
        for field in ("mediaKey", "mediaSha256", "mediaEncSha256"):
            if field == "mediaEncSha256" and not params[field]:
                continue
            try:
                if len(base64.b64decode(params[field], validate=True)) != 32:
                    raise ValueError
            except (ValueError, binascii.Error):
                raise MediaError("missing_download_metadata") from None
        kind = attachment.get("type")
        params.update(mediaType={"gif": "image", "file": "document", "voice": "audio"}.get(kind, kind), fileSize=size)
        return "downloadE2EEMedia", params
    url = text_value(attachment.get("url"))
    if not url:
        raise MediaError("missing_download_metadata")
    # The native method restricts HTTPS hosts and redirects to Meta media CDNs.
    # Do not substitute preview URLs or arbitrary links from the message text.
    return "downloadMedia", {"url": url}


def safe_filename(attachment, data):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "_", text_value(attachment.get("fileName"))).strip(" .")[:100]
    name = name or "attachment"
    extension = ""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        extension = ".png"
    elif data.startswith(b"\xff\xd8\xff"):
        extension = ".jpg"
    elif data.startswith((b"GIF87a", b"GIF89a")):
        extension = ".gif"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        extension = ".webp"
    if not extension and not Path(name).suffix:
        mime = text_value(attachment.get("mimeType")).split(";", 1)[0].strip().lower()
        extension = mimetypes.guess_extension(mime) or ".bin"
    if extension:
        name = (Path(name).stem or "attachment") + extension
    return name


class MediaSaver:
    """One bounded worker; keys exist only in queued jobs, never in the database."""
    def __init__(self, bridge, store, report, stop, *, max_bytes=None, queue_size=64, on_update=None, allow_download=None):
        self.bridge, self.store, self.report, self.stop = bridge, store, report, stop
        self.max_bytes = media_limit() if max_bytes is None else max_bytes
        if not 1 <= self.max_bytes <= NATIVE_MAX_BYTES:
            raise StoreFailure("Invalid media size limit.")
        self.items = queue.Queue(maxsize=queue_size)
        self.stopping = threading.Event()
        self.failure = None
        self.saved = 0
        self.thread = None
        self.on_update = on_update
        self.allow_download = allow_download or (lambda: True)

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._work, name="media-downloads", daemon=True)
            self.thread.start()

    def submit(self, event, summary):
        # Call only after admission/deduplication, using the accepted IDs.
        if summary["type"] != "message" or not summary.get("message_id"):
            return
        attachments = event.get("data", {}).get("attachments")
        if not isinstance(attachments, list):
            return
        encrypted = event.get("type") == "e2eeMessage" or event.get("data", {}).get("isE2EE") is True
        for index, item in enumerate(value for value in attachments if isinstance(value, dict)):
            if item.get("type") not in DOWNLOAD_TYPES:
                continue
            attachment = {key: value for key, value in item.items() if key in TRANSIENT_FIELDS and isinstance(value, (str, int))}
            job = (summary["thread_id"], summary["message_id"], index, encrypted, attachment)
            if not self.allow_download():
                self._finish(job, {"downloadStatus": "disabled"})
                continue
            if self.stopping.is_set() or self.stop.is_set():
                self._finish(job, {"downloadStatus": "interrupted"})
                continue
            try:
                self.items.put_nowait(job)
            except queue.Full:
                self._finish(job, {"downloadStatus": "failed", "downloadError": "queue_full"})
                self.report("Media queue full; one attachment was skipped. Its message metadata is saved.")

    def _finish(self, job, result):
        if not self.store.update_media(job[0], job[1], job[2], result):
            return False
        if self.on_update is not None:
            try:
                self.on_update(job[0], job[1])
            except Exception:
                self.report("Telegram media notification could not be queued. Local attachment state is saved.")
        return True

    def _save(self, job):
        tid, mid, index, encrypted, attachment = job
        method, params = download_request(attachment, encrypted, self.max_bytes)
        result = self.bridge.call(method, params, timeout=120)
        encoded = result.get("data")
        if not isinstance(encoded, str):
            raise MediaError("invalid_download_response")
        if len(encoded) > 4 * ((self.max_bytes + 2) // 3):
            raise MediaError("too_large")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise MediaError("invalid_download_response") from None
        if len(data) > self.max_bytes:
            raise MediaError("too_large")
        if not data:
            raise MediaError("empty_download")
        digest = hashlib.sha256(data).hexdigest()
        if method == "downloadE2EEMedia" and bytes.fromhex(digest) != base64.b64decode(params["mediaSha256"], validate=True):
            raise MediaError("integrity_check_failed")
        with self.store.lock:
            if not self.store.media_available(tid, mid):
                raise MediaError("retention_expired")
            self._write_file(job, attachment, data, digest)

    def _write_file(self, job, attachment, data, digest):
        tid, mid, index, _, _ = job
        token = hashlib.sha256((tid + "\0" + mid).encode()).hexdigest()
        relative = Path("media") / self.store.run_id / token / (str(index + 1).zfill(3) + "_" + safe_filename(attachment, data))
        target = self.store.root / relative
        parent = self.store.root
        for part in relative.parts[:-1]:
            parent = parent / part
            if parent.is_symlink():
                raise MediaError("invalid_local_path")
            parent.mkdir(mode=0o700, exist_ok=True)
        if not target.resolve().is_relative_to(self.store.root):
            raise MediaError("invalid_local_path")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".download-", delete=False) as handle:
                temporary = Path(handle.name)
                os.fchmod(handle.fileno(), 0o600)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        if not self._finish(job, {"downloadStatus": "saved", "localPath": relative.as_posix(), "savedBytes": len(data), "sha256": digest}):
            target.unlink(missing_ok=True)
            raise MediaError("retention_expired")
        self.saved += 1
        self.report(f"Media file saved locally ({self.saved} this run). Use show-messages for its path.")

    def _work(self):
        try:
            while not self.stopping.is_set() or not self.items.empty():
                try:
                    job = self.items.get(timeout=0.2)
                except queue.Empty:
                    continue
                try:
                    if self.stopping.is_set() or self.stop.is_set():
                        self._finish(job, {"downloadStatus": "interrupted"})
                    elif not self.allow_download():
                        self._finish(job, {"downloadStatus": "disabled"})
                    else:
                        try:
                            self._save(job)
                        except (MediaError, BridgeFailure, OSError) as exc:
                            category = str(exc) if isinstance(exc, MediaError) else ("local_file_error" if isinstance(exc, OSError) else "download_failed")
                            status = "interrupted" if self.stopping.is_set() or self.stop.is_set() else "failed"
                            self._finish(job, {"downloadStatus": status, "downloadError": category})
                            if status != "interrupted":
                                self.report("Media attachment was not saved (category=" + category + "). Message logging continues.")
                finally:
                    self.items.task_done()
                    # Release decrypted material and keys before waiting for more events.
                    job = None
        except Exception:
            self.failure = StoreFailure("Media download state could not be stored; private details hidden.")
            self.stop.set()

    def close(self):
        self.stopping.set()
        self.start()  # Also drain/cancel jobs if startup failed before readiness.
        self.thread.join(timeout=5)
        return not self.thread.is_alive()
