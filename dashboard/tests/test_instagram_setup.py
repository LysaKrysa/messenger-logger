"""Exercise the Instagram guest flow with synthetic browser and Telegram data."""
from concurrent.futures import ThreadPoolExecutor
import json
import re

from dashboard.broker import PairingReceiver
from dashboard.setup_store import InstagramLinkUnavailable
from dashboard.setup_web import INSTAGRAM_COOKIE
from dashboard.store import DashboardError
from dashboard.tests.test_dashboard import Base, FakeAPI, ORIGIN
from dashboard.tests.test_setup import FakeBrowser
from dashboard.web import create_app

OWNER, OTHER = 87654321, 34567890
COOKIES = {"ds_user_id": "44332211", "sessionid": "SYNTHETIC_INSTAGRAM_SESSION",
           "csrftoken": "SYNTHETIC_INSTAGRAM_CSRF", "rur": r'"SYNTHETIC\054ROUTING"'}


class InstagramBrowser(FakeBrowser):
    def call(self, ident, action, data=None):
        result = super().call(ident, action, data)
        return {"cookies": dict(COOKIES)} if action == "capture" else result


class InstagramSetupTests(Base):
    def setUp(self):
        super().setUp()
        self.messenger = self.paired()
        self.api = FakeAPI()
        self.receiver = PairingReceiver(self.store, self.api, origin=ORIGIN)
        self.browser = InstagramBrowser()
        self.app = create_app(self.store, origin=ORIGIN, browser_client=self.browser, delivery_api=self.api, testing=True)
        self.invites = self.app.extensions["setup_invites"]
        self.client = self.app.test_client()

    def update(self, **payload):
        self.receiver.handle(dict(payload, update_id=self.store.setting("bot_offset", -1) + 1))

    def command(self, owner=OWNER, kind="private"):
        self.update(message={"text": "/instagram", "chat": {"id": owner, "type": kind},
                             "from": {"id": owner, "is_bot": False}})
        return json.loads(self.api.calls[-1][1]) if self.api.calls else {}

    def claim(self, token, *, path="/instagram", client=None):
        return (client or self.client).post(path + "/api/claim", base_url=ORIGIN,
            headers={"Origin": ORIGIN}, json={"token": token, "consent": True, "name": "Untrusted label"})

    def post(self, action, state, body=None, *, origin=ORIGIN):
        return self.client.post("/instagram/api/" + action, base_url=ORIGIN,
            headers={"Origin": origin, "X-Setup-CSRF": state["csrf"]}, json=body or {})

    def confirmation(self, state):
        self.receiver.connection_alerts.confirmations()
        message = json.loads(self.api.calls[-1][1])
        self.assertEqual(message["chat_id"], OWNER)
        self.assertIn(state["confirmation_code"], message["text"])
        return message["reply_markup"]["inline_keyboard"][0][0]["callback_data"]

    def callback(self, data, *, owner=OWNER, bot_id=123456, kind="private"):
        self.update(callback_query={"id": "synthetic", "data": data, "from": {"id": owner, "is_bot": False},
            "message": {"chat": {"id": owner, "type": kind}, "from": {"id": bot_id, "is_bot": True}}})

    def test_command_requires_approved_pairing_and_replaces_links(self):
        self.assertEqual(self.command(kind="supergroup"), {})
        self.assertIn("zatwierdzonego", self.command(OTHER)["text"])
        self.store.set_telegram_language(OWNER, "en")
        first = self.command()
        self.assertTrue(first["link_preview_options"]["is_disabled"])
        old = re.search(r"/instagram#([A-Za-z0-9_-]{43})", first["text"])[1]
        self.assertIn("expires in 15 minutes", first["text"])
        self.assertIn("/instagram#", self.command()["text"])
        self.assertEqual(self.claim(old).status_code, 400)
        self.assertIn("/instagram#", self.command()["text"])
        self.assertIn("three Instagram links per hour", self.command()["text"])
        self.assertEqual(sum(row["active"] for row in self.invites.list()), 1)

    def test_single_claim_and_confirmation_gate_with_separate_guest_cookie(self):
        _, token = self.invites.create_instagram(OWNER)
        self.assertEqual(self.client.get("/instagram#" + token, base_url=ORIGIN).status_code, 200)
        self.assertEqual(self.claim(token, path="/setup").status_code, 400)
        state = self.claim(token).json
        self.assertRegex(state["confirmation_code"], r"^[0-9]{6}$")
        self.assertEqual(self.claim(token, client=self.app.test_client()).status_code, 400)
        cookie = self.client.get_cookie(INSTAGRAM_COOKIE, domain="admin.example.test")
        self.assertTrue(cookie.secure and cookie.http_only)
        self.assertEqual(cookie.same_site, "Strict")
        self.assertEqual(self.client.get("/setup/api/status", base_url=ORIGIN).status_code, 401)
        for action in ("start", "input", "frame", "view", "finish"):
            self.assertEqual(self.post(action, state, {"approve": True}).status_code, 400)
        self.assertEqual(self.client.get("/instagram/api/stream", base_url=ORIGIN).status_code, 400)
        self.assertEqual(self.browser.calls, [])
        data = self.confirmation(state)
        sent = len(self.api.calls)
        self.receiver.connection_alerts.confirmations()
        self.assertEqual(len(self.api.calls), sent)
        for options in ({"owner": OTHER}, {"bot_id": 444}, {"kind": "supergroup"}):
            self.callback(data, **options)
            self.assertEqual(self.post("start", state).status_code, 400)
        self.callback(data[:-16] + "0" * 16)
        self.assertEqual(self.post("start", state).status_code, 400)
        self.callback(data)
        self.assertEqual(self.post("start", state, origin="https://foreign.example").status_code, 403)
        self.assertEqual(self.post("start", state, {"platform": "facebook", "mobile": True}).status_code, 200)
        self.assertEqual(self.browser.calls[-1][2]["platform"], "instagram")
        self.assertTrue(self.browser.calls[-1][2]["mobile"])

    def test_finish_uses_browser_session_and_existing_chat_once(self):
        original = self.store.account(self.messenger, private=True)
        _, token = self.invites.create_instagram(OWNER)
        state = self.claim(token).json
        self.callback(self.confirmation(state))
        self.post("start", state)
        finish = self.post("finish", state, {"approve": True, "chat_id": OTHER, "cookies": {"ds_user_id": "12345"}})
        self.assertEqual(finish.status_code, 200)
        self.assertEqual(finish.json["pairing"], "instagram_started")
        self.assertNotIn("?start=", finish.json["telegram_url"])
        self.assertNotIn(COOKIES["sessionid"], finish.text)
        self.assertEqual(self.browser.calls[-1][1], "stop")
        instagram = self.store.user_account(OWNER, platform="instagram")
        self.assertEqual(instagram["facebook_id"], COOKIES["ds_user_id"])
        self.assertTrue(instagram["desired"])
        self.assertIsNone(self.store.user_account(OTHER, platform="instagram"))
        self.assertEqual(self.store.account(self.messenger, private=True), original)
        raw = self.store.account(instagram["id"], private=True)
        self.assertEqual(self.store.unseal("cookies:" + instagram["id"], raw["cookies"]),
                         {key: value for key, value in COOKIES.items() if key != "rur"})
        with self.store.db() as db:
            self.assertNotIn(COOKIES["sessionid"], "\n".join(db.iterdump()))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM invites").fetchone()[0], 0)
        self.assertEqual(self.post("finish", state, {"approve": True}).status_code, 200)
        self.assertEqual(len(self.store.accounts()), 2)
        with self.assertRaisesRegex(InstagramLinkUnavailable, "already_paired"):
            self.invites.create_instagram(OWNER)
        self.store.disconnect_user(OWNER, platform="instagram")
        with self.assertRaisesRegex(InstagramLinkUnavailable, "slot_in_use"):
            self.invites.create_instagram(OWNER)
        self.store.action(instagram["id"], "remove")
        self.invites.create_instagram(OWNER)

    def test_concurrent_completion_is_idempotent_and_consumes_one_reserved_slot(self):
        self.store.max_accounts = 2
        _, token = self.invites.create_instagram(OWNER)
        with self.assertRaises(DashboardError):
            self.store.add_account("Slot already reserved")
        with self.assertRaises(DashboardError):
            self.invites.create()
        session = self.invites.claim(token, "", instagram=True)
        row = self.invites.confirmations()[0]
        self.assertEqual(self.invites.confirm(OWNER, self.invites.confirmation_data(row)), "approved")
        with ThreadPoolExecutor(2) as workers:
            ids = list(workers.map(lambda _: self.invites.complete(session, COOKIES), range(2)))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(len(self.store.accounts()), 2)

    def test_owner_unlink_revokes_confirmed_session_and_closes_browser(self):
        ident, token = self.invites.create_instagram(OWNER)
        state = self.claim(token).json
        self.callback(self.confirmation(state))
        self.assertEqual(self.post("start", state).status_code, 200)
        self.store.action(self.messenger, "disconnect")
        self.assertEqual(self.post("finish", state, {"approve": True}).status_code, 401)
        self.assertEqual(self.invites.list()[0]["state"], "revoked")
        self.assertEqual(self.invites.browser_stops(), [ident])
        self.assertEqual(len(self.store.accounts()), 1)

    def test_expired_links_and_existing_manual_pairing_prevent_setup(self):
        _, token = self.invites.create_instagram(OWNER)
        self.now += 901
        self.assertEqual(self.claim(token).status_code, 400)
        _, token = self.invites.create_instagram(OWNER)
        ident = self.store.add_account("Already paired", json.dumps({k: v for k, v in COOKIES.items() if k != "rur"}),
                                       platform="instagram")
        invite = self.store.invite(ident).split("start=")[1]
        self.assertTrue(self.store.claim_invite(invite, OWNER, "Existing Telegram owner"))
        self.assertEqual(self.claim(token).status_code, 400)
        with self.assertRaisesRegex(InstagramLinkUnavailable, "already_paired"):
            self.invites.create_instagram(OWNER)
