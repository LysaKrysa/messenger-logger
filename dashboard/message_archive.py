"""Encrypted, expiring message copies for the authenticated owner's viewer."""
from datetime import datetime, timedelta
import json
import math
import re
from zoneinfo import ZoneInfo

from .store import DashboardError
from .conversations import Conversations

RETENTION = 259200
MEDIA_KEYS = {"type", "fileName", "mimeType", "downloadStatus", "viewOnce"}
FIELDS = {"thread_id", "message_id", "sender", "text", "original_text", "created_ms", "captured_at",
          "deleted", "deleted_at_ms", "last_edit_ms", "conversation", "attachments", "preview_truncated"}


def bound(db, row, *, worker=False):
    current = db.execute("SELECT * FROM accounts WHERE id=?", (row["id"],)).fetchone()
    keys = ("pair_epoch", "chat_id", "facebook_id") + (("revision", "auth_epoch") if worker else ())
    if not current or not current["chat_id"] or any(current[key] != row[key] for key in keys) or worker and not current["desired"]:
        raise DashboardError("Account pairing changed.")


class MessageArchive:
    def __init__(self, store):
        self.store = store
        self.conversations = Conversations(store)
        with store.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS message_archive(
                id TEXT PRIMARY KEY,account_id TEXT NOT NULL,pair_epoch INTEGER NOT NULL,
                conversation_id TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,
                deleted INTEGER NOT NULL,types TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS archive_owner ON message_archive(account_id,pair_epoch,conversation_id,created DESC,id);
                CREATE INDEX IF NOT EXISTS archive_expiry ON message_archive(expires);''')

    def cleanup(self):
        with self.store.db() as db:
            db.execute("DELETE FROM message_archive WHERE expires<=? OR NOT EXISTS (SELECT 1 FROM accounts a WHERE a.id=message_archive.account_id AND a.pair_epoch=message_archive.pair_epoch AND a.chat_id IS NOT NULL)", (self.store.clock(),))

    def report(self, row, data):
        now = self.store.clock()
        if (not isinstance(data, dict) or set(data) != {"revision", "messages"} or type(data["revision"]) is not int
                or data["revision"] != row["revision"] or not isinstance(data["messages"], list) or len(data["messages"]) > 20):
            raise DashboardError("Invalid message report.")
        for item in data["messages"]:
            if not isinstance(item, dict) or set(item) != FIELDS:
                raise DashboardError("Invalid message record.")
            for key in ("thread_id", "message_id", "sender", "text", "conversation"):
                if not isinstance(item[key], str) or len(item[key]) > (300000 if key == "text" else 500):
                    raise DashboardError("Invalid message field.")
            if (not item["thread_id"] or not item["message_id"] or type(item["deleted"]) is not bool or type(item["preview_truncated"]) is not bool
                    or item["original_text"] is not None and (not isinstance(item["original_text"], str) or len(item["original_text"]) > 300000)):
                raise DashboardError("Invalid message content.")
            for key in ("captured_at", "created_ms", "deleted_at_ms", "last_edit_ms"):
                value = item[key]
                if value is not None and (type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= (now + 300) * (1 if key == "captured_at" else 1000)):
                    raise DashboardError("Invalid message time.")
            if not item["captured_at"] or not item["created_ms"]:
                raise DashboardError("Missing message time.")
            if not isinstance(item["attachments"], list) or len(item["attachments"]) > 100:
                raise DashboardError("Invalid attachment list.")
            for media in item["attachments"]:
                if (not isinstance(media, dict) or not set(media) <= MEDIA_KEYS
                        or any(type(value) is not bool if key == "viewOnce" else not isinstance(value, str) or len(value) > 500 for key, value in media.items())):
                    raise DashboardError("Invalid attachment metadata.")
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            bound(db, row, worker=True)
            since = db.execute("SELECT value FROM settings WHERE key=?", ("archive_since:" + row["id"],)).fetchone()
            since = json.loads(since[0]) if since else 0
            for item in data["messages"]:
                born = min(item["captured_at"], item["created_ms"] / 1000)
                if born + RETENTION <= now or born < since:
                    continue
                self.conversations._upsert(db, row, item["thread_id"], item["conversation"][:120])
                conversation = db.execute("SELECT id FROM conversation_catalog WHERE account_id=? AND pair_epoch=? AND thread_id=?", (row["id"], row["pair_epoch"], item["thread_id"])).fetchone()[0]
                ident = self.store.secret("archive:" + row["id"] + ":" + str(row["pair_epoch"]) + ":" + item["thread_id"] + "\0" + item["message_id"])
                previous = db.execute("SELECT payload FROM message_archive WHERE id=?", (ident,)).fetchone()
                if previous:
                    old = self.store.unseal("archive:" + ident, previous[0])
                    item = dict(item)
                    if old["deleted"]:
                        item.update(deleted=True, deleted_at_ms=old["deleted_at_ms"])
                    if (old["last_edit_ms"] or 0) > (item["last_edit_ms"] or 0):
                        item.update(text=old["text"], last_edit_ms=old["last_edit_ms"])
                payload = self.store.seal("archive:" + ident, item)
                types = "|" + "|".join(sorted({media.get("type", "attachment") for media in item["attachments"]})) + "|"
                db.execute("INSERT INTO message_archive VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,deleted=excluded.deleted,types=excluded.types,expires=MIN(message_archive.expires,excluded.expires)",
                           (ident, row["id"], row["pair_epoch"], conversation, item["created_ms"] / 1000, born + RETENTION, int(item["deleted"]), types, payload))
            db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("archive_sync:" + row["id"], json.dumps({"updated": now, "pair_epoch": row["pair_epoch"]})))
            db.execute("DELETE FROM message_archive WHERE expires<=?", (now,))

    def conversations_for(self, row, query=""):
        if len(query) > 100:
            raise DashboardError("Conversation search is too long.")
        with self.store.db() as db:
            bound(db, row)
            values = db.execute("SELECT c.id,c.name,COUNT(*) AS messages,MAX(m.created) AS latest FROM message_archive m JOIN conversation_catalog c ON c.id=m.conversation_id WHERE m.account_id=? AND m.pair_epoch=? AND m.expires>? GROUP BY c.id ORDER BY latest DESC LIMIT 5000", (row["id"], row["pair_epoch"], self.store.clock())).fetchall()
        return [dict(value) for value in values if query.casefold() in value["name"].casefold()]

    def page(self, row, conversation, filters):
        if not isinstance(conversation, str) or not re.fullmatch(r"[a-f0-9]{32}", conversation):
            raise DashboardError("Conversation unavailable.")
        status, kind = filters.get("status", "all"), filters.get("kind", "all")
        if status not in {"all", "deleted", "undeleted"} or kind not in {"all", "text", "image", "video", "voice", "audio", "file"}:
            raise DashboardError("Invalid message filter.")
        query, sender = filters.get("q", ""), filters.get("sender", "")
        if len(query) > 200 or len(sender) > 100 or not re.fullmatch(r"[0-9]{1,7}", filters.get("offset", "0")):
            raise DashboardError("Invalid message search.")
        offset = int(filters.get("offset", "0"))
        conditions, params = ["account_id=?", "pair_epoch=?", "conversation_id=?", "expires> ?"], [row["id"], row["pair_epoch"], conversation, self.store.clock()]
        if status != "all":
            conditions.append("deleted=?")
            params.append(int(status == "deleted"))
        if kind != "all":
            conditions.append("types='||'" if kind == "text" else "types LIKE ?")
            if kind != "text":
                params.append("%|" + kind + "|%")
        for key, operator in (("from", ">="), ("to", "<")):
            if filters.get(key):
                try:
                    date = datetime.strptime(filters[key], "%Y-%m-%d")
                    if key == "to":
                        date += timedelta(days=1)
                    date = date.replace(tzinfo=ZoneInfo("Europe/Warsaw")).timestamp()
                except (ValueError, OverflowError):
                    raise DashboardError("Invalid search date.") from None
                conditions.append("created" + operator + "?")
                params.append(date)
        with self.store.db() as db:
            bound(db, row)
            if not db.execute("SELECT 1 FROM conversation_catalog WHERE id=? AND account_id=? AND pair_epoch=?", (conversation, row["id"], row["pair_epoch"])).fetchone():
                raise DashboardError("Conversation unavailable.")
            rows = db.execute("SELECT id,payload FROM message_archive WHERE " + " AND ".join(conditions) + " ORDER BY created DESC,id LIMIT 5001 OFFSET ?", (*params, offset))
            values, scanned = [], 0
            for value in rows:
                if scanned == 5000:
                    return values, offset + scanned
                scanned += 1
                item = self.store.unseal("archive:" + value["id"], value["payload"])
                haystack = " ".join([item["text"], item["original_text"] or ""] + [media.get("fileName", "") for media in item["attachments"]])
                if query.casefold() not in haystack.casefold() or sender.casefold() not in item["sender"].casefold():
                    continue
                values.append(item)
                if len(values) == 30:
                    return values, offset + scanned if rows.fetchone() else None
            return values, None
