"""Offline alerts and fixed-account reconnect security; no real external requests."""
import concurrent.futures
import json
import re
from unittest.mock import patch

from dashboard.broker import PairingReceiver, BotUnavailable
from dashboard.connection_alerts import ConnectionAlerts, OFFLINE_SECONDS, STARTUP_SECONDS
from dashboard.setup_store import SetupInvites
from dashboard.setup_web import COOKIE
from dashboard.store import Store, DashboardError
from dashboard.tests.test_dashboard import Base, FakeAPI, BOT, COOKIES
from dashboard.tests.test_setup import FakeBrowser
from dashboard.web import create_app

ORIGIN = "https://setup.example:8443"
USER, OTHER = 87654321, 22222222


class Browser(FakeBrowser):
    def __init__(self):
        super().__init__()
        self.cookies = json.loads(COOKIES)

    def call(self, ident, action, data=None):
        result = super().call(ident, action, data)
        return {"cookies": self.cookies} if action == "capture" else result


class ReconnectTests(Base):
    def setUp(self):
        super().setUp()
        self.first = self.paired()
        self.second = self.paired(OTHER, COOKIES.replace("111222333", "444555666"))
        self.api, self.browser = FakeAPI(), Browser()
        self.alerts = ConnectionAlerts(self.store, self.api, origin=ORIGIN,
                                      close_browser=lambda ident: self.browser.call(ident, "stop"))
        self.invites = self.alerts.invites
        self.receiver = PairingReceiver(self.store, self.api, origin=ORIGIN)
        self.app = create_app(self.store, origin=ORIGIN, testing=True, browser_client=self.browser)
        self.client = self.app.test_client()

    def heartbeat(self, connected, ident=None):
        row = self.store.account(ident or self.first, private=True)
        self.store.report_worker(row, {"revision": row["revision"], "connected": connected, "last_event": None})

    def offline(self):
        self.heartbeat(True)
        self.alerts.tick()
        self.now += 1
        self.heartbeat(False)
        self.alerts.tick()
        self.now += OFFLINE_SECONDS
        self.heartbeat(False)
        self.api.calls.clear()
        self.alerts.tick()
        messages = [json.loads(body) for method, body, _ in self.api.calls if method == "sendMessage"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["chat_id"], USER)
        return re.search(r"/reconnect#([A-Za-z0-9_-]{43})", messages[0]["text"])[1]

    def claim(self, token, client=None, **extra):
        result = (client or self.client).post("/setup/api/claim", json=dict(token=token, name="Do not rename the account", consent=True, **extra), base_url=ORIGIN, headers={"Origin": ORIGIN})
        self.assertEqual(result.status_code, 200, result.data)
        return result.json

    def session(self, client=None):
        return (client or self.client).get_cookie(COOKIE, domain="setup.example").value

    def post(self, action, state, data=None):
        return self.client.post("/setup/api/" + action, json={} if data is None else data, base_url=ORIGIN,
                                headers={"Origin": ORIGIN, "X-Setup-CSRF": state["csrf"]})

    def confirm(self, approve=True, user=USER, **changes):
        self.api.calls.clear()
        self.alerts.confirmations()
        message = json.loads(self.api.calls[-1][1])
        data = message["reply_markup"]["inline_keyboard"][0][0 if approve else 1]["callback_data"]
        query = {"id": "SYNTHETIC_CALLBACK", "data": data, "from": {"id": user, "is_bot": False},
                 "message": {"message_id": 1, "chat": {"id": user, "type": "private"}, "from": {"id": BOT["id"], "is_bot": True}}}
        query.update(changes)
        self.alerts.confirm(query)
        return data, query

    def test_offline_debounce_one_alert_restarts_and_localized_recovery_episode(self):
        token = self.offline()
        first = json.loads(self.api.calls[0][1])
        self.assertIn("rejestrator", first["text"])
        self.assertIn("/polacz", first["text"])
        self.api.calls.clear()
        restarted = ConnectionAlerts(self.store, self.api, origin=ORIGIN, close_browser=lambda _: None)
        restarted.tick()
        self.now += 20
        restarted.tick()
        self.assertEqual(self.api.calls, [])
        self.heartbeat(True)
        self.alerts.tick()
        with self.assertRaises(DashboardError):
            self.invites.claim(token, "owner")
        self.now += 31
        self.heartbeat(True)
        self.alerts.tick()
        self.store.set_telegram_language(USER, "en")
        self.now += 1
        self.heartbeat(False)
        self.alerts.tick()
        self.now += OFFLINE_SECONDS
        self.heartbeat(False)
        self.api.calls.clear()
        self.alerts.tick()
        text = json.loads(self.api.calls[0][1])["text"]
        self.assertIn("logger is offline", text)
        self.assertIn("/reconnect", text)
        self.assertNotIn("/polacz", text)

    def test_short_disconnect_and_brief_recovery_do_not_spam(self):
        self.offline()
        self.api.calls.clear()
        self.heartbeat(True)
        self.alerts.tick()
        self.now += 10
        self.heartbeat(False)
        self.alerts.tick()
        self.now += OFFLINE_SECONDS
        self.heartbeat(False)
        self.alerts.tick()
        self.assertFalse(any(json.loads(body).get("chat_id") == USER for method, body, _ in self.api.calls if method == "sendMessage"))

    def test_concurrent_monitors_claim_one_alert_and_quiet_does_not_hide_operational_alerts(self):
        self.offline()
        key = "connection_monitor:" + self.first
        value = self.store.setting(key)
        value["alerted"] = False
        self.store.set_setting(key, value)
        row = self.store.account(self.first, private=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(lambda _: self.alerts.observe(row), range(4)))
        self.assertEqual(claims.count(True), 1)
        value["alerted"] = False
        self.store.set_setting(key, value)
        self.store.user_control(USER, "quiet", 3600)
        self.api.calls.clear()
        self.alerts.tick()
        self.assertEqual(json.loads(self.api.calls[0][1])["chat_id"], USER)

    def test_startup_grace_stale_heartbeat_and_monitor_warmup(self):
        self.alerts.tick()
        self.now += STARTUP_SECONDS - 1
        self.alerts.tick()
        self.assertEqual(self.api.calls, [])
        self.now += 1
        self.alerts.tick()
        self.assertTrue(self.api.calls)
        # An old connected report on a newly deployed monitor gets a short
        # warmup so the next live heartbeat can arrive before any alert.
        self.store.set_setting("connection_monitor:" + self.first, {})
        self.heartbeat(True)
        self.now += 300
        new = ConnectionAlerts(self.store, self.api, origin=ORIGIN, close_browser=lambda _: None)
        self.api.calls.clear()
        new.tick()
        self.assertEqual(self.api.calls, [])
        self.now += 5
        self.heartbeat(True)
        new.tick()
        self.now += 15
        self.heartbeat(True)
        new.tick()
        self.assertEqual(self.api.calls, [])

    def test_paused_unpaired_removed_accounts_never_get_automatic_alerts(self):
        self.store.user_control(USER, "pause")
        self.store.action(self.second, "disconnect")
        self.alerts.tick()
        self.now += 1000
        self.alerts.tick()
        self.assertEqual(self.api.calls, [])
        self.store.action(self.first, "remove")
        self.alerts.tick()
        self.assertEqual(self.api.calls, [])

    def test_uncertain_alert_and_confirmation_are_never_automatically_replayed(self):
        self.heartbeat(True)
        self.alerts.tick()
        self.now += 1
        self.heartbeat(False)
        self.alerts.tick()
        self.now += OFFLINE_SECONDS
        self.heartbeat(False)
        self.api.error = True
        self.alerts.tick()
        self.api.error = False
        self.api.calls.clear()
        self.alerts.tick()
        self.assertEqual(self.api.calls, [])
        _, token = self.invites.create_reconnect(USER)
        self.claim(token)
        self.api.error = True
        self.alerts.confirmations()
        self.api.error = False
        self.api.calls.clear()
        self.alerts.confirmations()
        self.assertEqual(self.api.calls, [])

    def test_get_preview_is_read_only_and_one_token_has_only_one_claim(self):
        token = self.offline()
        result = self.client.get("/reconnect#" + token, base_url=ORIGIN)
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("Set-Cookie", result.headers)
        self.assertNotIn(token, result.text)
        self.assertIn("Reconnect your Messenger", result.text)
        self.claim(token)
        denied = self.app.test_client().post("/setup/api/claim", json={"token": token, "consent": True}, base_url=ORIGIN, headers={"Origin": ORIGIN})
        self.assertEqual(denied.status_code, 400)

    def test_confirmation_gates_every_browser_and_capture_action(self):
        state = self.claim(self.offline())
        self.assertFalse(state["confirmed"])
        self.assertEqual(state["purpose"], "reconnect")
        for action in ("start", "frame", "view", "input", "finish"):
            self.assertEqual(self.post(action, state, {"approve": True, "action": "text", "text": "test"}).status_code, 400)
        self.assertEqual(self.client.get("/setup/api/stream", base_url=ORIGIN).status_code, 400)
        self.assertEqual(self.browser.calls, [])
        self.confirm()
        self.assertTrue(self.client.get("/setup/api/status", base_url=ORIGIN).json["confirmed"])
        self.assertEqual(self.post("start", state).status_code, 200)

    def test_only_original_facebook_is_updated_without_new_account_or_pairing(self):
        before = self.store.account(self.first, private=True)
        old_key = self.store.worker_key(before)
        state = self.claim(self.offline(), account_id=self.second, facebook_id="444555666", purpose="setup")
        self.confirm()
        self.post("start", state)
        self.browser.cookies["xs"] = "SYNTHETIC_REFRESHED_SESSION"
        result = self.post("finish", state, {"approve": True, "cookies": {"c_user": "444555666"}})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.json["pairing"], "reconnected")
        self.assertNotIn("?start=", result.json["telegram_url"])
        after = self.store.account(self.first, private=True)
        self.assertEqual(len(self.store.accounts()), 2)
        for key in ("id", "name", "facebook_id", "chat_id", "mode", "view_once", "desired"):
            self.assertEqual(before[key], after[key])
        self.assertEqual(after["revision"], before["revision"] + 1)
        self.assertEqual(after["auth_epoch"], before["auth_epoch"] + 1)
        self.assertIsNone(self.store.authenticate_worker(old_key))
        self.assertEqual(self.store.unseal("cookies:" + self.first, after["cookies"]), self.browser.cookies)
        self.assertNotIn(self.browser.cookies["xs"], result.text)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM invites").fetchone()[0], 0)
            self.assertNotIn(self.browser.cookies["xs"], "\n".join(db.iterdump()))
        repeat = self.post("finish", state, {"approve": True})
        self.assertEqual(repeat.status_code, 200)
        self.assertEqual(self.store.account(self.first)["revision"], after["revision"])
        self.assertIsNotNone(self.store.account(self.second))

    def test_different_facebook_account_revokes_link_closes_browser_and_writes_nothing(self):
        state = self.claim(self.offline())
        self.confirm()
        self.post("start", state)
        before = self.store.accounts(private=True)
        self.browser.cookies["c_user"] = "999999999"
        result = self.post("finish", state, {"approve": True})
        self.assertEqual(result.status_code, 400)
        self.assertIn("originally approved", result.json["error"])
        self.assertEqual(self.store.accounts(private=True), before)
        self.assertIsNone(self.invites.session(self.session()))
        self.assertEqual(self.browser.calls[-1][1], "stop")

    def test_owner_mismatch_forged_signature_foreign_chat_or_bot_cannot_approve(self):
        self.claim(self.offline())
        _, query = self.confirm(user=OTHER)
        self.assertFalse(self.invites.progress(self.session())["confirmed"])
        invalid = [dict(query, data=query["data"][:-1] + ("0" if query["data"][-1] != "0" else "1")),
                   dict(query, **{"from": {"id": USER, "is_bot": False}})]
        # The second query still carries the other user's chat. Both fail.
        for value in invalid:
            self.alerts.confirm(value)
        correct = dict(query, **{"from": {"id": USER, "is_bot": False}, "message": {"chat": {"id": USER, "type": "private"}, "from": {"id": BOT["id"], "is_bot": True}}})
        for chat_type, author in (("supergroup", BOT["id"]), ("private", BOT["id"] + 1)):
            value = dict(correct, message={"chat": {"id": USER, "type": chat_type}, "from": {"id": author, "is_bot": True}})
            self.alerts.confirm(value)
        self.assertFalse(self.invites.progress(self.session())["confirmed"])
        self.alerts.confirm(correct)
        self.assertTrue(self.invites.progress(self.session())["confirmed"])
        self.alerts.confirm(correct)
        self.assertIn("niedostępne", json.loads(self.api.calls[-1][1])["text"])

    def test_reject_expiry_recovery_pause_unlink_and_cookie_changes_invalidate_sessions(self):
        for kind in ("reject", "expiry", "recover", "pause", "unlink", "cookies", "revision", "remove"):
            with self.subTest(kind=kind):
                # Reuse a fresh fixture per boundary to avoid link-rate coupling.
                if kind != "reject":
                    self.tearDown()
                    self.setUp()
                state = self.claim(self.offline())
                if kind == "reject":
                    self.confirm(approve=False)
                else:
                    self.confirm()
                    if kind == "expiry": self.now += 901
                    elif kind == "recover": self.heartbeat(True)
                    elif kind == "pause": self.store.user_control(USER, "pause")
                    elif kind == "unlink": self.store.action(self.first, "disconnect")
                    elif kind == "cookies": self.store.update_cookies(self.first, COOKIES)
                    elif kind == "revision": self.store.action(self.first, "restart")
                    elif kind == "remove": self.store.action(self.first, "remove")
                self.assertIsNone(self.invites.session(self.session()))
                self.assertEqual(self.post("finish", state, {"approve": True}).status_code, 401)

    def test_new_link_revokes_old_one_and_rate_limit_persists_with_single_active_link(self):
        first = self.offline()
        self.claim(first)
        _, second = self.invites.create_reconnect(USER)
        self.assertIsNone(self.invites.session(self.session()))
        with self.assertRaises(DashboardError): self.invites.claim(first, "owner")
        self.invites.create_reconnect(USER)
        with self.assertRaises(DashboardError): self.invites.create_reconnect(USER)
        reopened = SetupInvites(Store(self.store.root, self.store.key, clock=lambda: self.now))
        with self.assertRaises(DashboardError): reopened.create_reconnect(USER)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM setup_invites WHERE purpose='reconnect' AND state IN ('pending','claimed')").fetchone()[0], 1)
        self.assertEqual(len(self.store.accounts()), 2)

    def test_full_account_capacity_does_not_prevent_refresh_or_reserve_another_slot(self):
        token = self.offline()
        self.store.max_accounts = 2
        state = self.claim(token)
        self.confirm()
        self.assertEqual(self.post("finish", state, {"approve": True}).status_code, 200)
        self.assertEqual(len(self.store.accounts()), 2)
        with self.assertRaises(DashboardError): self.invites.create()

    def test_active_reconnects_do_not_consume_new_account_reservations(self):
        self.offline()
        self.store.max_accounts = 3
        ident = self.store.add_account("Administrator-approved extra slot")
        self.assertIsNotNone(self.store.account(ident))
        self.assertEqual(len(self.store.accounts()), 3)

    def test_concurrent_claim_confirmation_and_completion_have_single_effect(self):
        token = self.offline()
        def claim(_):
            try: return self.invites.claim(token, "ignored")
            except DashboardError: return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            sessions = list(pool.map(claim, range(4)))
        self.assertEqual(sum(value is not None for value in sessions), 1)
        session = next(value for value in sessions if value)
        row = self.invites.session(session)
        data = self.invites.confirmation_data(row)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(lambda _: self.invites.confirm(USER, data), range(4)))
        self.assertEqual(outcomes.count("approved"), 1)
        before = self.store.account(self.first)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(lambda _: self.invites.complete(session, json.loads(COOKIES)), range(4)))
        self.assertEqual(outcomes, [self.first] * 4)
        self.assertEqual(self.store.account(self.first)["revision"], before["revision"] + 1)
        self.assertEqual(len(self.store.accounts()), 2)

    def test_admin_listing_and_revocation_include_reconnects_without_exposing_secrets(self):
        token = self.offline()
        row = self.invites.list()[0]
        self.assertEqual(row["purpose"], "reconnect")
        self.assertEqual(row["account_id"], self.first)
        self.assertTrue(row["active"])
        self.assertNotIn(token, json.dumps(self.invites.list()))
        self.invites.revoke(row["id"])
        with self.assertRaises(DashboardError): self.invites.claim(token, "owner")
        self.assertEqual(len(self.store.accounts()), 2)

    def test_reconnect_command_is_private_owned_and_available_only_after_outage(self):
        def command(text, user=USER, update_id=1, kind="private"):
            self.receiver.handle({"update_id": update_id, "message": {"text": text, "chat": {"id": user, "type": kind}, "from": {"id": user, "is_bot": False}}})
        command("/polacz")
        self.assertNotIn("/reconnect#", json.loads(self.api.calls[-1][1])["text"])
        self.offline()
        command("/reconnect", user=OTHER, update_id=2)
        self.assertNotIn("/reconnect#", json.loads(self.api.calls[-1][1])["text"])
        command("/polacz", update_id=3)
        self.assertIn("/reconnect#", json.loads(self.api.calls[-1][1])["text"])
        before = self.invites.list()
        command("/polacz 22222222", update_id=4)
        command("/reconnect", update_id=5, kind="supergroup")
        self.assertEqual(before, self.invites.list())

    def test_pausing_or_recovering_closes_confirmed_browser_and_invalidates_its_cookie(self):
        state = self.claim(self.offline())
        self.confirm()
        self.post("start", state)
        self.heartbeat(True)
        self.alerts.tick()
        self.assertIn((state["id"], "stop", None), self.browser.calls)
        self.assertIsNone(self.invites.session(self.session()))

    def test_link_is_rejected_when_monitor_is_stale_and_secrets_never_enter_progress(self):
        self.claim(self.offline())
        raw = json.dumps(self.invites.progress(self.session()))
        self.assertNotIn("111222333", raw)
        self.assertNotIn("SYNTHETIC_XS", raw)
        self.now += 46
        self.assertIsNone(self.invites.session(self.session()))
