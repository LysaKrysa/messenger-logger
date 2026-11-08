"""Owned delivery history, bounded encrypted payloads and explicit one-shot resends."""
import base64
import json
import math
import os
import re
import secrets
import time
import uuid

from conversation_filters import capture, configured, forwarding, thread_key
from telegram_relay import _rejection_reason
from .conversations import Conversations
from .delivery_cache import DeliveryCache, PayloadUnavailable
from .store import DashboardError

RETENTION = 259200
KINDS = {"message", "deleted", "edit", "reaction", "view_once", "notification"}


class Deliveries:
    def __init__(self, store, api):
        self.store, self.api = store, api
        self.conversations = Conversations(store)
        self.cache = DeliveryCache(store)
        self.last_cleanup = 0
        self.account_limit = self._limit("DASHBOARD_RESEND_CACHE_MB", 100)
        self.total_limit = self._limit("DASHBOARD_RESEND_TOTAL_MB", 500)
        with store.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS delivery_history(
                id TEXT PRIMARY KEY, account_id TEXT NOT NULL,pair_epoch INTEGER NOT NULL,
                facebook_id TEXT NOT NULL,chat_id INTEGER NOT NULL,group_key TEXT NOT NULL,
                thread_id TEXT NOT NULL,name TEXT NOT NULL,kind TEXT NOT NULL,
                created REAL NOT NULL,updated REAL NOT NULL,expires REAL NOT NULL,
                outcome TEXT NOT NULL,reason TEXT NOT NULL DEFAULT '',attempts INTEGER NOT NULL DEFAULT 1,
                cache_size INTEGER NOT NULL DEFAULT 0,cache_ref TEXT,cache_reason TEXT NOT NULL DEFAULT 'unavailable',retry_of TEXT,
                UNIQUE(account_id,pair_epoch,group_key));
                CREATE INDEX IF NOT EXISTS delivery_owner ON delivery_history(account_id,pair_epoch,created);
                CREATE TABLE IF NOT EXISTS resend_intents(
                    id TEXT PRIMARY KEY,account_id TEXT NOT NULL,pair_epoch INTEGER NOT NULL,
                    delivery_id TEXT NOT NULL,expires REAL NOT NULL,used REAL);''')
        self.cleanup(force=True)

    @staticmethod
    def _limit(name, default):
        raw = os.environ.get(name, str(default))
        if not re.fullmatch(r"[1-9][0-9]{0,3}", raw) or int(raw) > 2048:
            raise DashboardError("Invalid resend cache limit.")
        return int(raw) * 1024 * 1024

    def context(self, encoded):
        now = self.store.clock()
        if not encoded:
            value = {}
        else:
            try:
                if len(encoded) > 4096 or not re.fullmatch(r"[A-Za-z0-9_=-]+", encoded):
                    raise ValueError
                value = json.loads(base64.urlsafe_b64decode(encoded))
                if not isinstance(value, dict) or set(value) - {"group", "thread", "name", "kind", "expires"}:
                    raise ValueError
            except (ValueError, TypeError):
                raise DashboardError("Invalid delivery context.") from None
        result = dict(group=value.get("group", uuid.uuid4().hex), thread=value.get("thread", ""), name=value.get("name", ""),
                      kind=value.get("kind", "notification"), expires=value.get("expires", now + RETENTION))
        if (not isinstance(result["group"], str) or not re.fullmatch(r"[a-f0-9]{32}", result["group"])
                or result["thread"] != "" and not thread_key(result["thread"])
                or not isinstance(result["name"], str) or len(result["name"]) > 120
                or any(not c.isprintable() for c in result["name"])
                or not isinstance(result["kind"], str) or result["kind"] not in KINDS
                or type(result["expires"]) not in {int, float} or not math.isfinite(result["expires"])):
            raise DashboardError("Invalid delivery context.")
        result["expires"] = min(result["expires"], now + RETENTION)
        return result

    def allowed(self, row, tid, kind):
        policy = self.store.filters(row["id"])
        if not tid:
            return not configured(policy)
        mode = forwarding(policy, tid, row["mode"])
        if mode == "off":
            return False
        return (mode == "all" or kind in {"deleted", "notification"}
                or kind == "view_once" and row["view_once"] and policy["rules"].get(tid, {}).get("forward", "inherit") == "inherit")

    @staticmethod
    def _current(db, row):
        value = db.execute("SELECT * FROM accounts WHERE id=?", (row["id"],)).fetchone()
        if not value or not value["chat_id"] or any(value[key] != row[key] for key in ("pair_epoch", "chat_id", "facebook_id")):
            raise DashboardError("Account pairing changed.")
        return dict(value)

    def begin(self, row, method, context):
        if method not in {"sendMessage", "sendDocument"} or context["expires"] <= self.store.clock():
            raise DashboardError("Delivery content has expired.")
        self.conversations.remember(row, context["thread"], context["name"])
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._current(db, row)
            if not current["desired"] or current["auth_epoch"] != row["auth_epoch"]:
                raise DashboardError("Worker settings changed.")
            previous = db.execute("SELECT * FROM delivery_history WHERE account_id=? AND pair_epoch=? AND group_key=?", (row["id"], row["pair_epoch"], context["group"])).fetchone()
            if previous:
                # Only the worker's definite missing-topic repair may continue
                # an existing send. Sent/uncertain attempts are never replayed.
                if previous["outcome"] != "failed" or previous["reason"] != "topic_missing" or previous["attempts"] >= 2:
                    raise DashboardError("This delivery was already attempted.")
                db.execute("UPDATE delivery_history SET outcome='sending',reason='',attempts=attempts+1,updated=? WHERE id=?", (self.store.clock(), previous["id"]))
                return previous["id"]
            ident = uuid.uuid4().hex
            db.execute("INSERT INTO delivery_history(id,account_id,pair_epoch,facebook_id,chat_id,group_key,thread_id,name,kind,created,updated,expires,outcome) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'sending')",
                       (ident, row["id"], row["pair_epoch"], row["facebook_id"], row["chat_id"], context["group"], context["thread"], context["name"], context["kind"], self.store.clock(), self.store.clock(), context["expires"]))
            return ident

    def save(self, row, ident, method, fields, source=None, filename=None, size=0):
        metadata = {"version": 1, "method": method, "fields": fields, "filename": filename, "file_size": size}
        estimate = int(size * 1.4) + len(json.dumps(metadata).encode()) * 2 + 4096
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            item = db.execute("SELECT cache_size,cache_reason FROM delivery_history WHERE id=? AND account_id=? AND pair_epoch=?", (ident, row["id"], row["pair_epoch"])).fetchone()
            if not item:
                return
            own = db.execute("SELECT COALESCE(SUM(cache_size),0) FROM delivery_history WHERE account_id=?", (row["id"],)).fetchone()[0] - item[0]
            total = db.execute("SELECT COALESCE(SUM(cache_size),0) FROM delivery_history").fetchone()[0] - item[0]
            if own + estimate > self.account_limit or total + estimate > self.total_limit:
                if not item[0]:
                    db.execute("UPDATE delivery_history SET cache_reason='quota' WHERE id=?", (ident,))
                return
            db.execute("UPDATE delivery_history SET cache_size=? WHERE id=?", (estimate, ident))
        try:
            actual = self.cache.write(row["id"], ident, metadata, source)
        except PayloadUnavailable:
            with self.store.db() as db:
                db.execute("UPDATE delivery_history SET cache_size=?,cache_reason=? WHERE id=?", (item[0], item[1], ident))
        else:
            with self.store.db() as db:
                db.execute("UPDATE delivery_history SET cache_size=?,cache_reason='ready' WHERE id=?", (actual, ident))

    def finish(self, row, ident, status=None, payload=None, *, fields=None):
        ok = type(status) is int and 200 <= status < 300 and isinstance(payload, dict) and payload.get("ok") is True
        code = payload.get("error_code") if isinstance(payload, dict) else None
        rejected = not ok and (type(status) is int and 400 <= status < 500 or type(code) is int and 400 <= code < 500)
        outcome = "sent" if ok else "failed" if rejected else "uncertain"
        reason = "" if ok else _rejection_reason(code if type(code) is int else status, payload) if rejected else "unconfirmed"
        with self.store.db() as db:
            db.execute("UPDATE delivery_history SET outcome=?,reason=?,updated=? WHERE id=? AND account_id=?", (outcome, reason, self.store.clock(), ident, row["id"]))
            item = db.execute("SELECT thread_id,cache_ref FROM delivery_history WHERE id=?", (ident,)).fetchone()
        self.store.record_delivery(row["id"], {"sent": "sent", "failed": "rejected", "uncertain": "unconfirmed"}[outcome])
        if ok and fields and item:
            topic = fields.get("message_thread_id")
            try: topic = int(topic) if type(topic) in {int, str} else None
            except ValueError: topic = None
            self.conversations.topic(row, item["thread_id"], topic)
            result = payload.get("result")
            document = result.get("document") if isinstance(result, dict) else None
            file_id = document.get("file_id") if isinstance(document, dict) else None
            if isinstance(file_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{16,1024}", file_id):
                compact = dict(fields, document=file_id)
                try:
                    for key in ("caption_entities", "reply_markup", "disable_notification", "disable_content_type_detection"):
                        if isinstance(compact.get(key), str):
                            compact[key] = json.loads(compact[key])
                    self.save(row, item["cache_ref"] or ident, "sendDocument", compact)
                except (DashboardError, ValueError, OSError):
                    pass  # Optional compaction cannot change a confirmed send.
        return outcome

    def cleanup(self, *, force=False):
        now = self.store.clock()
        if not force and now - self.last_cleanup < 30:
            return
        self.last_cleanup = now
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE delivery_history SET outcome='uncertain',reason='unconfirmed' WHERE outcome='sending' AND updated<?", (now - 120,))
            db.execute("DELETE FROM delivery_history WHERE expires<=? OR NOT EXISTS (SELECT 1 FROM accounts a WHERE a.id=delivery_history.account_id AND a.pair_epoch=delivery_history.pair_epoch AND a.facebook_id=delivery_history.facebook_id AND a.chat_id=delivery_history.chat_id)", (now,))
            for value in db.execute("SELECT DISTINCT account_id FROM delivery_history").fetchall():
                db.execute("DELETE FROM delivery_history WHERE account_id=? AND id NOT IN (SELECT id FROM delivery_history WHERE account_id=? ORDER BY created DESC LIMIT 5000)", (value[0], value[0]))
            db.execute("DELETE FROM resend_intents WHERE expires<=? OR NOT EXISTS (SELECT 1 FROM delivery_history h WHERE h.id=resend_intents.delivery_id)", (now,))
            keep = {row[0] for row in db.execute("SELECT id FROM delivery_history WHERE cache_ref IS NULL")}
        for path in self.cache.root.iterdir():
            if re.fullmatch(r"[a-f0-9]{32}\.cache", path.name) and path.stem not in keep:
                path.unlink(missing_ok=True)
            elif re.fullmatch(r"\.[a-f0-9]{32}\.[A-Za-z0-9_-]+\.tmp", path.name) and path.stat().st_mtime < time.time() - 300:
                path.unlink(missing_ok=True)

    def get(self, row, ident):
        self.cleanup()
        with self.store.db() as db:
            value = db.execute("SELECT * FROM delivery_history WHERE id=? AND account_id=? AND pair_epoch=? AND facebook_id=? AND chat_id=? AND expires>?", (ident, row["id"], row["pair_epoch"], row["facebook_id"], row["chat_id"], self.store.clock())).fetchone()
            if not value:
                return None
            value = dict(value)
            source = db.execute("SELECT * FROM delivery_history WHERE id=? AND account_id=? AND pair_epoch=? AND expires>?", (value["cache_ref"] or ident, row["id"], row["pair_epoch"], self.store.clock())).fetchone()
        path = self.cache.path(value["cache_ref"] or ident)
        value["available"] = bool(source and source["cache_size"] > 0 and path.is_file() and not path.is_symlink()
                                  and value["outcome"] != "sending" and self.allowed(row, value["thread_id"], value["kind"]))
        value["cache_reason"] = source["cache_reason"] if source else "unavailable"
        return value

    def rows(self, row, offset=0, limit=10):
        self.cleanup()
        if type(offset) is not int or not 0 <= offset <= 5000:
            raise DashboardError("Invalid page.")
        with self.store.db() as db:
            ids = [value[0] for value in db.execute("SELECT id FROM delivery_history WHERE account_id=? AND pair_epoch=? AND expires>? ORDER BY created DESC,rowid DESC LIMIT ? OFFSET ?", (row["id"], row["pair_epoch"], self.store.clock(), limit, offset))]
        return [value for ident in ids if (value := self.get(row, ident)) is not None]

    def intent(self, row, ident):
        value = self.get(row, ident)
        if not value or not value["available"]:
            raise PayloadUnavailable("This delivery cannot be resent under the current settings or retention limits.")
        token = uuid.uuid4().hex
        with self.store.db() as db:
            self._current(db, row)
            db.execute("INSERT INTO resend_intents VALUES (?,?,?,?,?,NULL)", (token, row["id"], row["pair_epoch"], ident, min(self.store.clock() + 600, value["expires"])))
        return token

    def resend(self, row, token, *, acknowledged=False, expected_delivery=None):
        if acknowledged is not True:
            raise DashboardError("Confirm the possible duplicate before resending.")
        if not self.store.take_limit("resend:" + row["id"], 3, 60):
            raise DashboardError("Please wait before requesting another resend.")
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._current(db, row)
            intent = db.execute("SELECT * FROM resend_intents WHERE id=? AND account_id=? AND pair_epoch=? AND used IS NULL AND expires>?", (token, row["id"], row["pair_epoch"], self.store.clock())).fetchone()
            if not intent:
                raise DashboardError("This resend confirmation expired or was already used.")
            if expected_delivery is not None and expected_delivery != intent["delivery_id"]:
                raise DashboardError("This confirmation belongs to another delivery.")
            db.execute("UPDATE resend_intents SET used=? WHERE id=?", (self.store.clock(), token))
            original_id = intent["delivery_id"]
        original = self.get(current, original_id)
        if not original or not original["available"]:
            raise PayloadUnavailable("This delivery is no longer available to resend.")
        ident = uuid.uuid4().hex
        source_id = original["cache_ref"] or original["id"]
        with self.store.db() as db:
            self._current(db, current)
            db.execute("INSERT INTO delivery_history(id,account_id,pair_epoch,facebook_id,chat_id,group_key,thread_id,name,kind,created,updated,expires,outcome,cache_ref,retry_of) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'sending',?,?)",
                       (ident, current["id"], current["pair_epoch"], current["facebook_id"], current["chat_id"], uuid.uuid4().hex, original["thread_id"], original["name"], original["kind"], self.store.clock(), self.store.clock(), original["expires"], source_id, original["id"]))
        attempted, fields = False, None
        try:
            with self.cache.read(current["id"], source_id) as (metadata, blocks):
                fields = dict(metadata["fields"], chat_id=current["chat_id"])
                topic = self.conversations.topic(current, original["thread_id"])
                if topic is not None:
                    fields["message_thread_id"] = topic
                # Recheck after integrity verification and before the send.
                if self.store.clock() >= original["expires"] or not self.allowed(current, original["thread_id"], original["kind"]):
                    raise PayloadUnavailable("Delivery expired or is excluded by conversation filters.")
                with self.store.db() as db:
                    self._current(db, current)
                attempted = True
                if metadata["filename"] is None:
                    status, payload = self.api.post(metadata["method"], json.dumps(fields).encode(), chat=current["chat_id"])
                else:
                    boundary = "resend-" + secrets.token_hex(16)
                    prefix = b""
                    for key, value in fields.items():
                        prefix += ("--" + boundary + '\r\nContent-Disposition: form-data; name="' + key + '"\r\n\r\n' + (value if isinstance(value, str) else json.dumps(value)) + "\r\n").encode()
                    prefix += ("--" + boundary + '\r\nContent-Disposition: form-data; name="document"; filename="' + metadata["filename"] + '"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
                    suffix = ("\r\n--" + boundary + "--\r\n").encode()
                    def body():
                        yield prefix
                        yield from blocks()
                        yield suffix
                    status, payload = self.api.post(metadata["method"], body(), chat=current["chat_id"], content_type="multipart/form-data; boundary=" + boundary,
                                                    length=len(prefix) + metadata["file_size"] + len(suffix))
        except Exception:
            if attempted:
                self.finish(current, ident)
            else:
                with self.store.db() as db:
                    db.execute("UPDATE delivery_history SET outcome='failed',reason='payload_unavailable' WHERE id=?", (ident,))
                    db.execute("UPDATE delivery_history SET cache_size=0,cache_reason='unavailable' WHERE id=?", (source_id,))
            return self.get(current, ident)
        self.finish(current, ident, status, payload, fields=fields)
        return self.get(current, ident)
