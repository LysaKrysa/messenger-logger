"""Explicit administrator credential editing with synthetic sessions only."""
import html
import json
import re

from dashboard.portal_web import COOKIE
from dashboard.store import DashboardError
from dashboard.tests.test_dashboard import Base, COOKIES, ORIGIN, PASSWORD
from dashboard.web import create_app


class CookieEditorTests(Base):
    def setUp(self):
        super().setUp()
        self.first = self.paired()
        self.second = self.paired(22222222, COOKIES.replace("111222333", "444555666"))
        self.store.set_password(PASSWORD)
        self.app = create_app(self.store, origin=ORIGIN, testing=True)
        self.client = self.app.test_client()
        with self.client.session_transaction(base_url=ORIGIN) as session:
            session.update(sid=self.store.login(PASSWORD, "synthetic"), csrf="admin-csrf")

    def url(self, ident=None):
        return "/accounts/" + (ident or self.first) + "/cookies/edit"

    def post(self, data, client=None, origin=ORIGIN, csrf="admin-csrf", ident=None):
        return (client or self.client).post(self.url(ident), data=dict(data, csrf=csrf), base_url=ORIGIN, headers={"Origin": origin})

    def editor(self):
        response = self.post({"action": "load"})
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        raw = html.unescape(re.search(r'<textarea[^>]*>(.*?)</textarea>', page, re.S)[1])
        stamp = re.search(r'name="stamp" value="([a-f0-9]{64})"', page)[1]
        return response, raw, stamp

    def test_normal_pages_hide_credentials_and_only_explicit_post_loads_private_editor(self):
        for path in ("/", "/accounts/" + self.first, "/accounts/" + self.second):
            self.assertNotIn(b"SYNTHETIC_XS_PRIVATE", self.client.get(path, base_url=ORIGIN).data)
        self.assertEqual(self.client.get(self.url(), base_url=ORIGIN).status_code, 405)
        response, raw, _ = self.editor()
        self.assertEqual(json.loads(raw), json.loads(COOKIES))
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertIn("noarchive", response.headers["X-Robots-Tag"])

    def test_owner_portal_and_anonymous_sessions_cannot_load_or_save_credentials(self):
        user = self.app.test_client()
        auth = self.app.extensions["portal_auth"]
        claim = auth.claim(auth.issue(87654321))
        row = auth.confirmations()[0]
        self.assertTrue(auth.confirm(87654321, auth.callback_data(row)))
        token = auth.complete(claim)
        user.set_cookie(COOKIE, token, domain="admin.example.test", secure=True)
        for client in (user, self.app.test_client()):
            for data in ({"action": "load"}, {"action": "save", "cookies": COOKIES, "stamp": "a" * 64}):
                response = self.post(data, client=client, csrf=auth.csrf(token))
                self.assertIn(response.status_code, {302, 403})
                self.assertNotIn(b"SYNTHETIC_XS_PRIVATE", response.data)
        self.assertNotIn(b"SYNTHETIC_XS_PRIVATE", user.get("/me/profile", base_url=ORIGIN).data)

    def test_csrf_origin_and_missing_snapshot_cannot_reveal_or_change_cookies(self):
        for kwargs in ({"csrf": "wrong"}, {"origin": "https://evil.test"}):
            response = self.post({"action": "load"}, **kwargs)
            self.assertEqual(response.status_code, 403)
            self.assertNotIn(b"SYNTHETIC_XS_PRIVATE", response.data)
        for data in ({"action": "save", "cookies": COOKIES}, {"action": "other"}):
            self.assertEqual(self.post(data).status_code, 400)
        self.assertEqual(self.store.account(self.first)["revision"], 3)

    def test_save_encrypts_edits_rotates_worker_key_and_preserves_pairing_and_other_accounts(self):
        before = self.store.account(self.first, private=True)
        other = self.store.account(self.second, private=True)
        _, raw, stamp = self.editor()
        value = json.loads(raw); value["xs"] = "EDITED_COOKIE_SECRET"; value["fr"] = "ADDITIONAL_COOKIE_SECRET"
        response = self.post({"action": "save", "cookies": json.dumps(value), "stamp": stamp})
        self.assertEqual(response.status_code, 302)
        after = self.store.account(self.first, private=True)
        self.assertEqual(self.store.unseal("cookies:" + self.first, after["cookies"]), value)
        self.assertEqual(after["revision"], before["revision"] + 1)
        self.assertEqual(after["auth_epoch"], before["auth_epoch"] + 1)
        self.assertEqual(after["pair_epoch"], before["pair_epoch"])
        self.assertEqual(after["chat_id"], before["chat_id"])
        self.assertTrue(after["desired"])
        self.assertEqual(self.store.account(self.second, private=True), other)
        self.assertIsNone(self.store.authenticate_worker(self.store.worker_key(before)))
        self.assertIsNotNone(self.store.authenticate_worker(self.store.worker_key(after)))
        with self.store.db() as db: dump = "\n".join(db.iterdump())
        self.assertNotIn("EDITED_COOKIE_SECRET", dump)
        self.assertNotIn("ADDITIONAL_COOKIE_SECRET", dump)
        self.assertNotIn(b"EDITED_COOKIE_SECRET", self.client.get(response.location, base_url=ORIGIN).data)
        # The same snapshot cannot overwrite a later save.
        self.assertEqual(self.post({"action": "save", "cookies": raw, "stamp": stamp}).status_code, 400)

    def test_refreshed_cookie_or_reassigned_pairing_blocks_stale_edit(self):
        _, raw, stamp = self.editor()
        refreshed = json.loads(raw); refreshed["xs"] = "REFRESHED_COOKIE_SECRET"
        self.store.update_cookies(self.first, json.dumps(refreshed))
        response = self.post({"action": "save", "cookies": raw, "stamp": stamp})
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Reload saved cookies", response.data)
        self.assertEqual(self.store.unseal("cookies:" + self.first, self.store.account(self.first, private=True)["cookies"]), refreshed)
        _, _, stamp = self.editor()
        self.store.action(self.first, "disconnect")
        self.assertEqual(self.post({"action": "save", "cookies": raw, "stamp": stamp}).status_code, 400)

    def test_invalid_json_or_other_identity_is_rejected_without_erasing_edits_or_stored_session(self):
        _, raw, stamp = self.editor()
        before = self.store.account(self.first, private=True)
        for candidate in ("not json <script>", COOKIES.replace("111222333", "444555666")):
            response = self.post({"action": "save", "cookies": candidate, "stamp": stamp})
            self.assertEqual(response.status_code, 400)
            self.assertNotIn(b"<script>", response.data)
            self.assertEqual(self.store.account(self.first, private=True), before)
        # Snapshot signatures are account-bound, including identical cookie values.
        self.assertEqual(self.post({"action": "save", "cookies": raw, "stamp": stamp}, ident=self.second).status_code, 400)

    def test_paused_account_stays_paused_and_unrelated_preferences_do_not_invalidate_editor(self):
        self.store.user_control(87654321, "pause")
        _, raw, stamp = self.editor()
        self.store.user_control(87654321, "mode", "all")
        self.assertEqual(self.post({"action": "save", "cookies": raw, "stamp": stamp}).status_code, 302)
        row = self.store.account(self.first)
        self.assertFalse(row["desired"])
        self.assertEqual(row["mode"], "all")

    def test_empty_missing_or_invalid_stored_credentials_fail_without_plaintext_details(self):
        empty = self.store.add_account("Empty")
        self.assertEqual(self.post({"action": "load"}, ident=empty).status_code, 400)
        self.assertEqual(self.post({"action": "load"}, ident="a" * 32).status_code, 404)
        with self.store.db() as db:
            db.execute("UPDATE accounts SET cookies='INVALID_ENCRYPTED_VALUE' WHERE id=?", (self.first,))
        response = self.post({"action": "load"})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(b"INVALID_ENCRYPTED_VALUE", response.data)

    def test_stored_html_like_values_stay_escaped_and_preserve_literal_cookie_content(self):
        value = json.loads(COOKIES)
        value["fr"] = '</textarea><script>window.COOKIE_INJECTED=true</script>'
        self.store.update_cookies(self.first, json.dumps(value))
        response, raw, _ = self.editor()
        self.assertEqual(json.loads(raw), value)
        self.assertNotIn(b"</textarea><script>", response.data)
