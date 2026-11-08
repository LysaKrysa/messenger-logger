"""Telegram-confirmed, pairing-bound user sessions, separate from administrator auth."""
import hmac
import json
import re
import secrets
import uuid
from urllib.parse import urlsplit

from .store import DashboardError, digest
from telegram_i18n import tr

LINK_SECONDS, SESSION_SECONDS, IDLE_SECONDS = 600, 28800, 1800
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")


class PortalAuth:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS portal_links(
                id TEXT PRIMARY KEY,account_id TEXT NOT NULL,pair_epoch INTEGER NOT NULL,
                owner_chat INTEGER NOT NULL,facebook_id TEXT NOT NULL,token_hash TEXT NOT NULL UNIQUE,
                created REAL NOT NULL,expires REAL NOT NULL,state TEXT NOT NULL,
                claim_hash TEXT,code TEXT,notified INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS portal_sessions(
                hash TEXT PRIMARY KEY,account_id TEXT NOT NULL,pair_epoch INTEGER NOT NULL,
                owner_chat INTEGER NOT NULL,facebook_id TEXT NOT NULL,created REAL NOT NULL,touched REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS portal_session_owner ON portal_sessions(account_id,pair_epoch);
                CREATE TABLE IF NOT EXISTS portal_profiles(
                account_id TEXT PRIMARY KEY,pair_epoch INTEGER NOT NULL,name TEXT NOT NULL);''')

    @staticmethod
    def bound(db, value):
        row = db.execute("SELECT * FROM accounts WHERE id=?", (value["account_id"],)).fetchone()
        if (not row or not row["chat_id"] or row["pair_epoch"] != value["pair_epoch"]
                or row["chat_id"] != value["owner_chat"] or row["facebook_id"] != value["facebook_id"]):
            raise DashboardError("Portal pairing changed.")
        return dict(row)

    def cleanup(self):
        now = self.store.clock()
        with self.store.db() as db:
            db.execute("DELETE FROM portal_links WHERE expires<=? OR state IN ('used','rejected') OR NOT EXISTS (SELECT 1 FROM accounts a WHERE a.id=portal_links.account_id AND a.pair_epoch=portal_links.pair_epoch AND a.chat_id=portal_links.owner_chat AND a.facebook_id=portal_links.facebook_id)", (now,))
            db.execute("DELETE FROM portal_sessions WHERE created<=? OR touched<=? OR NOT EXISTS (SELECT 1 FROM accounts a WHERE a.id=portal_sessions.account_id AND a.pair_epoch=portal_sessions.pair_epoch AND a.chat_id=portal_sessions.owner_chat AND a.facebook_id=portal_sessions.facebook_id)", (now - SESSION_SECONDS, now - IDLE_SECONDS))
            db.execute("DELETE FROM portal_profiles WHERE NOT EXISTS (SELECT 1 FROM accounts a WHERE a.id=portal_profiles.account_id AND a.pair_epoch=portal_profiles.pair_epoch)")

    def issue(self, owner):
        if type(owner) is not int or not self.store.take_limit("portal-link:" + str(owner), 6, 900):
            raise DashboardError("Portal link unavailable.")
        token, ident, now = secrets.token_urlsafe(32), uuid.uuid4().hex, self.store.clock()
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM accounts WHERE chat_id=?", (owner,)).fetchone()
            if not row or not row["facebook_id"] or not self.store.bot():
                raise DashboardError("An approved paired account is required.")
            db.execute("DELETE FROM portal_links WHERE account_id=?", (row["id"],))
            db.execute("INSERT INTO portal_links(id,account_id,pair_epoch,owner_chat,facebook_id,token_hash,created,expires,state) VALUES (?,?,?,?,?,?,?,?,'issued')",
                       (ident, row["id"], row["pair_epoch"], owner, row["facebook_id"], digest(token), now, now + LINK_SECONDS))
        return token

    def claim(self, token):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise DashboardError("Portal link unavailable.")
        claim, code = secrets.token_urlsafe(32), f"{secrets.randbelow(1000000):06d}"
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM portal_links WHERE token_hash=? AND state='issued' AND expires>?", (digest(token), self.store.clock())).fetchone()
            if not row:
                raise DashboardError("Portal link unavailable.")
            self.bound(db, row)
            db.execute("UPDATE portal_links SET state='pending',claim_hash=?,code=? WHERE id=?", (digest(claim), code, row["id"]))
        return claim

    def claim_row(self, token):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            return None
        with self.store.db() as db:
            row = db.execute("SELECT * FROM portal_links WHERE claim_hash=? AND state IN ('pending','approved') AND expires>?", (digest(token), self.store.clock())).fetchone()
            if not row:
                return None
            try:
                self.bound(db, row)
            except DashboardError:
                return None
        return dict(row)

    def csrf(self, token):
        return self.store.secret("portal-csrf:" + token)

    def confirmations(self):
        self.cleanup()
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT * FROM portal_links WHERE state='pending' AND notified=0 AND expires>?", (self.store.clock(),)).fetchall()
            # Claim notification before the network call. An ambiguous bot reply
            # is never replayed; the owner may request a fresh link instead.
            for row in rows:
                db.execute("UPDATE portal_links SET notified=1 WHERE id=?", (row["id"],))
        return [dict(row) for row in rows]

    def callback_data(self, row, approve=True):
        prefix = "pla" if approve else "plr"
        signature = self.store.secret("portal-approval:" + prefix + ":" + row["id"] + ":" + row["claim_hash"])[:16]
        return prefix + "." + row["id"] + "." + signature

    def confirm(self, owner, data):
        if type(owner) is not int or not isinstance(data, str) or not re.fullmatch(r"pl[ar]\.[a-f0-9]{32}\.[a-f0-9]{16}", data):
            return False
        ident = data.split(".")[1]
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM portal_links WHERE id=? AND owner_chat=? AND state='pending' AND notified=1 AND expires>?", (ident, owner, self.store.clock())).fetchone()
            if not row:
                return False
            try:
                self.bound(db, row)
            except DashboardError:
                return False
            approved = data.startswith("pla.")
            if not hmac.compare_digest(data, self.callback_data(row, approved)):
                return False
            db.execute("UPDATE portal_links SET state=? WHERE id=?", ("approved" if approved else "rejected", ident))
        return True

    def complete(self, claim):
        if not isinstance(claim, str) or not TOKEN.fullmatch(claim):
            raise DashboardError("Portal confirmation unavailable.")
        token, now = secrets.token_urlsafe(32), self.store.clock()
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM portal_links WHERE claim_hash=? AND state='approved' AND expires>?", (digest(claim), now)).fetchone()
            if not row:
                raise DashboardError("Portal confirmation unavailable.")
            self.bound(db, row)
            db.execute("UPDATE portal_links SET state='used' WHERE id=?", (row["id"],))
            db.execute("INSERT INTO portal_sessions VALUES (?,?,?,?,?,?,?)", (digest(token), row["account_id"], row["pair_epoch"], row["owner_chat"], row["facebook_id"], now, now))
            db.execute("DELETE FROM portal_sessions WHERE account_id=? AND hash NOT IN (SELECT hash FROM portal_sessions WHERE account_id=? ORDER BY touched DESC LIMIT 10)", (row["account_id"], row["account_id"]))
        return token

    def session(self, token):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            return None
        now = self.store.clock()
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            session = db.execute("SELECT * FROM portal_sessions WHERE hash=? AND created>? AND touched>?", (digest(token), now - SESSION_SECONDS, now - IDLE_SECONDS)).fetchone()
            if not session:
                return None
            try:
                row = self.bound(db, session)
            except DashboardError:
                return None
            db.execute("UPDATE portal_sessions SET touched=? WHERE hash=?", (now, session["hash"]))
        # Account secrets never become template context or session payloads.
        row["has_cookies"] = bool(row.pop("cookies"))
        row.pop("auth_epoch")
        return row

    def revoke(self, account, token=None, *, pair_epoch=None):
        clause = " AND pair_epoch=?" if pair_epoch is not None else ""
        extra = (pair_epoch,) if pair_epoch is not None else ()
        with self.store.db() as db:
            if token is not None:
                db.execute("DELETE FROM portal_sessions WHERE hash=? AND account_id=?" + clause, (digest(token), account) + extra)
            else:
                db.execute("DELETE FROM portal_sessions WHERE account_id=?" + clause, (account,) + extra)
                db.execute("DELETE FROM portal_links WHERE account_id=?" + clause, (account,) + extra)

    def profile(self, row):
        with self.store.db() as db:
            value = db.execute("SELECT name FROM portal_profiles WHERE account_id=? AND pair_epoch=?", (row["id"], row["pair_epoch"])).fetchone()
        return value[0] if value else row["name"]

    def update_profile(self, row, name, language):
        if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(not c.isprintable() for c in name)
                or not isinstance(language, str) or language not in {"pl", "en"}):
            raise DashboardError("Invalid profile settings.")
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            self.bound(db, {"account_id": row["id"], "pair_epoch": row["pair_epoch"], "owner_chat": row["chat_id"], "facebook_id": row["facebook_id"]})
            db.execute("INSERT INTO portal_profiles VALUES (?,?,?) ON CONFLICT(account_id) DO UPDATE SET pair_epoch=excluded.pair_epoch,name=excluded.name", (row["id"], row["pair_epoch"], name.strip()))
            db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("telegram_language:" + str(row["chat_id"]), json.dumps(language)))


class PortalLogins:
    def __init__(self, store, api, origin):
        self.store, self.api, self.auth = store, api, PortalAuth(store)
        self.origin = origin.rstrip("/")
        parsed = urlsplit(self.origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise DashboardError("Configure an HTTPS dashboard origin.")

    def request(self, owner, language):
        try:
            token = self.auth.issue(owner)
        except DashboardError:
            return tr(language, "portal_unavailable")
        return tr(language, "portal_link", url=self.origin + "/me/login?lang=" + language + "#" + token)

    def tick(self):
        for row in self.auth.confirmations():
            language = self.store.telegram_language(row["owner_chat"])
            try:
                self.api.json("sendMessage", {"chat_id": row["owner_chat"], "text": tr(language, "portal_confirmation", code=row["code"]),
                    "reply_markup": {"inline_keyboard": [[
                        {"text": tr(language, "portal_approve"), "callback_data": self.auth.callback_data(row)},
                        {"text": tr(language, "portal_reject"), "callback_data": self.auth.callback_data(row, False)}]]}})
            except Exception:
                print("Dashboard sign-in confirmation unconfirmed; no automatic resend.", flush=True)

    def callback(self, query):
        qid = query.get("id")
        if not isinstance(qid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", qid):
            return
        sender, message = query.get("from"), query.get("message")
        bot, accepted, language = self.store.bot(), False, "pl"
        if isinstance(sender, dict) and type(sender.get("id")) is int and sender["id"] > 0:
            owner = sender["id"]
            language = self.store.telegram_language(owner)
            chat = message.get("chat") if isinstance(message, dict) else None
            author = message.get("from") if isinstance(message, dict) else None
            if (sender.get("is_bot") is False and isinstance(chat, dict) and chat.get("type") == "private"
                    and type(chat.get("id")) is int and chat["id"] == owner and isinstance(author, dict)
                    and bot and author.get("id") == bot["id"] and author.get("is_bot") is True
                    and type(message.get("message_id")) is int and 0 < message["message_id"] < 2**53
                    and self.store.take_limit("portal-confirm:" + str(owner), 20, 60)):
                accepted = self.auth.confirm(owner, query.get("data"))
        key = "portal_approved" if accepted and query["data"].startswith("pla.") else "portal_rejected" if accepted else "portal_expired"
        try:
            self.api.json("answerCallbackQuery", {"callback_query_id": qid, "text": tr(language, key), "show_alert": True, "cache_time": 0})
        except Exception:
            pass
