"""Per-person bot preferences and live relay isolation; no real Telegram sends."""
import io
import json
import os
from unittest.mock import patch

from dashboard.broker import PairingReceiver, DOWNLOAD_CALLBACK, create_app
from dashboard.store import Store, DashboardError
from dashboard.tests.test_dashboard import Base, FakeAPI, BOT, COOKIES
from event_store import EventStore
from telegram_relay import TelegramRelay


class LanguageTests(Base):
    def setUp(self):
        super().setUp()
        self.first = self.paired()
        self.second = self.paired(22222222, COOKIES.replace("111222333", "444555666"))
        self.api = FakeAPI()
        self.receiver = PairingReceiver(self.store, self.api)
        self.client = create_app(self.store, self.api).test_client()

    def command(self, text, user=87654321, update_id=10, **changes):
        value = {"update_id": update_id, "message": {
            "chat": {"id": user, "type": "private"}, "from": {"id": user, "is_bot": False, "language_code": "en"},
            "message_thread_id": 7, "text": text}}
        value["message"].update(changes)
        self.receiver.handle(value)

    def preference(self, ident, data=None):
        key = self.store.worker_key(self.store.account(ident, private=True))
        return self.client.post("/telegram/getLocale", json={} if data is None else data,
                                headers={"Authorization": "Bearer " + key})

    def test_polish_default_commands_are_per_user_and_persist_without_worker_restart(self):
        before = {row["id"]: row["revision"] for row in self.store.accounts()}
        self.assertEqual(self.store.telegram_language(87654321), "pl")
        self.command("/en")
        self.assertEqual(self.store.telegram_language(87654321), "en")
        self.assertEqual(self.store.telegram_language(22222222), "pl")
        reply = json.loads(self.api.calls[-1][1])
        self.assertIn("Language set to English", reply["text"])
        self.assertEqual(reply["chat_id"], 87654321)
        self.assertEqual(reply["message_thread_id"], 7)
        restarted = Store(self.store.root, self.store.key, clock=lambda: self.now)
        self.assertEqual(restarted.telegram_language(87654321), "en")
        self.command("/pl", update_id=11)
        self.assertIn("Ustawiono język polski", json.loads(self.api.calls[-1][1])["text"])
        self.assertEqual(self.store.telegram_language(87654321), "pl")
        self.assertEqual(before, {row["id"]: row["revision"] for row in self.store.accounts()})

    def test_invalid_group_sender_address_and_target_arguments_cannot_change_preferences(self):
        cases = [("/en 22222222", {}), ("/en@another_bot", {}),
                 ("/en", {"chat": {"id": 87654321, "type": "supergroup"}}),
                 ("/en", {"from": {"id": 22222222, "is_bot": False}}),
                 ("/en", {"from": {"id": 87654321, "is_bot": True}})]
        for index, (text, changes) in enumerate(cases, 10):
            self.now += 61
            self.command(text, update_id=index, **changes)
        self.assertEqual(self.store.telegram_language(87654321), "pl")
        self.assertEqual(self.store.telegram_language(22222222), "pl")
        self.command("/en@" + BOT["username"], update_id=99)
        self.assertEqual(self.store.telegram_language(87654321), "en")

    def test_choice_is_available_before_pairing_and_survives_disconnect(self):
        self.command("/en", user=33333333)
        self.command("/help", user=33333333, update_id=11)
        self.assertIn("/en — English", json.loads(self.api.calls[-1][1])["text"])
        self.command("/privacy", user=22222222, update_id=12)
        self.assertIn("Ten bot przesyła", json.loads(self.api.calls[-1][1])["text"])
        self.command("/en", update_id=13)
        self.command("/disconnect", update_id=14)
        self.assertIn("Telegram pairing removed", json.loads(self.api.calls[-1][1])["text"])
        self.assertEqual(self.store.telegram_language(87654321), "en")

    def test_locale_lookup_is_owned_read_only_and_never_calls_telegram(self):
        self.store.set_telegram_language(87654321, "en")
        self.assertEqual(self.preference(self.first).json, {"ok": True, "result": {"language": "en"}})
        self.assertEqual(self.preference(self.second).json, {"ok": True, "result": {"language": "pl"}})
        self.assertEqual(self.api.calls, [])
        for params in ({"chat_id": 87654321}, {"language": "en"}, {"other": "x" * 500}):
            self.assertEqual(self.preference(self.second, params).status_code, 403)
        self.assertEqual(self.store.telegram_language(22222222), "pl")
        self.assertEqual(self.client.post("/telegram/getLocale", json={}).status_code, 403)
        key = self.store.worker_key(self.store.account(self.first, private=True))
        self.store.action(self.first, "disconnect")
        self.assertEqual(self.client.post("/telegram/getLocale", json={}, headers={"Authorization": "Bearer " + key}).status_code, 403)

    def test_invalid_stored_preference_falls_back_safely_and_setter_rejects_invalid_values(self):
        self.store.set_setting("telegram_language:87654321", {"unexpected": "value"})
        self.assertEqual(self.store.telegram_language(87654321), "pl")
        for user, value in ((True, "en"), (-1, "en"), (87654321, "de"), (87654321, {})):
            with self.subTest(user=user, value=value), self.assertRaises(DashboardError):
                self.store.set_telegram_language(user, value)

    def test_download_buttons_and_requested_copy_use_pressing_users_language(self):
        self.store.set_telegram_language(87654321, "en")
        for index, (ident, user, label) in enumerate(((self.first, 87654321, "⬇ Download file"),
                                                    (self.second, 22222222, "⬇ Pobierz plik")), 10):
            key = self.store.worker_key(self.store.account(ident, private=True))
            response = self.client.post("/telegram/sendDocument", data={"chat_id": str(user), "document": (io.BytesIO(b"VOICE"), "voice.ogg")},
                                        headers={"Authorization": "Bearer " + key})
            self.assertEqual(response.status_code, 200)
            from email.parser import BytesParser
            from email.policy import default
            method, body, options = self.api.calls[-1]
            message = BytesParser(policy=default).parsebytes(("MIME-Version: 1.0\r\nContent-Type: " + options["content_type"] + "\r\n\r\n").encode() + body)
            fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True) for part in message.iter_parts()}
            self.assertEqual(json.loads(fields["reply_markup"])["inline_keyboard"][0][0]["text"], label)
            self.receiver.handle({"update_id": index, "callback_query": {"id": "callback_" + str(index), "data": DOWNLOAD_CALLBACK,
                "from": {"id": user, "is_bot": False}, "message": {"message_id": 45, "message_thread_id": 7,
                "chat": {"id": user, "type": "private"}, "from": {"id": BOT["id"], "is_bot": True},
                "document": {"file_id": "SYNTHETIC_FILE_ID_123456789"}}}})
            copy_params = json.loads(self.api.calls[-1][1])
            self.assertIn("Requested download" if user == 87654321 else "Kopia na żądanie", copy_params["caption"])
            self.assertEqual(copy_params["chat_id"], user)

    def test_upgrade_restart_schedules_only_desired_running_accounts(self):
        self.store.action(self.second, "stop")
        before = {row["id"]: row["revision"] for row in self.store.accounts()}
        self.assertEqual(self.store.restart_active(), 1)
        after = {row["id"]: row for row in self.store.accounts()}
        self.assertEqual(after[self.first]["revision"], before[self.first] + 1)
        self.assertEqual(after[self.second]["revision"], before[self.second])
        self.assertFalse(after[self.second]["desired"])

    def test_two_live_relays_follow_only_their_owners_commands_without_restarting(self):
        archives, relays = [], []
        class Response:
            def __init__(self, raw): self.raw = raw
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return self.raw[:limit]
        def gateway(request, **kwargs):
            body = request.data if isinstance(request.data, bytes) else b"".join(request.data)
            result = self.client.post("/telegram/" + request.full_url.rsplit("/", 1)[-1], data=body,
                                      content_type=request.get_header("Content-type"),
                                      headers={"Authorization": request.get_header("Authorization")})
            return Response(result.data)
        try:
            for ident, own in ((self.first, "111222333"), (self.second, "444555666")):
                self.store.action(ident, "options", mode="all")
                row = self.store.account(ident, private=True)
                archive = EventStore(self.root / ident, own_id=own, started_at=1000, run_id=ident)
                archive.accept({"type": "metadata", "data": {"contacts": [{"id": 222, "name": "Saved"}], "threads": [{"id": 42, "name": "Unnamed conversation"}]}})
                archives.append(archive)
                env = {"TELEGRAM_ENABLED": "true", "TELEGRAM_TOPICS": "false", "TELEGRAM_MODE": "all",
                       "TELEGRAM_RELAY_URL": "http://127.0.0.1:8788", "TELEGRAM_ACCOUNT_KEY": self.store.worker_key(row),
                       "TELEGRAM_BOT_ID": str(BOT["id"]), "TELEGRAM_CHAT_ID": str(row["chat_id"])}
                with patch.dict(os.environ, env, clear=True):
                    relay = TelegramRelay(lambda _: None, archive, start=False)
                relay.interval = 0
                relays.append(relay)
            def send(index, expected):
                for position, (archive, relay, title) in enumerate(zip(archives, relays, expected)):
                    mid = f"message-{index}-{position}"
                    archive.accept({"type": "message", "timestamp": 1001000 + index, "data": {
                        "id": mid, "threadId": 42, "senderId": 222, "timestampMs": 1001000 + index,
                        "text": "Deleted message Sent Saved <b>literal😀</b>"}})
                    relay._deleted("42", mid)
                    params = json.loads(self.api.calls[-1][1])
                    self.assertTrue(params["text"].startswith(title))
                    self.assertIn("Deleted message Sent Saved <b>literal😀</b>", params["text"])
                    self.assertIn("Saved", params["text"])
                    self.assertIn("Unnamed conversation", params["text"])
            with patch("telegram_relay.urlopen", side_effect=gateway):
                send(0, ("💬 Zapisana wiadomość", "💬 Zapisana wiadomość"))
                self.command("/en")
                send(1, ("💬 Message captured", "💬 Zapisana wiadomość"))
                self.command("/en", user=22222222, update_id=11)
                self.command("/pl", update_id=12)
                send(2, ("💬 Zapisana wiadomość", "💬 Message captured"))
        finally:
            for relay in relays: relay.close()
            for archive in archives: archive.close()
