"""Encrypted account configuration. Message archives remain in worker directories."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import uuid

from cryptography.fernet import Fernet, InvalidToken
from werkzeug.security import generate_password_hash, check_password_hash

from logger import parse_cookies, ConfigurationError
from telegram_i18n import language as normalize_language

ID = re.compile(r"[a-f0-9]{32}\Z")
BOT_TOKEN = re.compile(r"[0-9]+:[A-Za-z0-9_-]{20,}\Z")


class DashboardError(ValueError):
    """Only fixed, safe messages may be presented to an administrator."""


def account_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise DashboardError("Invalid account reference.")
    return value


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    def __init__(self, root, key, *, clock=time.time, readonly=False, max_accounts=6):
        self.root, self.clock, self.readonly = Path(root), clock, readonly
        self.path = self.root / "dashboard.sqlite3"
        self.key = key.strip() if isinstance(key, bytes) else key.strip().encode()
        self.cipher = Fernet(self.key)
        self.max_accounts = max_accounts
        if not readonly:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self.db() as db:
                db.executescript('''
                    PRAGMA journal_mode=WAL;
                    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS accounts(
                        id TEXT PRIMARY KEY,name TEXT NOT NULL,cookies TEXT,facebook_id TEXT UNIQUE,
                        chat_id INTEGER UNIQUE,pending_chat INTEGER UNIQUE,pending_name TEXT,
                        desired INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 1,
                        auth_epoch INTEGER NOT NULL DEFAULT 1,mode TEXT NOT NULL DEFAULT 'deleted',
                        view_once INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS invites(
                        hash TEXT PRIMARY KEY,account_id TEXT NOT NULL UNIQUE,expires REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS sessions(
                        hash TEXT PRIMARY KEY,created REAL NOT NULL,touched REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS limits(key TEXT PRIMARY KEY,count INTEGER NOT NULL,expires REAL NOT NULL);
                ''')
            os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        if self.readonly and not self.path.is_file():
            raise DashboardError("Dashboard configuration is not initialized.")
        # Even a read-only SQLite WAL connection creates shared-memory files.
        # The root CLI/controller must create those as the database owner, so
        # they cannot leave root-owned 0600 files that block the web/bot user.
        previous = os.geteuid()
        owner = self.path.stat().st_uid if self.path.exists() else self.root.stat().st_uid
        switched = previous == 0 and owner != 0
        if switched:
            os.seteuid(owner)
        try:
            db = sqlite3.connect(self.path.resolve().as_uri() + ("?mode=ro" if self.readonly else "?mode=rw"), uri=True, timeout=10) if self.path.exists() else sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA trusted_schema=OFF")
            try:
                with db:
                    yield db
            finally:
                db.close()
        finally:
            if switched:
                os.seteuid(previous)

    def seal(self, purpose, value):
        return self.cipher.encrypt(json.dumps({"purpose": purpose, "value": value}).encode()).decode()

    def unseal(self, purpose, value):
        try:
            payload = json.loads(self.cipher.decrypt(value.encode()))
            if payload["purpose"] != purpose:
                raise ValueError
            return payload["value"]
        except (ValueError, KeyError, TypeError, InvalidToken):
            raise DashboardError("Stored credentials could not be opened. Check the encryption key.") from None

    def secret(self, purpose):
        return hmac.new(self.key, purpose.encode(), hashlib.sha256).hexdigest()

    def setting(self, key, default=None):
        with self.db() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.db() as db:
            db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def telegram_language(self, user_id):
        if type(user_id) is not int or not 0 < user_id < 2**53:
            return "pl"
        return normalize_language(self.setting("telegram_language:" + str(user_id), "pl"))

    def set_telegram_language(self, user_id, language):
        if type(user_id) is not int or not 0 < user_id < 2**53 or not isinstance(language, str) or language not in {"pl", "en"}:
            raise DashboardError("Invalid Telegram language preference.")
        self.set_setting("telegram_language:" + str(user_id), language)

    def restart_active(self):
        with self.db() as db:
            cursor = db.execute("UPDATE accounts SET revision=revision+1 WHERE desired=1 AND cookies IS NOT NULL AND chat_id IS NOT NULL")
            return cursor.rowcount

    def set_password(self, password):
        if not isinstance(password, str) or not 14 <= len(password) <= 128:
            raise DashboardError("Use an administrator password between 14 and 128 characters.")
        hashed = generate_password_hash(password, method="scrypt:32768:8:1")
        with self.db() as db:
            db.execute("INSERT INTO settings VALUES ('password',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(hashed),))
            db.execute("DELETE FROM sessions")

    def login(self, password, ip):
        limit_key = "login:" + self.secret(ip)
        if not self.take_limit(limit_key, 8, 900):
            raise DashboardError("Too many sign-in attempts. Try again in 15 minutes.")
        hashed = self.setting("password")
        if not hashed or not check_password_hash(hashed, password[:129]):
            raise DashboardError("The administrator password was not accepted.")
        token, now = secrets.token_urlsafe(32), self.clock()
        with self.db() as db:
            db.execute("DELETE FROM limits WHERE key=?", (limit_key,))
            db.execute("DELETE FROM sessions WHERE created<? OR touched<?", (now - 28800, now - 1800))
            db.execute("INSERT INTO sessions VALUES (?,?,?)", (digest(token), now, now))
        return token

    def session(self, token, *, revoke=False):
        if not isinstance(token, str) or len(token) > 128:
            return False
        now = self.clock()
        with self.db() as db:
            row = db.execute("SELECT * FROM sessions WHERE hash=?", (digest(token),)).fetchone()
            valid = row and row["created"] > now - 28800 and row["touched"] > now - 1800
            if revoke or not valid:
                db.execute("DELETE FROM sessions WHERE hash=?", (digest(token),))
            else:
                db.execute("UPDATE sessions SET touched=? WHERE hash=?", (now, digest(token)))
        return bool(valid) and not revoke

    def take_limit(self, key, maximum, window):
        now = self.clock()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM limits WHERE expires<=?", (now,))
            row = db.execute("SELECT count FROM limits WHERE key=?", (key,)).fetchone()
            if row and row[0] >= maximum:
                return False
            db.execute("INSERT INTO limits VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1", (key, now + window))
        return True

    def accounts(self, *, private=False):
        with self.db() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM accounts ORDER BY created")]
        if not private:
            for row in rows:
                row["has_cookies"] = bool(row.pop("cookies"))
                row.pop("auth_epoch")
        return rows

    def account(self, ident, *, private=False):
        account_id(ident)
        return next((row for row in self.accounts(private=private) if row["id"] == ident), None)

    def add_account(self, name, cookies=""):
        name = name.strip() if isinstance(name, str) else ""
        if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise DashboardError("Use an account label between 1 and 80 characters.")
        parsed = self.validate_cookies(cookies) if cookies.strip() else None
        ident = uuid.uuid4().hex
        try:
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] >= self.max_accounts:
                    raise DashboardError("This server has reached its configured account limit.")
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='setup_invites'").fetchone():
                    now = self.clock()
                    reserved = db.execute("SELECT COUNT(*) FROM setup_invites WHERE expires>? AND (state='pending' OR (state='claimed' AND session_expires>?))", (now, now)).fetchone()[0]
                    if db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] + reserved >= self.max_accounts:
                        raise DashboardError("Available account slots are reserved by active setup links. Disable an unused link before adding an account manually.")
                db.execute("INSERT INTO accounts(id,name,cookies,facebook_id,created) VALUES (?,?,?,?,?)", (ident, name, self.seal("cookies:" + ident, parsed) if parsed else None, parsed["c_user"] if parsed else None, self.clock()))
        except sqlite3.IntegrityError:
            raise DashboardError("That Messenger account is already configured.") from None
        return ident

    @staticmethod
    def validate_cookies(raw):
        if not isinstance(raw, str) or len(raw) > 131072:
            raise DashboardError("Cookie JSON must be at most 128 KB.")
        try:
            return parse_cookies(raw)
        except ConfigurationError:
            raise DashboardError("Cookie JSON is invalid. Include c_user, xs, and datr from your own session.") from None

    def update_cookies(self, ident, raw):
        parsed = self.validate_cookies(raw)
        account_id(ident)
        try:
            with self.db() as db:
                row = db.execute("SELECT facebook_id FROM accounts WHERE id=?", (ident,)).fetchone()
                if not row:
                    raise DashboardError("Account not found.")
                if row[0] and row[0] != parsed["c_user"]:
                    raise DashboardError("Those cookies belong to a different Messenger account. Create a separate account instead.")
                db.execute("UPDATE accounts SET cookies=?,facebook_id=?,revision=revision+1 WHERE id=?", (self.seal("cookies:" + ident, parsed), parsed["c_user"], ident))
        except sqlite3.IntegrityError:
            raise DashboardError("That Messenger account is already configured.") from None

    def configure_bot(self, token, bot):
        if not BOT_TOKEN.fullmatch(token) or not isinstance(bot, dict) or type(bot.get("id")) is not int or str(bot.get("id")) != token.split(":", 1)[0] or bot.get("has_topics_enabled") is not True or not isinstance(bot.get("username"), str) or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", bot["username"]):
            raise DashboardError("Verify the bot token and enable Threaded mode in BotFather first.")
        previous = self.setting("bot")
        if previous and previous["id"] != bot["id"]:
            raise DashboardError("This workspace uses one bot. Enter a token for the existing bot.")
        self.set_setting("bot", {"id": bot["id"], "username": bot["username"], "token": self.seal("bot", token)})

    def bot(self, *, token=False):
        bot = self.setting("bot")
        if bot:
            bot = dict(bot)
            encrypted = bot.pop("token")
            if token:
                bot["token"] = self.unseal("bot", encrypted)
        return bot

    def invite(self, ident):
        account_id(ident)
        if not self.bot():
            raise DashboardError("Connect the shared Telegram bot first.")
        token = secrets.token_urlsafe(32)
        with self.db() as db:
            row = db.execute("SELECT chat_id FROM accounts WHERE id=?", (ident,)).fetchone()
            if not row:
                raise DashboardError("Account not found.")
            if row[0]:
                raise DashboardError("Disconnect the current Telegram pairing before creating a new invite.")
            db.execute("DELETE FROM invites WHERE account_id=?", (ident,))
            db.execute("UPDATE accounts SET pending_chat=NULL,pending_name=NULL WHERE id=?", (ident,))
            db.execute("INSERT INTO invites VALUES (?,?,?)", (digest(token), ident, self.clock() + 900))
        return "https://t.me/" + self.bot()["username"] + "?start=" + token

    def claim_invite(self, token, chat_id, name):
        if not re.fullmatch(r"[A-Za-z0-9_-]{40,64}", token) or type(chat_id) is not int or chat_id <= 0:
            return False
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT account_id FROM invites WHERE hash=? AND expires>?", (digest(token), self.clock())).fetchone()
            if not row or db.execute("SELECT 1 FROM accounts WHERE chat_id=? OR pending_chat=?", (chat_id, chat_id)).fetchone():
                return False
            changed = db.execute("UPDATE accounts SET pending_chat=?,pending_name=? WHERE id=? AND chat_id IS NULL AND pending_chat IS NULL", (chat_id, name[:100], row[0])).rowcount
            if changed:
                db.execute("DELETE FROM invites WHERE hash=?", (digest(token),))
        return bool(changed)

    def action(self, ident, action, *, mode="deleted", view_once=False):
        account_id(ident)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM accounts WHERE id=?", (ident,)).fetchone()
            if not row:
                raise DashboardError("Account not found.")
            if action == "approve":
                if not row["pending_chat"]:
                    raise DashboardError("There is no pending Telegram pairing.")
                db.execute("UPDATE accounts SET chat_id=pending_chat,pending_chat=NULL,pending_name=NULL,revision=revision+1 WHERE id=?", (ident,))
            elif action == "disconnect":
                db.execute("UPDATE accounts SET desired=0,chat_id=NULL,pending_chat=NULL,pending_name=NULL,revision=revision+1,auth_epoch=auth_epoch+1 WHERE id=?", (ident,))
                db.execute("DELETE FROM invites WHERE account_id=?", (ident,))
            elif action in {"start", "restart"}:
                if not row["cookies"] or not row["chat_id"]:
                    raise DashboardError("Save cookies and approve a Telegram pairing before starting.")
                db.execute("UPDATE accounts SET desired=1,revision=revision+1 WHERE id=?", (ident,))
            elif action == "stop":
                db.execute("UPDATE accounts SET desired=0,revision=revision+1 WHERE id=?", (ident,))
            elif action == "options":
                if mode not in {"deleted", "all"}:
                    raise DashboardError("Choose deleted messages or all supported chat events.")
                db.execute("UPDATE accounts SET mode=?,view_once=?,revision=revision+1 WHERE id=?", (mode, int(bool(view_once)), ident))
            elif action == "remove":
                db.execute("DELETE FROM invites WHERE account_id=?", (ident,))
                db.execute("DELETE FROM accounts WHERE id=?", (ident,))
            else:
                raise DashboardError("Unknown account action.")

    def worker_key(self, row):
        return row["id"] + "." + self.secret("worker:" + row["id"] + ":" + str(row["auth_epoch"]))

    def authenticate_worker(self, token):
        if not isinstance(token, str) or len(token) != 97:
            return None
        ident = token.split(".", 1)[0]
        if not ID.fullmatch(ident):
            return None
        row = self.account(ident, private=True)
        return row if row and row["desired"] and row["chat_id"] and hmac.compare_digest(token, self.worker_key(row)) else None

    def disconnect_user(self, chat_id):
        with self.db() as db:
            row = db.execute("SELECT id FROM accounts WHERE chat_id=? OR pending_chat=?", (chat_id, chat_id)).fetchone()
        if row:
            self.action(row[0], "disconnect")
        return bool(row)


def from_environment(*, readonly=False):
    key_path = Path(os.environ.get("DASHBOARD_KEY_FILE", "/etc/messenger-dashboard/master.key"))
    return Store(os.environ.get("DASHBOARD_STATE_DIR", "/var/lib/messenger-dashboard"), key_path.read_bytes(), readonly=readonly,
                 max_accounts=int(os.environ.get("DASHBOARD_MAX_ACCOUNTS", "6")))
