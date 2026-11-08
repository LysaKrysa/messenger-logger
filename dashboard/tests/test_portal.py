"""Owner-only sessions, controls, metadata and Telegram sign-in without live sends."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import re
from unittest.mock import patch

from dashboard.broker import PairingReceiver, create_app as broker_app
from dashboard.portal_auth import PortalAuth, SESSION_SECONDS, IDLE_SECONDS
from dashboard.portal_i18n import TEXT
from dashboard.portal_web import COOKIE, CLAIM_COOKIE
from dashboard.store import DashboardError
from dashboard.tests.test_dashboard import Base, FakeAPI, BOT, COOKIES, ORIGIN, PASSWORD
from dashboard.web import create_app

USER, OTHER = 87654321, 22222222


class PortalBase(Base):
    def setUp(self):
        super().setUp()
        self.first = self.paired()
        self.second = self.paired(OTHER, COOKIES.replace("111222333", "444555666"))
        self.row = self.store.account(self.first, private=True)
        self.other = self.store.account(self.second, private=True)
        self.api = FakeAPI()
        self.web = create_app(self.store, origin=ORIGIN, testing=True, delivery_api=self.api)
        self.auth = self.web.extensions["portal_auth"]
        self.client = self.web.test_client()

    def approved(self, owner=USER):
        link = self.auth.issue(owner)
        claim = self.auth.claim(link)
        value = next(row for row in self.auth.confirmations() if row["owner_chat"] == owner)
        self.assertTrue(self.auth.confirm(owner, self.auth.callback_data(value)))
        return self.auth.complete(claim)

    def login(self, client=None, owner=USER):
        client = client or self.client
        token = self.approved(owner)
        client.set_cookie(COOKIE, token, domain="admin.example.test", secure=True)
        return token

    def post(self, path, values=None, client=None, token=None, origin=ORIGIN):
        client = client or self.client
        if token is None:
            cookie = client.get_cookie(COOKIE, domain="admin.example.test")
            token = cookie.value if cookie else ""
        data = dict(values or {}, csrf=self.auth.csrf(token))
        return client.post(path, data=data, base_url=ORIGIN, headers={"Origin": origin})

    def stats(self, row=None, **changes):
        row = row or self.row
        value = dict(revision=row["revision"], started_at=self.now - 100, regular_connected=True, encrypted_connected=True,
                     messages=7, deletions=2, attachments=5, files=3, media_bytes=123456,
                     conversations=2, run_messages=4, run_deletions=1, run_events=6)
        value.update(changes)
        return value


class PortalAuthTests(PortalBase):
    def test_claim_requires_signed_owner_approval_and_is_single_use(self):
        link = self.auth.issue(USER)
        claim = self.auth.claim(link)
        with self.assertRaises(DashboardError): self.auth.claim(link)
        with self.assertRaises(DashboardError): self.auth.complete(claim)
        value = self.auth.confirmations()[0]
        self.assertEqual(self.auth.confirmations(), [])
        data = self.auth.callback_data(value)
        self.assertLessEqual(len(data), 64)
        self.assertFalse(self.auth.confirm(OTHER, data))
        self.assertFalse(self.auth.confirm(USER, data[:-1] + ("a" if data[-1] != "a" else "b")))
        self.assertTrue(self.auth.confirm(USER, data))
        session = self.auth.complete(claim)
        self.assertEqual(self.auth.session(session)["id"], self.first)
        self.assertNotIn("cookies", self.auth.session(session))
        self.assertNotIn("auth_epoch", self.auth.session(session))
        with self.assertRaises(DashboardError): self.auth.complete(claim)
        self.assertFalse(self.auth.confirm(USER, data))
        with self.store.db() as db: dump = "\n".join(db.iterdump())
        for secret in (link, claim, session): self.assertNotIn(secret, dump)

    def test_rejection_expiry_replacement_and_pending_approval_cannot_authenticate(self):
        old = self.auth.issue(USER)
        new = self.auth.issue(USER)
        with self.assertRaises(DashboardError): self.auth.claim(old)
        claim = self.auth.claim(new)
        value = self.auth.confirmations()[0]
        self.assertTrue(self.auth.confirm(USER, self.auth.callback_data(value, False)))
        with self.assertRaises(DashboardError): self.auth.complete(claim)
        late = self.auth.issue(USER)
        self.now += 600
        with self.assertRaises(DashboardError): self.auth.claim(late)
        pending = self.store.add_account("Pending", COOKIES.replace("111222333", "777888999"))
        self.store.claim_invite(self.store.invite(pending).split("start=")[1], 33333333, "Pending")
        with self.assertRaises(DashboardError): self.auth.issue(33333333)
        with self.assertRaises(DashboardError): self.auth.issue(True)

    def test_concurrent_claim_and_completion_each_have_one_effect(self):
        link = self.auth.issue(USER)
        def claim(_):
            try: return self.auth.claim(link)
            except DashboardError: return None
        with ThreadPoolExecutor(max_workers=2) as pool: claims = list(pool.map(claim, range(2)))
        self.assertEqual(sum(value is not None for value in claims), 1)
        value = self.auth.confirmations()[0]
        self.auth.confirm(USER, self.auth.callback_data(value))
        def finish(_):
            try: return self.auth.complete(next(item for item in claims if item))
            except DashboardError: return None
        with ThreadPoolExecutor(max_workers=2) as pool: sessions = list(pool.map(finish, range(2)))
        self.assertEqual(sum(value is not None for value in sessions), 1)

    def test_idle_absolute_revocation_and_pairing_changes_invalidate_sessions(self):
        token = self.approved()
        self.now += IDLE_SECONDS
        self.assertIsNone(self.auth.session(token))
        token = self.approved()
        start = self.now
        for _ in range(15):
            self.now += 1700
            self.assertIsNotNone(self.auth.session(token))
        self.now = start + SESSION_SECONDS
        self.assertIsNone(self.auth.session(token))
        token = self.approved()
        self.store.user_control(USER, "pause")
        self.assertIsNotNone(self.auth.session(token))
        self.store.user_control(USER, "resume")
        self.store.update_cookies(self.first, COOKIES)
        self.assertIsNotNone(self.auth.session(token))
        self.store.action(self.first, "disconnect")
        self.assertIsNone(self.auth.session(token))

    def test_old_owner_mutations_cannot_change_a_new_pairing(self):
        token = self.approved()
        stale = self.auth.session(token)
        self.store.action(self.first, "disconnect")
        invitation = self.store.invite(self.first).split("start=")[1]
        self.store.claim_invite(invitation, 33333333, "New owner")
        self.store.action(self.first, "approve")
        fresh_token = self.approved(33333333)
        self.auth.revoke(self.first, pair_epoch=stale["pair_epoch"])
        self.assertIsNotNone(self.auth.session(fresh_token))
        with self.assertRaises(DashboardError): self.auth.update_profile(stale, "Old owner", "en")
        self.assertFalse(self.store.user_control(USER, "pause", expected=stale))
        self.auth.revoke(self.first)
        self.assertIsNone(self.auth.session(fresh_token))

    def test_profiles_are_validated_and_bound_to_pairing(self):
        token = self.approved()
        row = self.auth.session(token)
        self.auth.update_profile(row, "Żaneta <literal>", "en")
        self.assertEqual(self.auth.profile(row), "Żaneta <literal>")
        self.assertEqual(self.store.telegram_language(USER), "en")
        self.assertEqual(self.store.telegram_language(OTHER), "pl")
        for name, locale in (("", "pl"), ("x" * 81, "pl"), ("a\nb", "pl"), ("Name", "de"), ("Name", {})):
            with self.assertRaises(DashboardError): self.auth.update_profile(row, name, locale)


class PortalWebTests(PortalBase):
    def test_full_http_login_uses_independent_secure_cookies_and_requires_owner_confirmation(self):
        link = self.auth.issue(USER)
        self.assertEqual(self.client.get("/me", base_url=ORIGIN).status_code, 302)
        self.assertEqual(self.client.get("/me/login", base_url=ORIGIN).status_code, 200)
        self.assertIsNone(self.client.get_cookie("messenger_admin", domain="admin.example.test"))
        response = self.client.post("/me/api/claim", json={"token": link}, base_url=ORIGIN, headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 200)
        cookie = response.headers["Set-Cookie"]
        for flag in ("Secure", "HttpOnly", "SameSite=Strict", "Path=/"): self.assertIn(flag, cookie)
        self.assertNotIn("Domain=", cookie)
        state = self.client.get("/me/api/login-status", base_url=ORIGIN).json
        self.assertEqual(set(state), {"state", "code", "csrf"})
        self.assertEqual(state["state"], "pending")
        self.assertEqual(self.client.post("/me/api/complete", base_url=ORIGIN, headers={"Origin": ORIGIN, "X-Portal-CSRF": state["csrf"]}).status_code, 400)
        row = self.auth.confirmations()[0]
        self.auth.confirm(USER, self.auth.callback_data(row))
        self.assertEqual(self.client.get("/me/api/login-status", base_url=ORIGIN).json["owner"], USER)
        response = self.client.post("/me/api/complete", base_url=ORIGIN, headers={"Origin": ORIGIN, "X-Portal-CSRF": state["csrf"]})
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(self.client.get_cookie(COOKIE, domain="admin.example.test"))
        self.assertIsNone(self.client.get_cookie(CLAIM_COOKIE, domain="admin.example.test"))
        self.assertIsNone(self.client.get_cookie("messenger_admin", domain="admin.example.test"))
        self.assertEqual(self.client.get("/me", base_url=ORIGIN).status_code, 200)
        self.assertEqual(self.client.get("/accounts/" + self.second, base_url=ORIGIN).status_code, 302)

    def test_csrf_origin_and_other_session_tokens_cannot_authorize_actions(self):
        token = self.login()
        response = self.client.post("/me/settings", data={"mode": "all"}, base_url=ORIGIN, headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.post("/me/settings", {"mode": "all"}, origin="https://evil.test").status_code, 403)
        foreign = self.approved(OTHER)
        self.assertEqual(self.post("/me/settings", {"mode": "all"}, token=foreign).status_code, 403)
        self.assertEqual(self.store.account(self.first)["mode"], "deleted")
        self.assertEqual(self.client.post("/me/api/claim", json={"token": self.auth.issue(USER)}, base_url=ORIGIN, headers={"Origin": "https://evil.test"}).status_code, 403)
        self.assertEqual(self.client.get("/me", base_url=ORIGIN).headers["Cache-Control"], "no-store")

    def test_pages_show_only_owned_metadata_never_credentials_message_bodies_or_admin_controls(self):
        self.login()
        history = self.web.extensions["deliveries"]
        history.conversations.remember(self.row, "42", "My conversation")
        history.conversations.remember(self.other, "43", "OTHER_OWNER_PRIVATE_CHAT")
        for row, label in ((self.row, "My conversation"), (self.other, "OTHER_OWNER_PRIVATE_DELIVERY")):
            context = {"group": row["id"], "thread": "42", "name": label, "kind": "deleted", "expires": self.now + 600}
            ident = history.begin(row, "sendMessage", context)
            history.save(row, ident, "sendMessage", {"chat_id": row["chat_id"], "text": "PRIVATE_MESSAGE_BODY"})
            history.finish(row, ident, 200, {"ok": True})
        for path in ("/me", "/me/settings", "/me/profile", "/me/conversations", "/me/deliveries"):
            response = self.client.get(path, base_url=ORIGIN)
            self.assertEqual(response.status_code, 200, path)
            for secret in ("OTHER_OWNER_PRIVATE", "PRIVATE_MESSAGE_BODY", "SYNTHETIC_XS_PRIVATE", "SYNTHETIC_DATR_PRIVATE", ">Administrator<", "/accounts/new", "/bot", "/setup-links"):
                self.assertNotIn(secret.encode(), response.data, path)
        self.assertIn(b"My conversation", self.client.get("/me/conversations", base_url=ORIGIN).data)
        self.assertIn(b"My conversation", self.client.get("/me/deliveries", base_url=ORIGIN).data)

    def test_preferences_profile_pause_resume_and_quiet_are_per_user_and_persist(self):
        self.login()
        revision = self.store.account(self.first)["revision"]
        self.assertEqual(self.post("/me/settings", {"mode": "all", "media": "on", "view_once": "on", "account_id": self.second, "role": "admin"}).status_code, 302)
        self.assertEqual(self.store.account(self.first)["mode"], "all")
        self.assertTrue(self.store.account(self.first)["view_once"])
        self.assertEqual(self.store.account(self.first)["revision"], revision)
        self.assertEqual(self.store.account(self.second)["mode"], "deleted")
        self.assertEqual(self.post("/me/quiet", {"duration": "3600"}).status_code, 302)
        self.assertEqual(self.store.controls(self.store.account(self.first))["quiet_until"], self.now + 3600)
        self.assertEqual(self.post("/me/profile", {"name": "Ada <script>", "language": "en"}).status_code, 302)
        body = self.client.get("/me/profile", base_url=ORIGIN).data
        self.assertIn(b"Ada &lt;script&gt;", body)
        self.assertIn(b"Your profile", body)
        self.assertNotIn(b"<script>Ada", body)
        self.assertEqual(self.store.account(self.first)["name"], "Synthetic account")
        self.assertEqual(self.store.telegram_language(OTHER), "pl")
        for action, enabled in (("pause", 0), ("resume", 1)):
            self.assertEqual(self.post("/me/capture", {"action": action}).status_code, 302)
            self.assertEqual(self.store.account(self.first)["desired"], enabled)
            self.assertEqual(self.client.get("/me", base_url=ORIGIN).status_code, 200)

    def test_foreign_conversation_and_delivery_ids_and_resend_confirmations_are_denied(self):
        self.login()
        history = self.web.extensions["deliveries"]
        history.conversations.remember(self.other, "43", "Foreign")
        chat = history.conversations.rows(self.other)[0]
        response = self.post("/me/conversations", {"action": "rule", "conversation": chat["id"], "capture": "exclude", "forward": "off"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.store.filters(self.second)["rules"], {})
        ident = history.begin(self.other, "sendMessage", {"group": self.second, "thread": "43", "name": "Foreign", "kind": "deleted", "expires": self.now + 600})
        history.save(self.other, ident, "sendMessage", {"chat_id": OTHER, "text": "PRIVATE"})
        history.finish(self.other, ident, 200, {"ok": True})
        token = history.intent(self.other, ident)
        self.assertEqual(self.client.get("/me/deliveries/" + ident + "/resend", base_url=ORIGIN).status_code, 404)
        self.assertEqual(self.post("/me/deliveries/" + ident + "/resend", {"confirmation": token, "acknowledge": "yes"}).status_code, 404)
        self.assertEqual(self.api.calls, [])

    def test_logout_all_and_administrator_revocation_preserve_capture_and_admin_login(self):
        self.store.set_password(PASSWORD)
        with self.client.session_transaction(base_url=ORIGIN) as session:
            session.update(sid=self.store.login(PASSWORD, "test"), csrf="admin-csrf")
        self.login()
        other_browser = self.web.test_client(); self.login(other_browser)
        self.assertEqual(self.post("/me/sessions/revoke").status_code, 302)
        self.assertEqual(other_browser.get("/me", base_url=ORIGIN).status_code, 302)
        self.assertTrue(self.store.account(self.first)["desired"])
        self.assertEqual(self.client.get("/", base_url=ORIGIN).status_code, 200)
        self.login(other_browser)
        response = self.client.post("/accounts/" + self.first + "/portal/revoke", data={"csrf": "admin-csrf"}, base_url=ORIGIN, headers={"Origin": ORIGIN})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(other_browser.get("/me", base_url=ORIGIN).status_code, 302)
        self.assertTrue(self.store.account(self.first)["desired"])


class PortalStatsTests(PortalBase):
    def test_owned_strict_reports_and_fresh_stale_and_paused_views(self):
        self.login()
        self.store.report_worker(self.row, {"revision": self.row["revision"], "connected": True, "last_event": self.now})
        self.store.report_stats(self.row, self.stats())
        body = self.client.get("/me", base_url=ORIGIN).data.decode()
        self.assertIn("Połączono", body)
        self.assertIn("Zachowane wiadomości", body)
        self.assertNotIn(TEXT["pl"]["stats_stale"], body)
        self.now += 91
        self.assertIn(TEXT["pl"]["stats_stale"], self.client.get("/me", base_url=ORIGIN).data.decode())
        self.store.user_control(USER, "pause")
        self.assertIn("Wstrzymano", self.client.get("/me", base_url=ORIGIN).data.decode())
        with self.assertRaises(DashboardError): self.store.report_stats(self.row, self.stats())
        self.now += 259200
        self.login()
        self.assertIn(TEXT["pl"]["stats_missing"], self.client.get("/me", base_url=ORIGIN).data.decode())

    def test_invalid_or_cross_revision_reports_cannot_poison_statistics(self):
        value = self.stats()
        for changes in ({"revision": 1}, {"messages": -1}, {"files": 20}, {"deletions": 100}, {"media_bytes": True}, {"started_at": float("nan")}, {"text": "SECRET"}, {"regular_connected": "yes"}):
            bad = dict(value, **changes)
            with self.assertRaises(DashboardError): self.store.report_stats(self.row, bad)
        api = broker_app(self.store, self.api).test_client()
        headers = {"Authorization": "Bearer " + self.store.worker_key(self.row)}
        self.assertEqual(api.post("/telegram/reportStats", json=value, headers=headers).status_code, 200)
        self.assertIsNone(self.store.setting("worker_stats:" + self.second))
        self.assertEqual(api.post("/telegram/reportStats", json=self.stats(self.other), headers=headers).status_code, 200)
        self.assertEqual(api.post("/telegram/reportStats", json=dict(value, account_id=self.second), headers=headers).status_code, 403)


class PortalBotTests(PortalBase):
    def test_commands_and_owner_signed_callbacks_are_localized_and_validated(self):
        receiver = PairingReceiver(self.store, self.api, origin=ORIGIN)
        def command(sequence, value, owner=USER):
            receiver.handle({"update_id": sequence, "message": {"text": value, "chat": {"id": owner, "type": "private"}, "from": {"id": owner, "is_bot": False}}})
            return json.loads(self.api.calls[-1][1])["text"]
        self.assertIn("prywatny panel", command(1, "/panel"))
        self.store.set_telegram_language(USER, "en")
        reply = command(2, "/start dashboard")
        self.assertIn("private Messenger dashboard", reply)
        token = re.search(r"#([A-Za-z0-9_-]{43})", reply)[1]
        claim = self.auth.claim(token)
        receiver.maintenance()
        sent = json.loads(self.api.calls[-1][1])
        self.assertIn(self.auth.claim_row(claim)["code"], sent["text"])
        data = sent["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        callback = {"id": "login_confirm", "data": data, "from": {"id": USER, "is_bot": False}, "message": {"message_id": 7, "chat": {"id": USER, "type": "private"}, "from": {"id": BOT["id"], "is_bot": True}}}
        for change in ({"from": {"id": OTHER, "is_bot": False}}, {"from": {"id": USER, "is_bot": True}}, {"message": {"message_id": 7, "chat": {"id": USER, "type": "group"}, "from": {"id": BOT["id"], "is_bot": True}}}):
            receiver.portal.callback(dict(callback, **change))
            self.assertEqual(self.auth.claim_row(claim)["state"], "pending")
        receiver.handle({"update_id": 3, "callback_query": callback})
        self.assertEqual(self.auth.claim_row(claim)["state"], "approved")
        self.assertIn("Approved", json.loads(self.api.calls[-1][1])["text"])

    def test_uncertain_confirmation_is_claimed_once_and_owner_must_request_a_new_link(self):
        receiver = PairingReceiver(self.store, self.api, origin=ORIGIN)
        self.auth.claim(self.auth.issue(USER))
        self.api.error = True
        receiver.maintenance(); receiver.maintenance()
        self.assertEqual(len(self.api.calls), 1)
        self.api.error = False
        receiver.maintenance()
        self.assertEqual(len(self.api.calls), 1)

    def test_every_interface_key_is_translated(self):
        self.assertEqual(set(TEXT["en"]), set(TEXT["pl"]))
