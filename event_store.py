"""Normalize typed Messenger events and keep durable, recoverable daily logs."""
from __future__ import annotations

from datetime import datetime
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid
from zoneinfo import ZoneInfo
from identities import display_label, username_label, display_name

MEDIA_FIELDS = {
    "type", "url", "previewUrl", "fileName", "mimeType", "fileSize", "width",
    "height", "duration", "stickerId", "latitude", "longitude", "description", "sourceText", "viewOnce", "viewMode",
}
DOWNLOAD_TYPES = {"image", "video", "audio", "voice", "file", "document", "sticker", "gif"}
EVENT_NAMES = {
    "message": "message", "e2eeMessage": "message", "messageEdit": "edit",
    "messageUnsend": "unsend", "reaction": "reaction", "e2eeReaction": "reaction",
}


class StoreFailure(RuntimeError):
    pass


def identifier(value) -> str:
    if value is None or isinstance(value, (dict, list, bool)):
        return ""
    value = str(value).strip()
    if value in {"", "0", "None"}:
        return ""
    if value.endswith("@msgr"):
        value = value.rsplit("@", 1)[0].split(":", 1)[0]
    return value[:150]


def text_value(value, default="") -> str:
    return value if isinstance(value, str) else default


def clean_media(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    media = [
        {key: item for key, item in entry.items() if key in MEDIA_FIELDS and isinstance(item, (str, int, float, bool))}
        for entry in value if isinstance(entry, dict)
    ]
    for item in media:
        if "viewOnce" in item:
            item["viewOnce"] = item["viewOnce"] is True
        if "viewMode" in item and item["viewMode"] not in {"once", "replay", "limited", "keep"}:
            item.pop("viewMode")
        if item.get("type") in DOWNLOAD_TYPES:
            item["downloadStatus"] = "pending"
    return media


def number(value, default=0):
    try:
        return int(value) if not isinstance(value, bool) else default
    except (TypeError, ValueError, OverflowError):
        return default


class EventStore:
    def __init__(self, data_dir: Path, *, timezone="Europe/Warsaw", own_id="", started_at=None, run_id=None, retention_seconds=None, clock=None):
        self.root = data_dir.resolve()
        self.logs = self.root / "message_logs_modern"
        self.logs.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.zone = ZoneInfo(timezone)
        self.own_id = identifier(own_id)
        self.clock = clock or time.time
        self.retention_seconds = retention_seconds
        self.capture_allowed = lambda tid: True
        self.started_at = started_at
        self.cutoff_ms = math.ceil(started_at * 1000) if started_at is not None else None
        self.run_id = run_id or (uuid.uuid4().hex if started_at is not None else "")
        if self.run_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", self.run_id):
            raise StoreFailure("Invalid run identifier.")
        self.lock = threading.RLock()
        # Unattributed deletions wait briefly in memory for their incoming original.
        # Never create a persistent tombstone that could belong to our own message.
        self.pending_deletions = {}
        path = self.root / "messenger-modern.sqlite3"
        existed = path.is_file()
        self.db = sqlite3.connect(path, check_same_thread=False)
        path.chmod(0o600)
        has_settings = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone()
        if has_settings:
            account = self.db.execute("SELECT value FROM settings WHERE key='account_id'").fetchone()
            if self.own_id and account and account[0] != self.own_id:
                self.db.close()
                raise StoreFailure("This log database belongs to another account. Use a separate DATA_DIR.")
            if account and not self.own_id:
                self.own_id = identifier(account[0])
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.execute("PRAGMA journal_size_limit=0")
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, started_at REAL NOT NULL, timezone TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS contacts (id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS threads (
                id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', type INTEGER NOT NULL DEFAULT 0,
                directory TEXT
            );
            CREATE TABLE IF NOT EXISTS messages (
                thread_id TEXT NOT NULL, message_id TEXT NOT NULL, sender_id TEXT, text TEXT,
                transport TEXT NOT NULL,
                PRIMARY KEY(thread_id, message_id)
            );
            CREATE INDEX IF NOT EXISTS message_lookup ON messages(message_id);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, dedup_key TEXT UNIQUE NOT NULL,
                thread_id TEXT NOT NULL, received_at REAL NOT NULL,
                type TEXT NOT NULL, payload TEXT NOT NULL, line TEXT NOT NULL,
                path TEXT NOT NULL, exported INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS file_offsets (path TEXT PRIMARY KEY, size INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS telegram_attempts (
                run_id TEXT NOT NULL, thread_id TEXT NOT NULL, message_id TEXT NOT NULL,
                part TEXT NOT NULL, status TEXT NOT NULL,
                PRIMARY KEY(run_id,thread_id,message_id,part)
            );
            CREATE TABLE IF NOT EXISTS telegram_topics (
                bot_id TEXT NOT NULL, chat_id TEXT NOT NULL, thread_id TEXT NOT NULL,
                topic_id INTEGER, name TEXT NOT NULL, status TEXT NOT NULL,
                is_fallback INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(bot_id,chat_id,thread_id)
            );
        ''')
        # Additive migration: leave existing captured logs and events intact.
        additions = {
            "contacts": {"username": "TEXT NOT NULL DEFAULT ''", "name_priority": "INTEGER NOT NULL DEFAULT 2", "last_seen": "REAL NOT NULL DEFAULT 0"},
            "threads": {"last_seen": "REAL NOT NULL DEFAULT 0"},
            "events": {"run_id": "TEXT NOT NULL DEFAULT ''", "message_id": "TEXT NOT NULL DEFAULT ''"},
            "messages": {
                "run_id": "TEXT NOT NULL DEFAULT ''", "created_ms": "INTEGER NOT NULL DEFAULT 0",
                "captured_at": "REAL NOT NULL DEFAULT 0", "original_text": "TEXT",
                "attachments": "TEXT NOT NULL DEFAULT '[]'", "reply_json": "TEXT NOT NULL DEFAULT '{}'",
                "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
                "deleted": "INTEGER NOT NULL DEFAULT 0", "deleted_at_ms": "INTEGER",
                "deleted_run_id": "TEXT NOT NULL DEFAULT ''", "last_edit_ms": "INTEGER",
            },
        }
        needs_upgrade = any(
            any(column not in {row[1] for row in self.db.execute("PRAGMA table_info(" + table + ")")} for column in definitions)
            for table, definitions in additions.items()
        )
        backup = self.root / "messenger-modern.before-content.sqlite3"
        if existed and needs_upgrade and not backup.exists() and retention_seconds is None:
            with sqlite3.connect(backup) as snapshot:
                self.db.backup(snapshot)
            backup.chmod(0o600)
        with self.db:
            for table, definitions in additions.items():
                existing = {row[1] for row in self.db.execute("PRAGMA table_info(" + table + ")")}
                for column, definition in definitions.items():
                    if column not in existing:
                        self.db.execute("ALTER TABLE " + table + " ADD COLUMN " + column + " " + definition)
        if self.own_id:
            account = self.db.execute("SELECT value FROM settings WHERE key='account_id'").fetchone()
            if account and account[0] != self.own_id:
                self.db.close()
                raise StoreFailure("This log database belongs to another account. Use a separate DATA_DIR.")
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO settings VALUES ('account_id',?)", (self.own_id,))
        if self.run_id:
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO runs VALUES (?,?,?)", (self.run_id, started_at, timezone))
        # Keys are intentionally not persisted, so interrupted jobs cannot be
        # retried after a restart. Do not present them as still downloading.
        with self.db:
            for tid, mid, raw in self.db.execute("SELECT thread_id,message_id,attachments FROM messages WHERE attachments LIKE '%\"downloadStatus\": \"pending\"%'").fetchall():
                attachments = json.loads(raw)
                for item in attachments:
                    if item.get("downloadStatus") == "pending":
                        item["downloadStatus"] = "interrupted"
                self.db.execute("UPDATE messages SET attachments=? WHERE thread_id=? AND message_id=?", (json.dumps(attachments, ensure_ascii=False), tid, mid))
        # A retention rewrite can be resumed before strict append recovery.
        pending_rebuild = self.db.execute("SELECT value FROM settings WHERE key='retention_rebuild_paths'").fetchone()
        if pending_rebuild:
            from retention import rebuild_exports
            rebuild_exports(self, json.loads(pending_rebuild[0]))
        else:
            self._recover_files()
            self._export_pending()
        with self.db:
            for uid, name, username in self.db.execute("SELECT id,name,username FROM contacts").fetchall():
                cleaned = (display_label(name), username_label(username))
                if cleaned != (name, username):
                    self.db.execute("UPDATE contacts SET name=?,username=? WHERE id=?", (*cleaned, uid))
        if self.retention_seconds is not None:
            self.cleanup_expired()

    def close(self):
        with self.lock:
            self.db.close()

    def _target(self, relative: str) -> Path:
        path = self.logs / relative
        if not path.resolve().is_relative_to(self.logs.resolve()):
            raise StoreFailure("Daily log path is outside the log directory.")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    def _recover_files(self):
        with self.lock:
            for relative, size in self.db.execute("SELECT path,size FROM file_offsets").fetchall():
                path = self._target(relative)
                actual = path.stat().st_size if path.exists() else 0
                if actual < size:
                    raise StoreFailure("A daily log is shorter than its committed size. Restore it before continuing.")
                if actual > size:
                    # Roll back only this logger's interrupted append. It owns this namespace.
                    with path.open("r+b") as handle:
                        handle.truncate(size)
                        handle.flush()
                        os.fsync(handle.fileno())

    def _export_pending(self):
        with self.lock:
            rows = self.db.execute("SELECT id,path,line FROM events WHERE exported=0 ORDER BY id").fetchall()
            for event_id, relative, line in rows:
                path = self._target(relative)
                row = self.db.execute("SELECT size FROM file_offsets WHERE path=?", (relative,)).fetchone()
                if row is None:
                    size = path.stat().st_size if path.exists() else 0
                    with self.db:
                        self.db.execute("INSERT INTO file_offsets VALUES (?,?)", (relative, size))
                else:
                    size = row[0]
                encoded = (line + "\n").encode("utf-8")
                with path.open("r+b" if path.exists() else "w+b") as handle:
                    handle.seek(0, os.SEEK_END)
                    if handle.tell() < size:
                        raise StoreFailure("Daily log was shortened outside this logger.")
                    handle.truncate(size)
                    handle.seek(size)
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                path.chmod(0o600)
                with self.db:
                    self.db.execute("UPDATE file_offsets SET size=? WHERE path=?", (size + len(encoded), relative))
                    self.db.execute("UPDATE events SET exported=1 WHERE id=?", (event_id,))

    def _metadata(self, data):
        with self.db:
            for row in data.get("contacts", []):
                uid = identifier(row.get("id"))
                name = display_label(row.get("name")) or display_label(row.get("firstName"))
                username = username_label(row.get("username"))
                if uid and (name or username):
                    priority = 1 if row.get("priority") == 1 else 2
                    self.db.execute(
                        "INSERT INTO contacts(id,name,username,name_priority,last_seen) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                        "name=CASE WHEN excluded.name<>'' AND (contacts.name='' OR excluded.name_priority>=contacts.name_priority) THEN excluded.name ELSE contacts.name END, "
                        "name_priority=CASE WHEN excluded.name<>'' AND (contacts.name='' OR excluded.name_priority>=contacts.name_priority) THEN excluded.name_priority ELSE contacts.name_priority END, "
                        "username=CASE WHEN excluded.username<>'' THEN excluded.username ELSE contacts.username END,last_seen=excluded.last_seen",
                        (uid, name, username, priority, self.clock()),
                    )
            for row in data.get("threads", []):
                tid = identifier(row.get("id"))
                name = display_label(row.get("name"))
                if tid:
                    self.db.execute(
                        "INSERT INTO threads(id,name,type,last_seen) VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,type=excluded.type,last_seen=excluded.last_seen",
                        (tid, name, number(row.get("type")), self.clock()),
                    )

    def _name(self, user_id):
        row = self.db.execute("SELECT name,username FROM contacts WHERE id=?", (user_id,)).fetchone()
        if row:
            return display_name(*row, own=user_id == self.own_id)
        return "You" if user_id == self.own_id else "Unknown participant"

    def _thread_name(self, tid):
        row = self.db.execute("SELECT name FROM threads WHERE id=?", (tid,)).fetchone()
        if row and display_label(row[0]):
            return display_label(row[0])
        contact = self.db.execute("SELECT name,username FROM contacts WHERE id=?", (tid,)).fetchone()
        return self._name(tid) if contact and any(contact) else "Unnamed conversation"

    def _name_kind(self, user_id):
        row = self.db.execute("SELECT name,username FROM contacts WHERE id=?", (user_id,)).fetchone()
        if row and (display_label(row[0]) or username_label(row[1])):
            return None
        return "self" if user_id == self.own_id else "unknown"

    def _thread_kind(self, tid):
        row = self.db.execute("SELECT name FROM threads WHERE id=?", (tid,)).fetchone()
        if row and display_label(row[0]):
            return None
        contact = self.db.execute("SELECT name,username FROM contacts WHERE id=?", (tid,)).fetchone()
        return None if contact and (display_label(contact[0]) or username_label(contact[1])) else "unnamed"

    def _telegram_event(self, value):
        result = {"message_id": value["message_id"], "sender": self._name(value["sender_id"]),
                  "sender_kind": self._name_kind(value["sender_id"]),
                  "conversation_kind": self._thread_kind(value["thread_id"])}
        if value["type"] == "edit":
            result["text"] = value["text"]
        elif value["type"] == "reaction":
            result["reaction"] = value["reaction"]
            cached = self.db.execute("SELECT sender_id,text FROM messages WHERE thread_id=? AND message_id=? AND (?='' OR run_id=?)",
                                     (value["thread_id"], value["message_id"], self.run_id, self.run_id)).fetchone()
            if cached and (not self.own_id or cached[0] and cached[0] != self.own_id):
                result.update(original=cached[1], original_sender=self._name(cached[0]),
                              original_sender_kind=self._name_kind(cached[0]))
        return result

    def _future_event(self, event):
        """Use message creation time, not the current time assigned to a replay."""
        if self.retention_seconds is not None:
            data = event.get("data") or {}
            stamp = number(data.get("timestampMs") or event.get("timestamp"))
            if stamp <= (self.clock() - self.retention_seconds) * 1000:
                return False
        if self.cutoff_ms is None:
            return True
        data = event.get("data")
        if not isinstance(data, dict):
            return False
        kind = event.get("type")
        stamp = number(data.get("timestampMs"))
        if kind in {"message", "e2eeMessage"}:
            # No creation timestamp means we cannot prove this is a future message.
            return stamp >= self.cutoff_ms and stamp > 0
        # Some live reactions/deletions have only the bridge's observation time.
        # Never query history; an explicit older action time always wins over it.
        if stamp <= 0:
            stamp = number(event.get("timestamp"))
        return stamp >= self.cutoff_ms and stamp > 0

    def _resolve_thread(self, message_id):
        if not message_id:
            return ""
        rows = self.db.execute("SELECT thread_id FROM messages WHERE message_id=? LIMIT 2", (message_id,)).fetchall()
        return rows[0][0] if len(rows) == 1 else ""

    def _incoming(self, event, received):
        if not self.own_id:
            return True  # Legacy/offline stores without an authenticated identity.
        data = event.get("data")
        if not isinstance(data, dict):
            return False
        kind = EVENT_NAMES[event["type"]]
        mid = identifier(data.get("id") if kind == "message" else data.get("messageId"))
        tid = identifier(data.get("threadId") or data.get("chatJid") or data.get("chat")) or self._resolve_thread(mid) or "unresolved"
        uid = identifier(data.get("senderId") or data.get("actorId") or data.get("readerId") or data.get("senderJid") or data.get("sender"))
        for key, (seen, _) in list(self.pending_deletions.items()):
            if seen < received - 120:
                self.pending_deletions.pop(key, None)
        if kind == "message":
            if not uid or uid == self.own_id:
                self.pending_deletions.pop((tid, mid), None)
                self.pending_deletions.pop(("unresolved", mid), None)
                return False
            return True
        previous = self.db.execute("SELECT sender_id FROM messages WHERE thread_id=? AND message_id=?", (tid, mid)).fetchone()
        if previous and previous[0] and previous[0] != self.own_id:
            return kind == "unsend" or uid != self.own_id
        if kind == "unsend" and mid and previous is None:
            pending = {"type": "messageUnsend", "timestamp": event.get("timestamp"), "data": {
                "threadId": tid, "messageId": mid, "timestampMs": number(data.get("timestampMs") or event.get("timestamp")),
            }}
            self.pending_deletions.setdefault((tid, mid), (received, pending))
            while len(self.pending_deletions) > 4096:
                self.pending_deletions.pop(next(iter(self.pending_deletions)))
        return False

    def _safe_reply(self, reply, tid):
        reply = dict(reply)
        if self.own_id and reply:
            cached = self.db.execute("SELECT sender_id FROM messages WHERE thread_id=? AND message_id=?", (tid, reply.get("message_id", ""))).fetchone()
            author = (cached[0] if cached else None) or reply.get("sender_id")
            if not author or author == self.own_id:
                reply["text"] = ""
            if author:
                reply["sender_id"] = author
        return reply

    def _normalize(self, event):
        source_type = event.get("type")
        kind = EVENT_NAMES.get(source_type)
        data = event.get("data")
        if not kind or not isinstance(data, dict):
            return None
        mid = identifier(data.get("id") if kind == "message" else data.get("messageId"))
        tid = identifier(data.get("threadId") or data.get("chatJid") or data.get("chat"))
        if not tid:
            tid = self._resolve_thread(mid) or "unresolved"
        uid = identifier(data.get("senderId") or data.get("actorId") or data.get("readerId") or data.get("senderJid") or data.get("sender"))
        normalized = {
            "type": kind, "transport": "encrypted" if source_type.startswith("e2ee") or data.get("isE2EE") else ("unknown" if kind == "edit" else "regular"),
            "thread_id": tid, "sender_id": uid, "message_id": mid,
            "timestamp_ms": number(data.get("timestampMs") or event.get("timestamp")),
        }
        if kind != "message" and mid:
            previous = self.db.execute("SELECT transport FROM messages WHERE thread_id=? AND message_id=?", (tid, mid)).fetchone()
            if previous:
                normalized["transport"] = previous[0]
        if kind == "message":
            if uid and (data.get("senderName") or data.get("senderUsername")):
                self._metadata({"contacts": [{"id": uid, "name": data.get("senderName"), "username": data.get("senderUsername"), "priority": 1}], "threads": []})
            normalized.update(text=text_value(data.get("text")), attachments=clean_media(data.get("attachments")))
            if not mid:
                for media in normalized["attachments"]:
                    if media.get("downloadStatus") == "pending":
                        media.update(downloadStatus="failed", downloadError="missing_message_id")
            mentions = []
            for item in data.get("mentions") if isinstance(data.get("mentions"), list) else []:
                if isinstance(item, dict) and identifier(item.get("userId")):
                    user_id = identifier(item.get("userId"))
                    mentions.append({"user_id": user_id, "name": self._name(user_id), "offset": number(item.get("offset")), "length": number(item.get("length"))})
            normalized["metadata"] = {"mentions": mentions, "system_message": data.get("isAdminMsg") is True}
            if isinstance(data.get("replyTo"), dict):
                reply = data["replyTo"]
                normalized["reply"] = self._safe_reply({"message_id": identifier(reply.get("messageId")), "sender_id": identifier(reply.get("senderId")), "text": text_value(reply.get("text"))}, tid)
            title = text_value(data.get("threadName"))
            if title:
                self.db.execute("INSERT INTO threads(id,name,type) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name", (tid, title, number(data.get("threadType"))))
        elif kind == "edit":
            normalized.update(text=text_value(data.get("newText")), edit_count=number(data.get("editCount")))
        elif kind == "reaction":
            normalized["reaction"] = text_value(data.get("reaction"))
        return normalized

    def _body(self, event):
        esc = html.escape
        who = "<b>" + esc(self._name(event["sender_id"])) + "</b>"
        kind = event["type"]
        if kind == "message":
            parts = [esc(event["text"])] if event["text"] else []
            for media in event.get("attachments", []):
                label = text_value(media.get("type"), "attachment")
                filename = text_value(media.get("fileName"))
                url = text_value(media.get("url")) or text_value(media.get("previewUrl"))
                line = esc(label.capitalize()) + (": " + esc(filename) if filename else "")
                if url:
                    line += ": " + esc(url)
                if media.get("downloadStatus") == "pending":
                    line += " [media queued; use show-messages for download status]"
                parts.append(line)
            body = "\n".join(parts) or "[Message without supported text or attachment metadata]"
            reply = event.get("reply")
            if reply:
                author = self._name(reply["sender_id"])
                original = reply["text"]
                if not original and reply["message_id"]:
                    row = self.db.execute("SELECT text,sender_id FROM messages WHERE thread_id=? AND message_id=? AND (?='' OR run_id=?)", (event["thread_id"], reply["message_id"], self.run_id, self.run_id)).fetchone()
                    if row and (not self.own_id or row[1] and row[1] != self.own_id):
                        original, cached_id = row
                        author = self._name(reply["sender_id"] or cached_id)
                return f"{who} replied to <b>{esc(author)}</b>: <i>{esc(original or reply['message_id'])}</i>\n<b>Reply</b>: {body}"
            return f"{who}: {body}"
        mid = esc(event["message_id"] or "unknown message")
        if kind == "edit":
            return f"Message {mid} was edited: {esc(event['text'])}"
        if kind == "unsend":
            return f"Message {mid} was deleted/unsent. Captured content is retained in the deleted-message view."
        if kind == "reaction":
            action = "reacted <i>" + esc(event["reaction"]) + "</i> to" if event["reaction"] else "removed a reaction from"
            cached = self.db.execute("SELECT sender_id,text FROM messages WHERE thread_id=? AND message_id=? AND (?='' OR run_id=?)", (event["thread_id"], event["message_id"], self.run_id, self.run_id)).fetchone()
            original = ""
            if cached:
                original = "\n  <b>" + esc(self._name(cached[0])) + "</b>: <i>" + esc(cached[1] or "[attachment]") + "</i>"
            return f"{who} {action} message {mid}.{original}"
        raise StoreFailure("Unsupported normalized event type.")

    def _relative_path(self, tid, received):
        row = self.db.execute("SELECT directory FROM threads WHERE id=?", (tid,)).fetchone()
        directory = row[0] if row and row[0] else None
        if directory is None:
            title = self._thread_name(tid)
            safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", title).strip(" .")[:70] or "UNTITLED"
            suffix = tid if re.fullmatch(r"[0-9]{1,20}", tid) else hashlib.sha256(tid.encode()).hexdigest()[:12]
            directory = safe + "__" + suffix
            self.db.execute("INSERT INTO threads(id,name,directory) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET directory=excluded.directory", (tid, "", directory))
        date = datetime.fromtimestamp(received, self.zone)
        prefix = "runs/" + self.run_id + "/" if self.run_id else ""
        return f"{prefix}{directory}/{date.day}_{date.month}_{date.year}"

    def accept(self, event, *, received=None):
        """Return a safe local-log summary for a new event; never persist raw payloads."""
        received = self.clock() if received is None else received
        with self.lock:
            if event.get("type") == "metadata":
                self._metadata(event.get("data") or {})
                return None
            if event.get("type") not in EVENT_NAMES or not self._future_event(event):
                return None
            data = event.get("data")
            if not isinstance(data, dict):
                return None
            mid = identifier(data.get("id") if EVENT_NAMES[event["type"]] == "message" else data.get("messageId"))
            tid = identifier(data.get("threadId") or data.get("chatJid") or data.get("chat")) or self._resolve_thread(mid) or "unresolved"
            # Retain routing metadata so an excluded conversation can still be
            # selected in settings; never save its message content or media.
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO threads(id,last_seen) VALUES (?,?)", (tid, received))
                title = text_value(data.get("threadName"))
                if title:
                    self.db.execute("UPDATE threads SET name=? WHERE id=?", (title, tid))
            if not self.capture_allowed(tid):
                return None
            if not self._incoming(event, received):
                return None
            with self.db:
                value = self._normalize(event)
                if value is None:
                    return None
                if value["type"] == "unsend" and value["message_id"]:
                    previous = self.db.execute("SELECT deleted FROM messages WHERE thread_id=? AND message_id=?", (value["thread_id"], value["message_id"])).fetchone()
                    if previous and previous[0]:
                        return None
                serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                if value["type"] == "message" and value["message_id"]:
                    identity = "message\0" + value["thread_id"] + "\0" + value["message_id"]
                else:
                    identity = serialized
                key = hashlib.sha256(identity.encode()).hexdigest()
                if self.db.execute("SELECT 1 FROM events WHERE dedup_key=?", (key,)).fetchone():
                    return None
                body = self._body(value)
                date = datetime.fromtimestamp(received, self.zone)
                line = date.strftime("%a %b %d %H:%M:%S %Y") + " | " + body
                relative = self._relative_path(value["thread_id"], received)
                self.db.execute("INSERT INTO events(dedup_key,thread_id,received_at,type,payload,line,path,run_id,message_id) VALUES (?,?,?,?,?,?,?,?,?)", (key, value["thread_id"], received, value["type"], serialized, line, relative, self.run_id, value["message_id"]))
                if value["type"] == "message" and value["message_id"]:
                    if value["thread_id"] != "unresolved":
                        targets = self.db.execute("SELECT thread_id,deleted FROM messages WHERE message_id=?", (value["message_id"],)).fetchall()
                        if targets == [("unresolved", 1)]:
                            self.db.execute("UPDATE messages SET thread_id=? WHERE thread_id='unresolved' AND message_id=?", (value["thread_id"], value["message_id"]))
                    self.db.execute(
                        "INSERT INTO messages(thread_id,message_id,sender_id,text,transport,run_id,created_ms,captured_at,original_text,attachments,reply_json,metadata_json) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(thread_id,message_id) DO UPDATE SET "
                        "sender_id=excluded.sender_id,text=excluded.text,transport=excluded.transport,run_id=excluded.run_id,"
                        "created_ms=excluded.created_ms,captured_at=excluded.captured_at,original_text=excluded.original_text,"
                        "attachments=excluded.attachments,reply_json=excluded.reply_json,metadata_json=excluded.metadata_json",
                        (value["thread_id"], value["message_id"], value["sender_id"], value["text"], value["transport"], self.run_id,
                         value["timestamp_ms"], received, value["text"], json.dumps(value.get("attachments", []), ensure_ascii=False), json.dumps(value.get("reply", {}), ensure_ascii=False), json.dumps(value.get("metadata", {}), ensure_ascii=False)),
                    )
                elif value["type"] == "edit" and value["message_id"]:
                    self.db.execute("UPDATE messages SET text=?,last_edit_ms=? WHERE thread_id=? AND message_id=?", (value["text"], value["timestamp_ms"], value["thread_id"], value["message_id"]))
                elif value["type"] == "unsend" and value["message_id"]:
                    self.db.execute(
                        "INSERT INTO messages(thread_id,message_id,sender_id,text,transport,deleted,deleted_at_ms,deleted_run_id,captured_at) "
                        "VALUES (?,?,?,?,?,1,?,?,?) ON CONFLICT(thread_id,message_id) DO UPDATE SET "
                        "deleted=1,deleted_at_ms=excluded.deleted_at_ms,deleted_run_id=excluded.deleted_run_id",
                        (value["thread_id"], value["message_id"], value["sender_id"], "", value["transport"], value["timestamp_ms"], self.run_id, received),
                    )
                self.db.execute("UPDATE threads SET last_seen=? WHERE id=?", (received, value["thread_id"]))
                self.db.execute("UPDATE contacts SET last_seen=? WHERE id=?", (received, value["sender_id"]))
                summary = {"type": value["type"], "thread": self._thread_name(value["thread_id"]), "body": body, "received_at": received,
                           "thread_id": value["thread_id"], "message_id": value["message_id"], "sender_id": value["sender_id"]}
                born = self.db.execute("SELECT captured_at FROM messages WHERE thread_id=? AND message_id=?", (value["thread_id"], value["message_id"])).fetchone()
                summary["expires_at"] = min(received, born[0] if born and born[0] else received) + (self.retention_seconds or 259200)
                if value["type"] in {"edit", "reaction"}:
                    summary["telegram"] = self._telegram_event(value)
            self._export_pending()
            if value["type"] == "message":
                pending = self.pending_deletions.pop((value["thread_id"], value["message_id"]), None)
                if pending is None and self._resolve_thread(value["message_id"]) == value["thread_id"]:
                    pending = self.pending_deletions.pop(("unresolved", value["message_id"]), None)
                if pending:
                    pending[1]["data"]["threadId"] = value["thread_id"]
                    following = self.accept(pending[1], received=pending[0])
                    if following:
                        summary["following"] = following
            return summary

    def update_media(self, thread_id, message_id, index, result):
        """Update local download state without changing the append-only chat journal."""
        allowed = {"downloadStatus", "downloadError", "localPath", "savedBytes", "sha256"}
        safe = {key: value for key, value in result.items() if key in allowed}
        with self.lock, self.db:
            row = self.db.execute("SELECT attachments,captured_at,sender_id FROM messages WHERE thread_id=? AND message_id=? AND run_id=?",
                                  (thread_id, message_id, self.run_id)).fetchone()
            if row is None or self.own_id and (not row[2] or row[2] == self.own_id):
                return False
            if self.retention_seconds is not None and row[1] <= self.clock() - self.retention_seconds:
                return False
            attachments = json.loads(row[0])
            if not 0 <= index < len(attachments):
                return False
            attachments[index].update(safe)
            self.db.execute("UPDATE messages SET attachments=? WHERE thread_id=? AND message_id=?",
                            (json.dumps(attachments, ensure_ascii=False), thread_id, message_id))
            return True

    def media_available(self, thread_id, message_id):
        with self.lock:
            row = self.db.execute("SELECT captured_at,sender_id FROM messages WHERE thread_id=? AND message_id=? AND run_id=?", (thread_id, message_id, self.run_id)).fetchone()
            return row is not None and (not self.own_id or bool(row[1]) and row[1] != self.own_id) and (self.retention_seconds is None or row[0] > self.clock() - self.retention_seconds)

    def cleanup_expired(self):
        if self.retention_seconds is None:
            return {"messages": 0, "events": 0, "files": 0}
        from retention import expire
        return expire(self)

    def relay_snapshot(self, thread_id, message_id):
        """Read captured deletion content, never provider payloads or device state."""
        columns = ("sender_id", "text", "original_text", "created_ms", "captured_at", "deleted_at_ms", "last_edit_ms",
                   "deleted", "attachments", "reply_json", "metadata_json", "deleted_run_id", "run_id")
        with self.lock:
            row = self.db.execute("SELECT " + ",".join(columns) + " FROM messages WHERE thread_id=? AND message_id=?",
                                  (thread_id, message_id)).fetchone()
            if row is None:
                return None
            item = dict(zip(columns, row))
            item["thread_id"] = thread_id
            if self.own_id and (not item["sender_id"] or item["sender_id"] == self.own_id):
                return None
            if self.retention_seconds is not None:
                birth = item["captured_at"] or item["created_ms"] / 1000 or (item["deleted_at_ms"] or 0) / 1000
                if birth <= self.clock() - self.retention_seconds:
                    return None
            item["sender"] = self._name(item["sender_id"])
            item["sender_kind"] = self._name_kind(item["sender_id"])
            item["conversation_kind"] = self._thread_kind(thread_id)
            item["conversation"] = self._thread_name(thread_id)
            item["attachments"] = json.loads(item["attachments"])
            item["reply"] = self._safe_reply(json.loads(item.pop("reply_json")), thread_id)
            if item["reply"]:
                item["reply"]["sender_name"] = self._name(item["reply"].get("sender_id"))
                item["reply"]["sender_name_kind"] = self._name_kind(item["reply"].get("sender_id"))
            item["metadata"] = json.loads(item.pop("metadata_json"))
            for person in item["metadata"].get("mentions", []):
                person["name"] = self._name(person.get("user_id"))
                person["name_kind"] = self._name_kind(person.get("user_id"))
            return item

    def contact_needed(self, user_id):
        if not re.fullmatch(r"[0-9]{1,19}", user_id) or not 0 < int(user_id) <= 9223372036854775807 or user_id == self.own_id:
            return False
        with self.lock:
            row = self.db.execute("SELECT name,username FROM contacts WHERE id=?", (user_id,)).fetchone()
            return row is None or not display_label(row[0])

    def conversation_catalog(self, after=""):
        with self.lock:
            ids = [row[0] for row in self.db.execute("SELECT id FROM threads WHERE id>? ORDER BY id LIMIT 50", (after,))]
            return [{"id": tid, "name": "" if self._thread_kind(tid) else ''.join(c if c.isprintable() else ' ' for c in self._thread_name(tid))[:120]} for tid in ids]

    def claim_telegram(self, thread_id, message_id, part):
        # Claim before the network call: a timeout might still have delivered.
        # Never replay ambiguous attempts or scan archived deletions at startup.
        with self.lock, self.db:
            cursor = self.db.execute("INSERT OR IGNORE INTO telegram_attempts VALUES (?,?,?,?, 'attempting')",
                                     (self.run_id, thread_id, message_id, part))
            return cursor.rowcount == 1

    def finish_telegram(self, thread_id, message_id, part, status):
        if status not in {"sent", "failed", "skipped"}:
            raise StoreFailure("Invalid Telegram attempt state.")
        with self.lock, self.db:
            self.db.execute("UPDATE telegram_attempts SET status=? WHERE run_id=? AND thread_id=? AND message_id=? AND part=?",
                            (status, self.run_id, thread_id, message_id, part))

    def telegram_topic(self, bot_id, chat_id, thread_id):
        with self.lock:
            row = self.db.execute("SELECT topic_id,name,status,is_fallback FROM telegram_topics WHERE bot_id=? AND chat_id=? AND thread_id=?",
                                  (bot_id, chat_id, thread_id)).fetchone()
            return dict(zip(("topic_id", "name", "status", "is_fallback"), row)) if row else None

    def reserve_telegram_topic(self, bot_id, chat_id, thread_id, name, is_fallback):
        with self.lock, self.db:
            cursor = self.db.execute("INSERT OR IGNORE INTO telegram_topics(bot_id,chat_id,thread_id,name,status,is_fallback) VALUES (?,?,?,?, 'creating',?)",
                                     (bot_id, chat_id, thread_id, name, int(is_fallback)))
            return cursor.rowcount == 1

    def save_telegram_topic(self, bot_id, chat_id, thread_id, topic_id, name, is_fallback=False, *, status="ready"):
        if not isinstance(topic_id, int) or isinstance(topic_id, bool) or topic_id <= 0:
            raise StoreFailure("Invalid Telegram topic identifier.")
        if status not in {"ready", "repair_pending"}:
            raise StoreFailure("Invalid Telegram topic state.")
        with self.lock, self.db:
            self.db.execute("INSERT INTO telegram_topics VALUES (?,?,?,?,?,?,?) ON CONFLICT(bot_id,chat_id,thread_id) DO UPDATE SET topic_id=excluded.topic_id,name=excluded.name,status=excluded.status,is_fallback=excluded.is_fallback",
                            (bot_id, chat_id, thread_id, topic_id, name, status, int(is_fallback)))

    def begin_telegram_topic_repair(self, bot_id, chat_id, thread_id, topic_id):
        with self.lock, self.db:
            cursor = self.db.execute("UPDATE telegram_topics SET topic_id=NULL,status='creating' WHERE bot_id=? AND chat_id=? AND thread_id=? AND topic_id=? AND status='ready'",
                                     (bot_id, chat_id, thread_id, topic_id))
            return cursor.rowcount == 1

    def finish_telegram_topic_repair(self, bot_id, chat_id, thread_id, topic_id, *, missing=False):
        with self.lock, self.db:
            self.db.execute("UPDATE telegram_topics SET status=? WHERE bot_id=? AND chat_id=? AND thread_id=? AND topic_id=? AND status='repair_pending'",
                            ("missing" if missing else "ready", bot_id, chat_id, thread_id, topic_id))

    def cancel_telegram_topic(self, bot_id, chat_id, thread_id):
        with self.lock, self.db:
            # Only a definitive API rejection is safe to retry automatically.
            self.db.execute("DELETE FROM telegram_topics WHERE bot_id=? AND chat_id=? AND thread_id=? AND status='creating'",
                            (bot_id, chat_id, thread_id))

    def unconfirmed_telegram_topic(self, bot_id, chat_id, thread_id):
        with self.lock, self.db:
            self.db.execute("UPDATE telegram_topics SET status='unconfirmed' WHERE bot_id=? AND chat_id=? AND thread_id=? AND status='creating'",
                            (bot_id, chat_id, thread_id))

    def telegram_topic_names(self, bot_id, chat_id):
        with self.lock:
            return {row[0] for row in self.db.execute("SELECT name FROM telegram_topics WHERE bot_id=? AND chat_id=?", (bot_id, chat_id))}

    def counts(self):
        with self.lock:
            if self.run_id:
                return dict(self.db.execute("SELECT type,COUNT(*) FROM events WHERE run_id=? GROUP BY type", (self.run_id,)).fetchall())
            return dict(self.db.execute("SELECT type,COUNT(*) FROM events GROUP BY type").fetchall())

    def dashboard_stats(self):
        """Owned retained-content counts only; no bodies, names, IDs or URLs."""
        result = dict(messages=0, deletions=0, attachments=0, files=0, media_bytes=0, conversations=0)
        with self.lock:
            cutoff = self.clock() - (self.retention_seconds or 259200)
            chats = set()
            for tid, deleted, attachments in self.db.execute("SELECT thread_id,deleted,attachments FROM messages WHERE captured_at>? AND sender_id!='' AND sender_id!=?", (cutoff, self.own_id)):
                result["messages"] += 1
                result["deletions"] += int(bool(deleted))
                chats.add(tid)
                values = json.loads(attachments or "[]")
                result["attachments"] += len(values)
                for item in values:
                    if item.get("downloadStatus") == "saved":
                        result["files"] += 1
                        result["media_bytes"] += max(0, number(item.get("savedBytes")))
            result["conversations"] = len(chats)
        return result
