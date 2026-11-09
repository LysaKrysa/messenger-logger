"""Single-use public onboarding invitations, separate from administrator sessions."""
from __future__ import annotations

import json
import hmac
import re
import secrets
import sqlite3
import uuid

from .store import DashboardError, account_id, digest

TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
SESSION_SECONDS = 1200
RECONNECT_SECONDS = 900


def reconnect_account(store, db, row, *, completed=False):
    """Re-read the fixed account binding inside the caller's transaction."""
    account = db.execute("SELECT * FROM accounts WHERE id=?", (row["account_id"],)).fetchone()
    if (not account or account["platform"] != "facebook" or not account["desired"] or not account["cookies"]
            or account["chat_id"] != row["owner_chat"] or account["facebook_id"] != row["facebook_id"]
            or account["auth_epoch"] != row["auth_epoch"] or account["revision"] != row["account_revision"]):
        return None
    if completed:
        return account
    current = db.execute("SELECT value FROM settings WHERE key=?", ("connection_monitor:" + account["id"],)).fetchone()
    state = json.loads(current[0]) if current else {}
    if (state.get("eligible") is not True or state.get("epoch") != account["auth_epoch"]
            or state.get("revision") != account["revision"] or state.get("owner") != account["chat_id"]
            or state.get("facebook_id") != account["facebook_id"] or not 0 <= store.clock() - state.get("updated", 0) <= 45):
        return None
    heartbeat = db.execute("SELECT value FROM settings WHERE key=?", ("worker_status:" + account["id"],)).fetchone()
    value = json.loads(heartbeat[0]) if heartbeat else {}
    if value.get("revision") == account["revision"] and value.get("connected") is True and 0 <= store.clock() - value.get("updated", 0) <= 90:
        return None
    return account


class SetupInvites:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute('''CREATE TABLE IF NOT EXISTS setup_invites(
                id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, label TEXT NOT NULL,
                created REAL NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                session_hash TEXT UNIQUE, session_expires REAL, csrf TEXT, name TEXT,
                account_id TEXT, telegram_link TEXT, claimed REAL, telegram_hash TEXT
            )''')
            if "telegram_hash" not in {row[1] for row in db.execute("PRAGMA table_info(setup_invites)")}:
                db.execute("ALTER TABLE setup_invites ADD COLUMN telegram_hash TEXT")
            columns = {row[1] for row in db.execute("PRAGMA table_info(setup_invites)")}
            for name, definition in {"purpose": "TEXT NOT NULL DEFAULT 'setup'", "owner_chat": "INTEGER",
                                     "facebook_id": "TEXT", "auth_epoch": "INTEGER", "account_revision": "INTEGER",
                                     "confirmed": "INTEGER NOT NULL DEFAULT 0", "confirm_attempted": "REAL",
                                     "browser_closed": "INTEGER NOT NULL DEFAULT 0"}.items():
                if name not in columns:
                    db.execute("ALTER TABLE setup_invites ADD COLUMN " + name + " " + definition)

    def create(self, label="", hours=24):
        if type(hours) is not int or hours not in {1, 6, 24}:
            raise DashboardError("Choose a link lifetime of 1, 6, or 24 hours.")
        label = label.strip() if isinstance(label, str) else ""
        if len(label) > 80 or any(ord(c) < 32 for c in label):
            raise DashboardError("Use a link label of at most 80 characters.")
        if not self.store.bot():
            raise DashboardError("Connect the shared Telegram bot before creating setup links.")
        ident, token, now = uuid.uuid4().hex, secrets.token_urlsafe(32), self.store.clock()
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            accounts = db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
            reserved = db.execute("SELECT COUNT(*) FROM setup_invites WHERE purpose='setup' AND expires>? AND (state='pending' OR (state='claimed' AND session_expires>?))", (now, now)).fetchone()[0]
            if accounts + reserved >= self.store.max_accounts:
                raise DashboardError("All account slots are in use or reserved by active setup links. Disable an unused link first.")
            db.execute("INSERT INTO setup_invites(id,token_hash,label,created,expires) VALUES (?,?,?,?,?)", (ident, digest(token), label or "New invitation", now, now + hours * 3600))
        return ident, token

    def create_reconnect(self, user_id, *, expected=None):
        if type(user_id) is not int or not 0 < user_id < 2**53:
            raise DashboardError("Reconnect is unavailable.")
        ident, token, now = uuid.uuid4().hex, secrets.token_urlsafe(32), self.store.clock()
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            account = self.store.paired_account(db, user_id, platform="facebook")
            if expected is not None and (not account or any(account[key] != expected[key] for key in ("id", "pair_epoch", "chat_id", "facebook_id"))):
                raise DashboardError("Account pairing changed.")
            binding = dict(account or {})
            binding.update(account_id=binding.get("id"), owner_chat=user_id, account_revision=binding.get("revision"))
            if not account or not reconnect_account(self.store, db, binding):
                raise DashboardError("Reconnect is available only for your existing offline account.")
            limit_key = "reconnect-link:" + str(user_id)
            limit = db.execute("SELECT count,expires FROM limits WHERE key=?", (limit_key,)).fetchone()
            count, expires = (limit["count"], limit["expires"]) if limit and limit["expires"] > now else (0, now + 3600)
            if count >= 3:
                raise DashboardError("Reconnect link limit reached. Please try later or contact the administrator.")
            db.execute("INSERT INTO limits VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET count=excluded.count,expires=excluded.expires", (limit_key, count + 1, expires))
            db.execute("UPDATE setup_invites SET state='revoked',session_hash=NULL,csrf=NULL WHERE purpose='reconnect' AND account_id=? AND state IN ('pending','claimed')", (account["id"],))
            db.execute("INSERT INTO setup_invites(id,token_hash,label,created,expires,purpose,account_id,owner_chat,facebook_id,auth_epoch,account_revision) VALUES (?,?,?,?,?,'reconnect',?,?,?,?,?)",
                       (ident, digest(token), "Reconnect · " + account["name"], now, now + RECONNECT_SECONDS,
                        account["id"], user_id, account["facebook_id"], account["auth_epoch"], account["revision"]))
        return ident, token

    def list(self):
        now = self.store.clock()
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            for value in db.execute("SELECT * FROM setup_invites WHERE purpose='reconnect' AND state IN ('pending','claimed')").fetchall():
                if not reconnect_account(self.store, db, value):
                    db.execute("UPDATE setup_invites SET state='revoked',session_hash=NULL,csrf=NULL WHERE id=?", (value["id"],))
            db.execute("UPDATE setup_invites SET session_hash=NULL,csrf=NULL,telegram_link=NULL WHERE session_expires<=?", (now,))
            db.execute("DELETE FROM setup_invites WHERE expires<?", (now - 7 * 86400,))
            rows = [dict(row) for row in db.execute("SELECT id,label,created,expires,state,session_expires,account_id,claimed,purpose FROM setup_invites ORDER BY CASE WHEN expires>? AND (state='pending' OR (state='claimed' AND session_expires>?)) THEN 0 ELSE 1 END,created DESC,rowid DESC LIMIT 100", (now, now))]
        for row in rows:
            if row["state"] in {"pending", "claimed"} and (row["expires"] <= now or row["state"] == "claimed" and row["session_expires"] <= now):
                row["state"] = "expired"
            row["active"] = row["state"] in {"pending", "claimed"}
        return rows

    def claim(self, token, name):
        name = name.strip() if isinstance(name, str) else ""
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise DashboardError("This invitation is unavailable, expired, or already used.")
        session, csrf, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), self.store.clock()
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM setup_invites WHERE token_hash=? AND state='pending' AND expires>?", (digest(token), now)).fetchone()
            if not row:
                raise DashboardError("This invitation is unavailable, expired, or already used.")
            if row["purpose"] == "reconnect":
                account = reconnect_account(self.store, db, row)
                if not account:
                    raise DashboardError("This reconnect link is no longer available.")
                name = account["name"]
            elif row["purpose"] != "setup" or not name or len(name) > 80 or any(ord(c) < 32 for c in name):
                raise DashboardError("Enter your name, using 1 to 80 characters.")
            db.execute("UPDATE setup_invites SET state='claimed',session_hash=?,session_expires=?,csrf=?,name=?,claimed=? WHERE id=?", (digest(session), min(row["expires"], now + SESSION_SECONDS), csrf, name, now, row["id"]))
        return session

    def session(self, token, *, db=None):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            return None
        if db is None:
            with self.store.db() as connection:
                return self.session(token, db=connection)
        now = self.store.clock()
        row = db.execute("SELECT * FROM setup_invites WHERE session_hash=? AND state IN ('claimed','completed') AND expires>? AND session_expires>?", (digest(token), now, now)).fetchone()
        if row and row["purpose"] == "reconnect" and not reconnect_account(self.store, db, row, completed=row["state"] == "completed"):
            return None
        return dict(row) if row else None

    def confirmation_data(self, row, approve=True):
        action = "rca" if approve else "rcr"
        signature = self.store.secret(action + ":" + row["id"] + ":" + row["session_hash"] + ":" + str(row["auth_epoch"]))[:16]
        return action + "." + row["id"] + "." + signature

    def browser_stops(self):
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id FROM setup_invites WHERE purpose='reconnect' AND confirmed=1 AND browser_closed=0 AND (state<>'claimed' OR expires<=? OR session_expires<=?)", (self.store.clock(), self.store.clock())).fetchall()
            for row in rows:
                db.execute("UPDATE setup_invites SET browser_closed=1 WHERE id=?", (row["id"],))
        return [row["id"] for row in rows]

    def confirmations(self):
        result = []
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT * FROM setup_invites WHERE purpose='reconnect' AND state='claimed' AND confirmed=0 AND confirm_attempted IS NULL AND expires>? AND session_expires>?", (self.store.clock(), self.store.clock())).fetchall()
            for row in rows:
                if reconnect_account(self.store, db, row):
                    db.execute("UPDATE setup_invites SET confirm_attempted=? WHERE id=?", (self.store.clock(), row["id"]))
                    result.append(dict(row))
        return result

    def confirm(self, user_id, data):
        if type(user_id) is not int or not isinstance(data, str) or not re.fullmatch(r"rc[ar]\.[a-f0-9]{32}\.[a-f0-9]{16}", data):
            return "unavailable"
        action, ident, _ = data.split(".")
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM setup_invites WHERE id=? AND purpose='reconnect' AND state='claimed' AND confirmed=0 AND expires>? AND session_expires>?", (ident, self.store.clock(), self.store.clock())).fetchone()
            if not row or row["owner_chat"] != user_id or not reconnect_account(self.store, db, row) or not hmac.compare_digest(data, self.confirmation_data(row, action == "rca")):
                return "unavailable"
            if action == "rca":
                db.execute("UPDATE setup_invites SET confirmed=1 WHERE id=?", (ident,))
                return "approved"
            db.execute("UPDATE setup_invites SET state='revoked',session_hash=NULL,csrf=NULL WHERE id=?", (ident,))
            return "rejected"

    def complete(self, session, cookies):
        parsed = self.store.validate_cookies(json.dumps(cookies))
        ident, telegram_token = uuid.uuid4().hex, secrets.token_urlsafe(32)
        bot = self.store.bot()
        if not bot:
            raise DashboardError("The shared Telegram bot is unavailable. Contact the administrator.")
        link = "https://t.me/" + bot["username"] + "?start=" + telegram_token
        try:
            with self.store.db() as db:
                db.execute("BEGIN IMMEDIATE")
                row = self.session(session, db=db)
                if not row:
                    raise DashboardError("This invitation is no longer active.")
                if row["state"] == "completed":
                    return row["account_id"]
                if row["purpose"] == "reconnect":
                    account = reconnect_account(self.store, db, row)
                    if not account or not row["confirmed"]:
                        raise DashboardError("Confirm this reconnect request in your paired Telegram chat first.")
                    if parsed["c_user"] != row["facebook_id"]:
                        db.execute("UPDATE setup_invites SET state='revoked',session_hash=NULL,csrf=NULL WHERE id=?", (row["id"],))
                        ident = None
                    else:
                        ident = account["id"]
                        db.execute("UPDATE accounts SET cookies=?,revision=revision+1,auth_epoch=auth_epoch+1 WHERE id=?", (self.store.seal("cookies:" + ident, parsed), ident))
                        db.execute("DELETE FROM settings WHERE key=?", ("connection_monitor:" + ident,))
                        db.execute("UPDATE setup_invites SET state='completed',account_revision=account_revision+1,auth_epoch=auth_epoch+1 WHERE id=?", (row["id"],))
                elif row["purpose"] != "setup":
                    raise DashboardError("This invitation is unavailable.")
                elif db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] >= self.store.max_accounts:
                    raise DashboardError("This server has reached its account limit. Contact the administrator.")
                else:
                    db.execute("INSERT INTO accounts(id,name,cookies,facebook_id,created) VALUES (?,?,?,?,?)", (ident, row["name"], self.store.seal("cookies:" + ident, parsed), parsed["c_user"], self.store.clock()))
                    db.execute("INSERT INTO invites VALUES (?,?,?)", (digest(telegram_token), ident, self.store.clock() + 900))
                    db.execute("UPDATE setup_invites SET state='completed',account_id=?,telegram_link=?,telegram_hash=? WHERE id=?", (ident, self.store.seal("setup-link:" + row["id"], link), digest(telegram_token), row["id"]))
        except sqlite3.IntegrityError:
            raise DashboardError("This Messenger account is already connected. Contact the administrator instead of creating a duplicate.") from None
        if ident is None:
            raise DashboardError("Only the originally approved Messenger account can be reconnected. This link has been disabled.")
        return ident

    def progress(self, token):
        row = self.session(token)
        if not row:
            raise DashboardError("This setup session has expired or was disabled. Ask for a new invitation.")
        result = {"id": row["id"], "state": row["state"], "csrf": row["csrf"], "expires": row["session_expires"], "name": row["name"], "telegram_url": None, "pairing": "not_started", "purpose": row["purpose"], "confirmed": bool(row["confirmed"])}
        if row["purpose"] == "reconnect":
            bot = self.store.bot()
            result.update(telegram_url="https://t.me/" + bot["username"] if bot else None, pairing="reconnected" if row["state"] == "completed" else "confirmation")
            return result
        if row["state"] == "completed":
            with self.store.db() as db:
                account = db.execute("SELECT chat_id,pending_chat FROM accounts WHERE id=?", (row["account_id"],)).fetchone()
                invitation = db.execute("SELECT 1 FROM invites WHERE account_id=? AND hash=? AND expires>?", (row["account_id"], row["telegram_hash"], self.store.clock())).fetchone()
            result["pairing"] = "approved" if account and account["chat_id"] else "pending_approval" if account and account["pending_chat"] else "waiting" if invitation else "expired"
            if invitation and row["telegram_link"]:
                result["telegram_url"] = self.store.unseal("setup-link:" + row["id"], row["telegram_link"])
        return result

    def revoke(self, ident):
        account_id(ident)
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT account_id,telegram_hash FROM setup_invites WHERE id=?", (ident,)).fetchone()
            if not row:
                raise DashboardError("Setup link not found.")
            db.execute("UPDATE setup_invites SET state='revoked',session_hash=NULL,csrf=NULL,telegram_link=NULL WHERE id=?", (ident,))
            if row["account_id"]:
                db.execute("DELETE FROM invites WHERE account_id=? AND hash=?", (row["account_id"], row["telegram_hash"]))
