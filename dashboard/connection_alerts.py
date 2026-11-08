"""Independent, metadata-only offline monitoring and owner-confirmed reconnects."""
import json
import os
import re
from urllib.parse import urlsplit

from .setup_store import SetupInvites
from .store import DashboardError
from telegram_i18n import tr

OFFLINE_SECONDS = 180
STARTUP_SECONDS = 360
RECOVERY_SECONDS = 30


class ConnectionAlerts:
    def __init__(self, store, api, *, origin=None, close_browser=None, maintenance=None):
        self.store, self.api = store, api
        self.started = store.clock()
        self.invites = SetupInvites(store)
        self.close_browser = close_browser or self._close_browser
        self.maintenance = maintenance or (lambda: None)
        self.origin = (origin or os.environ.get("DASHBOARD_ORIGIN", "https://localhost:8443")).rstrip("/")
        parsed = urlsplit(self.origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise DashboardError("Configure an HTTPS dashboard origin for reconnect links.")

    def _close_browser(self, ident):
        from .setup_web import BrowserClient
        BrowserClient(self.store.secret("remote-browser-api")).call(ident, "stop")

    def observe(self, row):
        """Claim at most one alert per outage before any uncertain network send."""
        now, key = self.store.clock(), "connection_monitor:" + row["id"]
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT * FROM accounts WHERE id=?", (row["id"],)).fetchone()
            if not current or not current["desired"] or not current["chat_id"] or not current["cookies"]:
                db.execute("DELETE FROM settings WHERE key=?", (key,))
                return False
            if any(current[key] != row[key] for key in ("chat_id", "facebook_id", "auth_epoch", "revision")):
                return False
            row = current
            raw = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            state = json.loads(raw[0]) if raw else {}
            binding = {"epoch": row["auth_epoch"], "revision": row["revision"], "owner": row["chat_id"], "facebook_id": row["facebook_id"]}
            if any(state.get(k) != v for k, v in binding.items()):
                state = dict(binding, first_seen=now, had_online=False, alerted=False)
            raw = db.execute("SELECT value FROM settings WHERE key=?", ("worker_status:" + row["id"],)).fetchone()
            worker = json.loads(raw[0]) if raw else {}
            same = worker.get("revision") == row["revision"]
            fresh = same and 0 <= now - worker.get("updated", 0) <= 90
            good = fresh and worker.get("connected") is True
            if good:
                state.update(had_online=True, eligible=False, down_since=None)
                if state.get("online_since") is None:
                    state["online_since"] = now
                if now - state["online_since"] >= RECOVERY_SECONDS:
                    state["alerted"] = False
            else:
                state["online_since"] = None
                if same and worker.get("connected") is True and worker.get("updated", 0) > 0:
                    state["had_online"] = True
                if state.get("down_since") is None:
                    state["down_since"] = worker["updated"] + 90 if same and worker.get("connected") is True and worker.get("updated", 0) <= now - 90 else now
                state["eligible"] = (now - state["down_since"] >= OFFLINE_SECONDS
                                     and (state["had_online"] or now - state["first_seen"] >= STARTUP_SECONDS))
            state["updated"] = now
            notify = state.get("eligible") and not state.get("alerted") and now - self.started >= 15
            if notify:
                state["alerted"] = True
            db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(state)))
        return bool(notify)

    def issue(self, user_id):
        _, token = self.invites.create_reconnect(user_id)
        return self.origin + "/reconnect#" + token

    def request(self, user_id, language):
        row = self.store.user_account(user_id)
        if row is None:
            return tr(language, "not_paired")
        try:
            url = self.issue(user_id)
        except DashboardError:
            return tr(language, "reconnect_unavailable")
        return tr(language, "reconnect_link", url=url)

    def confirmations(self):
        for row in self.invites.confirmations():
            language = self.store.telegram_language(row["owner_chat"])
            keyboard = {"inline_keyboard": [[
                {"text": tr(language, "reconnect_approve"), "callback_data": self.invites.confirmation_data(row)},
                {"text": tr(language, "reconnect_reject"), "callback_data": self.invites.confirmation_data(row, False)},
            ]]}
            try:
                self.api.json("sendMessage", {"chat_id": row["owner_chat"], "text": tr(language, "reconnect_confirmation"),
                                               "reply_markup": keyboard, "link_preview_options": {"is_disabled": True}})
            except Exception:
                print("Reconnect confirmation could not be confirmed; no automatic resend.", flush=True)

    def confirm(self, query):
        sender, message = query.get("from"), query.get("message")
        query_id, bot = query.get("id"), self.store.bot()
        if not isinstance(query_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", query_id):
            return
        language, outcome = "pl", "unavailable"
        if isinstance(sender, dict) and type(sender.get("id")) is int:
            language = self.store.telegram_language(sender["id"])
            chat = message.get("chat") if isinstance(message, dict) else None
            author = message.get("from") if isinstance(message, dict) else None
            if (sender.get("is_bot") is False and isinstance(chat, dict) and chat.get("type") == "private"
                    and type(chat.get("id")) is int and chat["id"] == sender["id"]
                    and isinstance(author, dict) and bot and author.get("is_bot") is True and author.get("id") == bot["id"]):
                outcome = self.invites.confirm(sender["id"], query.get("data"))
        try:
            self.api.json("answerCallbackQuery", {"callback_query_id": query_id, "text": tr(language, "reconnect_expired" if outcome == "unavailable" else "reconnect_" + outcome), "show_alert": True, "cache_time": 0})
        except Exception:
            pass

    def tick(self):
        if not self.store.bot():
            return
        for row in self.store.accounts(private=True):
            try:
                if self.observe(row):
                    if not self.store.take_limit("offline-alert:" + row["id"], 3, 3600):
                        continue
                    language = self.store.telegram_language(row["chat_id"])
                    try:
                        url = self.issue(row["chat_id"])
                        text = tr(language, "offline_alert") + "\n\n" + tr(language, "reconnect_link", url=url)
                    except DashboardError:
                        text = tr(language, "offline_alert") + "\n\n" + tr(language, "reconnect_unavailable")
                    self.api.json("sendMessage", {"chat_id": row["chat_id"], "text": text, "link_preview_options": {"is_disabled": True}})
            except Exception:
                print("Connection alert could not be confirmed; no automatic resend.", flush=True)
        self.invites.list()  # Revoke invalid/recovered/paused bindings and prune old rows.
        for ident in self.invites.browser_stops():
            try:
                self.close_browser(ident)
            except Exception:
                pass  # The controller's independent hard deadline still closes it.
        self.confirmations()

    def run(self, stop):
        while not stop.is_set():
            for operation in (self.tick, self.maintenance):
                try:
                    operation()
                except Exception:
                    print("Connection monitoring or retention maintenance unavailable; private details hidden.", flush=True)
            stop.wait(5)
