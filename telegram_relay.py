"""Optional Telegram copies; no archive scans or retries of ambiguous sends."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import html
import json
import os
from pathlib import Path
import queue
import re
import secrets
import socket
import ssl
import stat
import tempfile
import threading
import time
import zipfile
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from telegram_format import RichText, chunks, message_card, download_caption, event_card
from telegram_topics import TopicRouter, TopicError, TelegramRejected
from telegram_i18n import tr, LANGUAGES

# Cloud Bot API multipart uploads: https://core.telegram.org/bots/api#sending-files
MAX_UPLOAD_BYTES = 50_000_000
ERROR_RESPONSE_BYTES = 8192
API_METHODS = frozenset({"getLocale", "getMe", "getChat", "getChatMember", "createForumTopic",
                         "editForumTopic", "sendMessage", "sendDocument"})


class TelegramConfigurationError(ValueError):
    pass


class AttachmentUnavailable(ValueError):
    """A local validation failure, before any Telegram upload was attempted."""
    def __init__(self, key):
        self.key = key
        super().__init__(tr("en", key))


def _request_details(method, code, reason):
    method = method if method in API_METHODS else "unknown"
    code = code if type(code) is int and 100 <= code <= 599 else "unknown"
    return "method={}; status={}; reason={}".format(method, code, reason)


def _rejection_reason(code, payload):
    # Only fixed categories leave this function; provider text may contain secrets.
    if code == 429:
        return "rate_limited"
    if code == 401:
        return "unauthorized"
    description = payload.get("description") if isinstance(payload, dict) else None
    description = description[:4096].lower() if isinstance(description, str) else ""
    for marker, reason in (
        ("message thread not found", "topic_missing"),
        ("message_thread_id_invalid", "topic_missing"),
        ("topic_deleted", "topic_missing"),
        ("topic_closed", "topic_closed"),
        ("topic is closed", "topic_closed"),
        ("can't parse entities", "invalid_entities"),
        ("cannot parse entities", "invalid_entities"),
        ("bot was blocked", "bot_blocked"),
        ("bot can't initiate conversation", "bot_not_started"),
        ("user is deactivated", "user_deactivated"),
        ("chat not found", "chat_not_found"),
        ("not enough rights", "permission_denied"),
        ("bot is not a member", "permission_denied"),
        ("message is too long", "message_too_long"),
        ("message text is empty", "empty_text"),
        ("file is too big", "file_too_large"),
        ("request entity too large", "file_too_large"),
    ):
        if marker in description:
            return reason
    return "request_rejected"


class TelegramAPIRejected(TelegramRejected):
    """A definite rejection, compatible with the topic creation safety checks."""
    def __init__(self, method, code, payload=None):
        super().__init__(code)
        self.reason = _rejection_reason(code, payload)
        self.details = _request_details(method, code, self.reason)


class TelegramRequestError(RuntimeError):
    """An unconfirmed outcome with static diagnostics and no provider text."""
    def __init__(self, method, reason, code=None):
        self.details = _request_details(method, code, reason)
        super().__init__(self.details)


def _failure_details(exc):
    if isinstance(exc, (TelegramAPIRejected, TelegramRequestError)):
        return exc.details
    return _request_details("unknown", None, "local_or_unclassified_error")


def boolean(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    if value.lower() in {"true", "1", "yes"}:
        return True
    if value.lower() in {"false", "0", "no"}:
        return False
    raise TelegramConfigurationError(name + " must be true or false.")


class TelegramRelay:
    def __init__(self, report, store, *, timezone="Europe/Warsaw", start=True, names=None):
        self.enabled = boolean("TELEGRAM_ENABLED", False)
        self.report, self.store = report, store
        self.zone = ZoneInfo(timezone)
        self.language = "pl"
        self.mode = os.environ.get("TELEGRAM_MODE", "deleted").lower()
        self.send_media = boolean("TELEGRAM_SEND_MEDIA", True)
        self.view_once = boolean("TELEGRAM_VIEW_ONCE", False)
        self.use_topics = boolean("TELEGRAM_TOPICS", False)
        self.topics = None
        self.names = names
        self.items = queue.Queue(maxsize=256)
        self.queued = set()
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = None
        self.next_send = 0.0
        self.interval = 1.05
        if self.enabled:
            self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            self.chat = os.environ.get("TELEGRAM_CHAT_ID", "")
            self.gateway = os.environ.get("TELEGRAM_RELAY_URL", "")
            self.language = "pl" if self.gateway else os.environ.get("TELEGRAM_LANGUAGE", "pl").lower()
            if self.language not in LANGUAGES:
                raise TelegramConfigurationError("TELEGRAM_LANGUAGE must be pl or en.")
            self.account_key = os.environ.get("TELEGRAM_ACCOUNT_KEY", "")
            bot_id = self.token.split(":", 1)[0]
            if self.gateway:
                parsed = urlsplit(self.gateway)
                if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port or parsed.path not in {"", "/"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
                    raise TelegramConfigurationError("The shared Telegram gateway must use a loopback HTTP address.")
                if not re.fullmatch(r"[a-f0-9]{32}\.[a-f0-9]{64}", self.account_key):
                    raise TelegramConfigurationError("The shared Telegram gateway requires an account credential.")
                bot_id = os.environ.get("TELEGRAM_BOT_ID", "")
                if not re.fullmatch(r"[0-9]{1,20}", bot_id):
                    raise TelegramConfigurationError("The shared Telegram bot ID is required.")
            self.interval = 3.1 if self.chat.startswith(("-", "@")) else 1.05
            if self.mode not in {"deleted", "all"}:
                raise TelegramConfigurationError("TELEGRAM_MODE must be deleted or all.")
            if (not self.gateway and not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", self.token)) or not self.chat.strip() or len(self.chat) > 128 or any(char in self.chat for char in "\r\n"):
                raise TelegramConfigurationError("Telegram requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
            if self.use_topics:
                self.topics = TopicRouter(self._api, store, bot_id, self.chat, language=self.language)
            if start:
                self.thread = threading.Thread(target=self._work, name="telegram-relay", daemon=True)
                self.thread.start()

    def _queue_deleted(self, tid, mid):
        key = (tid, mid)
        if not mid or self.stopping.is_set():
            return
        with self.lock:
            if key in self.queued:
                return
            try:
                self.items.put_nowait(("deleted", key))
            except queue.Full:
                self.report("Telegram queue full; one notification was skipped. Local logging continues.")
            else:
                self.queued.add(key)

    def submit(self, summary):
        if not self.enabled or self.stopping.is_set():
            return
        kind = summary["type"]
        if kind == "unsend":
            self._queue_deleted(summary["thread_id"], summary["message_id"])
        else:
            if kind == "message":
                # A delayed capture may fill an earlier deletion tombstone.
                self.media_updated(summary["thread_id"], summary["message_id"])
            if self.mode == "all" and kind != "message":
                try:
                    self.items.put_nowait(("summary", dict(summary)))
                except queue.Full:
                    self.report("Telegram queue full; one relay was skipped. Local logging continues.")

    def media_updated(self, tid, mid):
        if not self.enabled or self.stopping.is_set():
            return
        snapshot = self.store.relay_snapshot(tid, mid)
        if snapshot and self._eligible(snapshot):
            self._queue_deleted(tid, mid)

    def _eligible(self, snapshot):
        if snapshot["deleted"]:
            return snapshot["deleted_run_id"] == self.store.run_id
        once = self.view_once and any(item.get("viewOnce") for item in snapshot["attachments"])
        return snapshot["run_id"] == self.store.run_id and (once or self.mode == "all")

    def _post(self, method, body, content_type="application/json", *, length=None):
        if method != "getLocale":
            if self.stopping.wait(max(0.0, self.next_send - time.monotonic())):
                raise InterruptedError
            self.next_send = time.monotonic() + self.interval
        headers = {"Content-Type": content_type}
        if self.gateway:
            headers["Authorization"] = "Bearer " + self.account_key
        if length is not None:
            headers["Content-Length"] = str(length)
        url = self.gateway.rstrip("/") + "/telegram/" + method if self.gateway else "https://api.telegram.org/bot" + self.token + "/" + method
        request = Request(url,
                          data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=3 if method == "getLocale" else 30) as response:
                raw = response.read(1024 * 1024 + 1)
        except HTTPError as exc:
            # Inspect a bounded error body privately, then discard it. Never log
            # HTTPError itself: its URL includes the bot token.
            payload = None
            try:
                raw = exc.read(ERROR_RESPONSE_BYTES + 1)
                if len(raw) <= ERROR_RESPONSE_BYTES:
                    payload = json.loads(raw)
            except (OSError, ValueError):
                pass
            finally:
                exc.close()
            if 400 <= exc.code < 500:
                raise TelegramAPIRejected(method, exc.code, payload) from None
            raise TelegramRequestError(method, "server_error" if exc.code >= 500 else "http_error", exc.code) from None
        except InterruptedError:
            raise
        except OSError as exc:
            cause = exc.reason if isinstance(exc, URLError) else exc
            reason = ("dns_error" if isinstance(cause, socket.gaierror) else
                      "tls_error" if isinstance(cause, ssl.SSLError) else
                      "timeout" if isinstance(cause, TimeoutError) else "connection_error")
            raise TelegramRequestError(method, reason) from None
        if len(raw) > 1024 * 1024:
            raise TelegramRequestError(method, "response_too_large")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            raise TelegramRequestError(method, "invalid_response_json") from None
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            code = payload.get("error_code") if isinstance(payload, dict) else None
            if isinstance(code, int) and not isinstance(code, bool) and 400 <= code < 500:
                raise TelegramAPIRejected(method, code, payload)
            reason = "server_error" if type(code) is int and 500 <= code <= 599 else "invalid_response"
            raise TelegramRequestError(method, reason, code)
        return payload.get("result")

    def _api(self, method, parameters):
        return self._post(method, json.dumps(parameters).encode())

    def _language(self):
        if getattr(self, "gateway", ""):
            try:
                preferences = self._api("getLocale", {})
                candidate = preferences.get("language") if isinstance(preferences, dict) else None
                if isinstance(candidate, str) and candidate in LANGUAGES:
                    self.language = candidate
            except (TelegramAPIRejected, TelegramRequestError):
                pass  # Keep this worker's last language if the local lookup fails.
        if self.topics is not None:
            self.topics.language = self.language
        return self.language

    def _topic(self, thread_id, title):
        return self.topics.topic(thread_id, title) if self.topics is not None else None

    def _destination_chat(self):
        return self.topics.chat_id if self.topics is not None and self.topics.chat_id is not None else self.chat

    def _route(self, thread_id, title):
        if self.topics is None:
            return None
        return {"thread_id": thread_id, "title": title, "topic_id": self._topic(thread_id, title)}

    def _send_in_topic(self, action, *, topic_id=None, route=None):
        if route is None:
            return action(topic_id)
        try:
            result = action(route["topic_id"])
        except TelegramAPIRejected as exc:
            if exc.code != 400 or exc.reason != "topic_missing":
                raise
            route["topic_id"] = self.topics.replace_missing(route["thread_id"], route["topic_id"], route["title"])
            self.report("Telegram missing topic replaced; retrying only the rejected request once.")
            try:
                result = action(route["topic_id"])
            except TelegramAPIRejected as retry:
                if retry.code == 400 and retry.reason == "topic_missing":
                    self.topics.confirm(route["thread_id"], route["topic_id"], missing=True)
                    self.report("Telegram rejected the replacement topic; automatic repair paused. Use manage-topics to bind a verified existing topic.")
                raise
        self.topics.confirm(route["thread_id"], route["topic_id"])
        return result

    def _text(self, text, *, topic_id=None, route=None):
        rich = text if isinstance(text, RichText) else RichText().add(text)
        for part, entities in rich.pages():
            if self.stopping.is_set():
                raise InterruptedError
            payload = {
                "chat_id": self._destination_chat(), "text": part, "entities": entities, "disable_notification": True,
                "link_preview_options": {"is_disabled": True},
            }
            def send(current_topic):
                if current_topic is not None:
                    payload["message_thread_id"] = current_topic
                return self._post("sendMessage", json.dumps(payload).encode())
            self._send_in_topic(send, topic_id=topic_id, route=route)

    def _attempt(self, tid, mid, part, action):
        if self.stopping.is_set() or not self.store.claim_telegram(tid, mid, part):
            return
        try:
            action()
        except Exception as exc:
            self.store.finish_telegram(tid, mid, part, "failed")
            self._report_failure(exc)
        else:
            self.store.finish_telegram(tid, mid, part, "sent")
            self.report("Telegram notification sent.")

    def _report_failure(self, exc):
        if isinstance(exc, TopicError):
            self.report("Telegram topics: " + str(exc) + " Local content remains saved.")
            return
        self.report("Telegram relay failed (" + _failure_details(exc) +
                    "); captured content remains local. No automatic resend.")

    @contextmanager
    def _saved_file(self, media):
        relative = Path(media.get("localPath", ""))
        root = self.store.root / "media"
        target = self.store.root / relative
        if relative.is_absolute() or not relative.parts or relative.parts[0] != "media" or not target.resolve().is_relative_to(root):
            raise AttachmentUnavailable("file_unavailable")
        try:
            descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            raise AttachmentUnavailable("file_unavailable") from None
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_UPLOAD_BYTES or info.st_size != media.get("savedBytes"):
                raise AttachmentUnavailable("file_limit")
            digest = hashlib.sha256()
            while block := handle.read(65536):
                digest.update(block)
            if digest.hexdigest() != media.get("sha256"):
                raise AttachmentUnavailable("file_integrity")
            filename = re.sub(r'[^A-Za-z0-9._-]', "_", target.name)[:150] or "attachment.bin"
            handle.seek(0)
            yield handle, filename

    def _upload(self, handle, filename, caption, *, topic_id=None, route=None):
        size = handle.seek(0, os.SEEK_END)
        if size > MAX_UPLOAD_BYTES:
            raise AttachmentUnavailable("bundle_limit")
        rich = caption if isinstance(caption, RichText) else RichText().add(caption)
        if rich.length > 900:
            raise ValueError("Use a downloadable transcript for long captions.")
        caption_entities = [entity for entity in rich.entities if entity["type"] not in {"blockquote", "expandable_blockquote"}]
        boundary = "messenger-" + secrets.token_hex(16)
        suffix = ("\r\n--" + boundary + "--\r\n").encode()
        def send(current_topic):
            # Only a definite missing-topic rejection permits rewinding and retrying.
            handle.seek(0)
            fields = {"chat_id": self._destination_chat(), "caption": rich.text,
                      "caption_entities": json.dumps(caption_entities), "disable_notification": "true"}
            if current_topic is not None:
                fields["message_thread_id"] = str(current_topic)
            prefix = b""
            for key, value in fields.items():
                prefix += ("--" + boundary + '\r\nContent-Disposition: form-data; name="' + key + '"\r\n\r\n' + value + "\r\n").encode()
            prefix += ("--" + boundary + '\r\nContent-Disposition: form-data; name="document"; filename="' + filename + '"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
            def body():
                yield prefix
                while block := handle.read(65536):
                    if self.stopping.is_set():
                        raise InterruptedError
                    yield block
                yield suffix
            return self._post("sendDocument", body(), "multipart/form-data; boundary=" + boundary,
                              length=len(prefix) + size + len(suffix))
        return self._send_in_topic(send, topic_id=topic_id, route=route)

    def _document(self, media, caption, *, topic_id=None, route=None):
        with self._saved_file(media) as (handle, filename):
            return self._upload(handle, filename, caption, topic_id=topic_id, route=route)

    def _transcript(self, notice, card, route, language):
        if card.length <= 3500:
            self._text(card, route=route)
        else:
            with tempfile.TemporaryFile(dir=self.store.root) as output:
                output.write(card.text.encode("utf-8"))
                self._upload(output, "message.txt", download_caption(notice, tr(language, "message_details"), language), route=route)

    def _notice(self, snapshot, notice, card, route, language):
        saved = [media for media in snapshot["attachments"] if media.get("downloadStatus") == "saved"] if self.send_media else []
        try:
            if not saved:
                self._transcript(notice, card, route, language)
            elif len(saved) == 1 and card.length <= 900:
                self._document(saved[0], card, route=route)
            else:
                # Anonymous temporary files disappear on close/crash and cannot
                # leave an extra archive behind after the retention deadline.
                details = card.text.encode("utf-8")
                if len(details) + sum(media.get("savedBytes", 0) for media in saved) > MAX_UPLOAD_BYTES:
                    raise AttachmentUnavailable("bundle_limit")
                with tempfile.TemporaryFile(dir=self.store.root) as output:
                    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as bundle:
                        bundle.writestr("message.txt", details)
                        for index, media in enumerate(saved, 1):
                            with self._saved_file(media) as (source, filename):
                                with bundle.open(f"{index:02d}_" + filename, "w") as destination:
                                    while block := source.read(65536):
                                        if self.stopping.is_set():
                                            raise InterruptedError
                                        destination.write(block)
                    self._upload(output, "message-attachments.zip", download_caption(notice, tr(language, "zip_details"), language), route=route)
        except AttachmentUnavailable as exc:
            self._transcript(notice, card.add("\n\n" + tr(language, exc.key), "italic"), route, language)

    def _deleted(self, tid, mid):
        snapshot = self.store.relay_snapshot(tid, mid)
        if not snapshot or not self._eligible(snapshot):
            return
        if self.send_media and any(media.get("downloadStatus") == "pending" for media in snapshot["attachments"]):
            return  # MediaSaver queues us again after every terminal download result.
        if self.names is not None:
            self.names.wait_for(snapshot["sender_id"])
            snapshot = self.store.relay_snapshot(tid, mid)
            if not snapshot or not self._eligible(snapshot):
                return
        language = self._language()
        route = self._route(tid, "" if snapshot.get("conversation_kind") else snapshot["conversation"])
        notice = dict(snapshot)
        notice["attachments"] = [{key: value for key, value in item.items() if key not in {"url", "previewUrl", "localPath"}}
                                 for item in snapshot["attachments"]]
        phase = "deleted" if snapshot["deleted"] else "message"
        self._attempt(tid, mid, "notice:" + phase, lambda: self._notice(snapshot, notice, message_card(notice, self.zone, language), route, language))

    def _work(self):
        while not self.stopping.is_set():
            try:
                kind, item = self.items.get(timeout=0.2)
            except queue.Empty:
                continue
            if kind == "deleted":
                with self.lock:
                    self.queued.discard(item)
            try:
                if kind == "deleted":
                    self._deleted(*item)
                else:
                    plain = html.unescape(re.sub(r"<[^>]+>", "", item["body"]))
                    language = self._language()
                    event = item.get("telegram")
                    route = self._route(item["thread_id"], "" if isinstance(event, dict) and event.get("conversation_kind") else item["thread"])
                    self._text(event_card(item["thread"], plain, item["type"], language, event=event), route=route)
            except TopicError as exc:
                self.report("Telegram topics: " + str(exc) + " Local content remains saved.")
            except Exception as exc:
                self._report_failure(exc)
            finally:
                self.items.task_done()

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=2)
        unfinished = self.items.unfinished_tasks
        if unfinished:
            self.report("Telegram notifications may be unfinished at shutdown; captured content remains local.")
        return self.thread is None or not self.thread.is_alive()
