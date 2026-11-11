"""Bounded, content-free capture health and account-scoped archive reporting."""
from collections import deque
import json
import threading
import time


def attachment_preview(media):
    kinds = {"image", "video", "audio", "voice", "file", "sticker", "gif", "attachment", "link", "location"}
    states = {"saved", "pending", "failed", "interrupted", "disabled"}
    mime = str(media.get("mimeType", ""))[:120]
    return {"type": media.get("type") if media.get("type") in kinds else "attachment",
            "fileName": str(media.get("fileName", ""))[:100], "mimeType": mime if mime.isascii() else "",
            "downloadStatus": media.get("downloadStatus") if media.get("downloadStatus") in states else "unknown",
            "viewOnce": media.get("viewOnce") is True}


class CaptureMonitor:
    def __init__(self, started_at, *, clock=time.time):
        self.started_at, self.clock = started_at, clock
        self.lock = threading.Lock()
        self.issues = {key: deque(maxlen=10000) for key in ("decryption", "media", "queue")}
        self.counts = dict.fromkeys(self.issues, 0)
        self.last = dict.fromkeys(self.issues)
        self.last_capture = None

    def report(self, message):
        kind = None
        if message.startswith("An encrypted message was unavailable"):
            kind = "decryption"
        elif message.startswith("Media attachment was not saved"):
            kind = "media"
        elif message.startswith(("Media queue full", "Telegram queue full", "Telegram media notification could not be queued")):
            kind = "queue"
        if kind:
            with self.lock:
                now = self.clock()
                self.counts[kind] += 1
                self.last[kind] = now
                self.issues[kind].append(now)

    def captured(self):
        with self.lock:
            self.last_capture = self.clock()

    def snapshot(self):
        with self.lock:
            now = self.clock()
            for values in self.issues.values():
                while values and values[0] < now - 600:
                    values.popleft()
            for key in self.last:
                if self.last[key] is not None and self.last[key] < now - 259200:
                    self.last[key] = None
            if self.last_capture is not None and self.last_capture < now - 259200:
                self.last_capture = None
            return {"started_at": self.started_at, "last_capture": self.last_capture,
                    "counts": dict(self.counts), "last": dict(self.last),
                    "recent": {key: len(values) for key, values in self.issues.items()}}


class ArchiveSync:
    """Retry idempotent dashboard copies independently of Telegram delivery."""
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.dirty = {}
        self.after = 0
        self.next_at = 0
        self.next_scan = 0
        self.scan_epoch = 0

    def changed(self, thread_id, message_id):
        if not message_id:
            return
        with self.lock:
            if len(self.dirty) >= 4096:
                self.after = 0
                self.next_scan = 0
                self.scan_epoch += 1
                self.dirty.clear()
            self.dirty[(thread_id, message_id)] = None

    def report(self, api, revision):
        if time.monotonic() < self.next_at:
            return
        self.next_at = time.monotonic() + 5
        with self.lock:
            dirty = list(self.dirty)[:10]
            for key in dirty:
                self.dirty.pop(key, None)
            after = self.after
            scan = time.monotonic() >= self.next_scan
            scan_epoch = self.scan_epoch
        rows = []
        if scan:
            with self.store.lock:
                rows = self.store.db.execute("SELECT rowid,thread_id,message_id FROM messages WHERE rowid>? ORDER BY rowid LIMIT 10", (after,)).fetchall()
        keys = list(dict.fromkeys(dirty + [(row[1], row[2]) for row in rows]))
        messages, used = [], 0
        try:
            for index, (tid, mid) in enumerate(keys):
                item = self.store.relay_snapshot(tid, mid)
                if not item:
                    continue
                value = {key: item[key] for key in ("sender", "text", "original_text", "created_ms", "captured_at", "deleted", "deleted_at_ms", "last_edit_ms", "conversation")}
                value["preview_truncated"] = any(len(value[key] or "") > 20000 for key in ("text", "original_text")) or len(item["attachments"]) > 100
                for key in ("text", "original_text"):
                    if value[key] is not None:
                        value[key] = value[key][:20000]
                value["sender"], value["conversation"] = value["sender"][:500], value["conversation"][:500]
                value.update(thread_id=tid, message_id=mid, attachments=[attachment_preview(media) for media in item["attachments"][:100]])
                value["deleted"] = bool(value["deleted"])
                size = len(json.dumps(value).encode())
                if used + size > 700000:
                    # Queue the unsent remainder before advancing the initial scan.
                    with self.lock:
                        for key in keys[index:]:
                            self.dirty[key] = None
                    break
                messages.append(value)
                used += size
            if messages:
                api("reportArchive", {"revision": revision, "messages": messages})
            with self.lock:
                if scan and scan_epoch == self.scan_epoch:
                    self.after = rows[-1][0] if rows else 0
                    if not rows:
                        self.next_scan = time.monotonic() + 300
        except Exception:
            with self.lock:
                for key in dirty:
                    self.dirty.setdefault(key, None)
            raise
