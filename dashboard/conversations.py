"""Owned conversation metadata and independent capture/forwarding settings."""
import json
import uuid

from conversation_filters import DEFAULT, FORWARD, capture, forwarding, thread_key
from .store import DashboardError


class Conversations:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS conversation_catalog(
                id TEXT PRIMARY KEY,account_id TEXT NOT NULL,pair_epoch INTEGER NOT NULL,
                thread_id TEXT NOT NULL,name TEXT NOT NULL,seen REAL NOT NULL,telegram_topic INTEGER,
                UNIQUE(account_id,pair_epoch,thread_id));
                CREATE INDEX IF NOT EXISTS conversation_owner ON conversation_catalog(account_id,pair_epoch,seen);''')

    def report(self, row, data):
        if (not isinstance(data, dict) or set(data) != {"revision", "conversations"}
                or type(data["revision"]) is not int or data["revision"] != row["revision"]
                or not isinstance(data["conversations"], list) or len(data["conversations"]) > 50):
            raise DashboardError("Invalid conversation report.")
        for value in data["conversations"]:
            if (not isinstance(value, dict) or set(value) != {"id", "name"} or not thread_key(value["id"])
                    or not isinstance(value["name"], str) or len(value["name"]) > 120
                    or any(not char.isprintable() for char in value["name"])):
                raise DashboardError("Invalid conversation metadata.")
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT * FROM accounts WHERE id=?", (row["id"],)).fetchone()
            if not current or any(current[key] != row[key] for key in ("pair_epoch", "revision", "auth_epoch", "chat_id")):
                raise DashboardError("Account settings changed.")
            for value in data["conversations"]:
                self._upsert(db, row, value["id"], value["name"])
            db.execute("DELETE FROM conversation_catalog WHERE account_id=? AND id NOT IN (SELECT id FROM conversation_catalog WHERE account_id=? ORDER BY seen DESC LIMIT 5000)", (row["id"], row["id"]))

    def _upsert(self, db, row, tid, name):
        db.execute("INSERT INTO conversation_catalog(id,account_id,pair_epoch,thread_id,name,seen) VALUES (?,?,?,?,?,?) ON CONFLICT(account_id,pair_epoch,thread_id) DO UPDATE SET name=CASE WHEN excluded.name='' THEN conversation_catalog.name ELSE excluded.name END,seen=excluded.seen",
                   (uuid.uuid4().hex, row["id"], row["pair_epoch"], tid, name, self.store.clock()))

    def remember(self, row, tid, name):
        if not thread_key(tid):
            return
        with self.store.db() as db:
            self._upsert(db, row, tid, name[:120])

    def rows(self, row, offset=0, limit=20):
        if type(offset) is not int or not 0 <= offset <= 5000:
            raise DashboardError("Invalid page.")
        with self.store.db() as db:
            values = [dict(value) for value in db.execute("SELECT * FROM conversation_catalog WHERE account_id=? AND pair_epoch=? ORDER BY name COLLATE NOCASE,thread_id LIMIT ? OFFSET ?", (row["id"], row["pair_epoch"], limit, offset))]
        policy = self.store.filters(row["id"])
        for value in values:
            rule = policy["rules"].get(value["thread_id"], {"capture": None, "forward": "inherit"})
            value.update(capture=rule["capture"], forward=rule["forward"], capture_enabled=capture(policy, value["thread_id"]),
                         forward_mode=forwarding(policy, value["thread_id"], row["mode"]))
        return values

    def get(self, row, public_id):
        with self.store.db() as db:
            value = db.execute("SELECT * FROM conversation_catalog WHERE id=? AND account_id=? AND pair_epoch=?", (public_id, row["id"], row["pair_epoch"])).fetchone()
        return dict(value) if value else None

    def update(self, row, public_id=None, *, include=None, mode="inherit", default=None):
        if public_id is None and type(default) is not bool or public_id is not None and (include is not None and type(include) is not bool or not isinstance(mode, str) or mode not in FORWARD):
            raise DashboardError("Invalid conversation settings.")
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT * FROM accounts WHERE id=?", (row["id"],)).fetchone()
            if not current or current["pair_epoch"] != row["pair_epoch"] or current["chat_id"] != row["chat_id"]:
                raise DashboardError("Account pairing changed.")
            raw = db.execute("SELECT value FROM settings WHERE key=?", ("filters:" + row["id"],)).fetchone()
            value = json.loads(raw[0]) if raw else json.loads(json.dumps(DEFAULT))
            if public_id is None:
                value["capture_default"] = default
            else:
                item = db.execute("SELECT thread_id FROM conversation_catalog WHERE id=? AND account_id=? AND pair_epoch=?", (public_id, row["id"], row["pair_epoch"])).fetchone()
                if item is None:
                    raise DashboardError("Conversation is unavailable.")
                tid = item["thread_id"]
                if include is None and mode == "inherit":
                    value["rules"].pop(tid, None)
                else:
                    if tid not in value["rules"] and len(value["rules"]) >= 200:
                        raise DashboardError("At most 200 explicit conversation rules are supported.")
                    previous = value["rules"].get(tid, {})
                    since = previous.get("since", 0) if previous.get("forward") == mode else self.store.clock()
                    value["rules"][tid] = {"capture": include, "forward": mode, "since": since}
            db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("filters:" + row["id"], json.dumps(value)))

    def topic(self, row, tid, topic=None):
        with self.store.db() as db:
            if type(topic) is int and 0 < topic < 2**53:
                db.execute("UPDATE conversation_catalog SET telegram_topic=? WHERE account_id=? AND pair_epoch=? AND thread_id=?", (topic, row["id"], row["pair_epoch"], tid))
            found = db.execute("SELECT telegram_topic FROM conversation_catalog WHERE account_id=? AND pair_epoch=? AND thread_id=?", (row["id"], row["pair_epoch"], tid)).fetchone()
        return found[0] if found else None
