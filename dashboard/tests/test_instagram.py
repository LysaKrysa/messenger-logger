"""Instagram pairing and ownership boundaries with synthetic session data."""
import json
import sqlite3

from dashboard.broker import PairingReceiver
from dashboard.portal_auth import PortalAuth
from dashboard.store import DashboardError, Store
from dashboard.tests.test_dashboard import Base, FakeAPI, ORIGIN

OWNER = 87654321
COOKIES = json.dumps({"ds_user_id": "111222333", "sessionid": "SYNTHETIC_IG_SESSION", "csrftoken": "SYNTHETIC_CSRF"})


class InstagramTests(Base):
    def setUp(self):
        super().setUp()
        self.fb = self.paired()
        self.ig = self.store.add_account("Instagram pilot", COOKIES, platform="instagram")

    def pair_instagram(self):
        link = self.store.invite(self.ig)
        self.assertTrue(self.store.claim_invite(link.split("start=")[1], OWNER, "Pilot"))
        self.store.action(self.ig, "approve")
        self.store.action(self.ig, "start")

    def test_pairing_same_owner_is_platform_scoped_and_cookies_keep_identity(self):
        self.pair_instagram()
        self.assertEqual(self.store.user_account(OWNER)["id"], self.fb)
        self.assertEqual(self.store.user_account(OWNER, platform="instagram")["id"], self.ig)
        with self.assertRaises(DashboardError):
            self.store.add_account("Duplicate Instagram", COOKIES, platform="instagram")
        other = self.store.add_account("Another Instagram", COOKIES.replace("111222333", "111222334"), platform="instagram")
        self.assertFalse(self.store.claim_invite(self.store.invite(other).split("start=")[1], OWNER, "Pilot"))
        with self.assertRaises(DashboardError):
            self.store.update_cookies(self.ig, COOKIES.replace("111222333", "111222334"))
        previous = self.store.account(self.ig, private=True)
        self.store.update_cookies(self.ig, COOKIES.replace("SYNTHETIC_IG_SESSION", "REFRESHED_SYNTHETIC_IG"))
        self.assertIsNone(self.store.authenticate_worker(self.store.worker_key(previous)))
        self.assertTrue(self.store.account(self.ig)["desired"])
        self.assertEqual(self.store.account(self.ig)["pair_epoch"], previous["pair_epoch"])

    def test_bot_commands_and_confirmed_portal_target_instagram(self):
        self.pair_instagram()
        api = FakeAPI()
        receiver = PairingReceiver(self.store, api, origin=ORIGIN)
        sender = {"id": OWNER, "is_bot": False}
        chat = {"id": OWNER, "type": "private"}
        for index, command in enumerate(("/pauza instagram", "/stan instagram", "/panel instagram"), 1):
            receiver.handle({"update_id": index, "message": {"chat": chat, "from": sender, "text": command}})
        self.assertFalse(self.store.account(self.ig)["desired"])
        self.assertTrue(self.store.account(self.fb)["desired"])
        self.assertIn("Instagram", json.loads(api.calls[-2][1])["text"])
        auth = PortalAuth(self.store)
        token = json.loads(api.calls[-1][1])["text"].rsplit("#", 1)[1].split()[0]
        claim = auth.claim(token)
        request = auth.confirmations()[0]
        self.assertTrue(auth.confirm(OWNER, auth.callback_data(request)))
        session = auth.session(auth.complete(claim))
        self.assertEqual(session["id"], self.ig)
        self.assertNotIn("cookies", session)
        self.assertTrue(self.store.user_control(OWNER, "mode", "all", expected=session))
        self.assertEqual(self.store.account(self.ig)["mode"], "all")
        self.assertEqual(self.store.account(self.fb)["mode"], "deleted")

    def test_instagram_conversation_menu_stays_bound_to_current_pairing(self):
        self.pair_instagram()
        api = FakeAPI()
        receiver = PairingReceiver(self.store, api, origin=ORIGIN)
        _, markup = receiver.history.menu(OWNER, "chats", "en", platform="instagram")
        button = markup["inline_keyboard"][0][1]["callback_data"]
        query = {"id": "synthetic", "data": button, "from": {"id": OWNER, "is_bot": False},
                 "message": {"chat": {"id": OWNER, "type": "private"}, "message_id": 2,
                             "from": {"id": 123456, "is_bot": True}}}
        receiver.history.callback(query)
        self.assertFalse(self.store.filters(self.ig)["capture_default"])
        self.assertTrue(self.store.filters(self.fb)["capture_default"])
        self.store.disconnect_user(OWNER, platform="instagram")
        self.pair_instagram()
        receiver.history.callback(query)
        self.assertTrue(self.store.filters(self.ig)["capture_default"])

    def test_old_account_schema_keeps_existing_credentials_and_pairing(self):
        root = self.root / "old"
        root.mkdir()
        original = self.store.account(self.fb, private=True)
        legacy = sqlite3.connect(root / "dashboard.sqlite3")
        try:
            with legacy:
                legacy.execute('''CREATE TABLE accounts(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, cookies TEXT, facebook_id TEXT UNIQUE,
                    chat_id INTEGER UNIQUE, pending_chat INTEGER UNIQUE, pending_name TEXT, desired INTEGER,
                    revision INTEGER, auth_epoch INTEGER, mode TEXT, view_once INTEGER, created REAL, pair_epoch INTEGER
                )''')
                columns = [row[1] for row in legacy.execute("PRAGMA table_info(accounts)")]
                legacy.execute("INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               [original[key] for key in columns])
        finally:
            legacy.close()
        migrated = Store(root, self.store.key)
        self.assertEqual(migrated.account(self.fb, private=True), original)
        self.assertEqual(migrated.user_account(OWNER)["id"], self.fb)
        self.assertIsNotNone(migrated.authenticate_worker(self.store.worker_key(original)))
        self.assertEqual(Store(root, self.store.key).account(self.fb, private=True), original)
