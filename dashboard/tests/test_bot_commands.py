"""Private command controls and worker integration, with synthetic Telegram only."""
import io
import json
import os
import threading
from unittest.mock import patch

from dashboard.broker import PairingReceiver, create_app
from dashboard.store import Store
from dashboard.tests.test_dashboard import Base, FakeAPI, BOT, COOKIES
from event_store import EventStore
from media_saver import MediaSaver
from telegram_relay import TelegramRelay

USER, OTHER = 87654321, 22222222


class CommandTests(Base):
    def setUp(self):
        super().setUp()
        self.first = self.paired()
        self.second = self.paired(OTHER, COOKIES.replace("111222333", "444555666"))
        self.api = FakeAPI()
        self.receiver = PairingReceiver(self.store, self.api)
        self.client = create_app(self.store, self.api).test_client()
        self.update = 0

    def command(self, text, user=USER, **changes):
        self.update += 1
        message = {"text": text, "chat": {"id": user, "type": "private"}, "from": {"id": user, "is_bot": False}, "message_thread_id": 7}
        message.update(changes)
        before = len(self.api.calls)
        self.receiver.handle({"update_id": self.update, "message": message})
        return json.loads(self.api.calls[-1][1])["text"] if len(self.api.calls) > before else ""

    def controls(self, ident=None):
        return self.store.controls(self.store.account(ident or self.first))

    def post(self, method, value, ident=None):
        row = self.store.account(ident or self.first, private=True)
        return self.client.post("/telegram/" + method, json=value,
                                headers={"Authorization": "Bearer " + self.store.worker_key(row)})

    def test_first_reply_and_start_invitation_open_with_localized_help_notice(self):
        first = self.command("/start")
        self.assertTrue(first.startswith("Witaj"))
        self.assertIn("/help", first)
        self.assertIn("/pomoc", first)
        self.assertNotIn("Witaj", self.command("/settings"))
        self.assertTrue(self.command("/start").startswith("Witaj"))
        self.assertTrue(self.command("/en", user=33333333).startswith("Welcome"))
        ident = self.store.add_account("Invitation", COOKIES.replace("111222333", "777888999"))
        token = self.store.invite(ident).split("start=", 1)[1]
        text = self.command("/start " + token, user=44444444)
        self.assertTrue(text.startswith("Witaj"))
        self.assertIn("prośbę o połączenie", text)
        self.assertIsNone(self.store.account(ident)["chat_id"])

    def test_help_lists_all_commands_in_both_languages_and_aliases_work_independently(self):
        for language, heading, command in (("pl", "Polecenia", "/pomoc"), ("en", "Commands", "/help")):
            self.command("/" + language)
            text = self.command(command)
            self.assertIn(heading, text)
            for name in ("status", "stan", "pause", "pauza", "resume", "wznow", "mode", "tryb", "media", "quiet", "cisza", "settings", "ustawienia", "help", "pomoc", "en", "pl", "privacy", "disconnect"):
                self.assertIn("/" + name, text)
        self.assertIn("Your settings", self.command("/ustawienia@" + BOT["username"]))
        self.command("/pl")
        self.assertIn("Twoje ustawienia", self.command("/settings"))
        self.assertLess(len(text.encode("utf-16-le")) // 2, 3500)

    def test_pause_resume_are_owned_idempotent_and_preserve_pairing(self):
        before = self.store.account(self.first)
        old_key = self.store.worker_key(self.store.account(self.first, private=True))
        self.assertIn("wstrzymanie", self.command("/pauza"))
        paused = self.store.account(self.first)
        self.assertFalse(paused["desired"])
        self.assertEqual(paused["chat_id"], USER)
        self.command("/pause")
        self.assertEqual(self.store.account(self.first)["revision"], paused["revision"])
        self.assertTrue(self.store.account(self.second)["desired"])
        self.assertIn("wznowienie", self.command("/wznów"))
        active = self.store.account(self.first)
        self.command("/resume")
        self.assertEqual(active["revision"], self.store.account(self.first)["revision"])
        self.assertEqual(active["revision"], before["revision"] + 2)
        self.assertIsNone(self.store.authenticate_worker(old_key))
        new_row = self.store.account(self.first, private=True)
        self.assertIsNotNone(self.store.authenticate_worker(self.store.worker_key(new_row)))

    def test_mode_media_quiet_persist_per_owner_without_worker_restart(self):
        revision = self.store.account(self.first)["revision"]
        self.command("/tryb wszystkie")
        self.command("/media wyłącz")
        self.command("/cisza 1g")
        self.assertEqual(self.controls()["mode"], "all")
        self.assertFalse(self.controls()["media"])
        self.assertEqual(self.controls()["quiet_until"], self.now + 3600)
        self.assertEqual(self.store.account(self.first)["revision"], revision)
        self.assertEqual(self.controls(self.second)["mode"], "deleted")
        self.assertTrue(self.controls(self.second)["media"])
        self.assertEqual(self.controls(self.second)["quiet_until"], 0)
        reopened = Store(self.store.root, self.store.key, clock=lambda: self.now)
        self.assertEqual(reopened.controls(reopened.account(self.first)), self.controls())
        self.assertIn("Wyłączone", self.command("/ustawienia"))
        self.command("/mode deleted")
        self.command("/media on")
        self.assertTrue(self.controls()["media"])
        self.assertEqual(self.controls()["mode"], "deleted")

    def test_invalid_arguments_groups_and_sender_mismatches_cannot_control_accounts(self):
        before = self.controls()
        for text in ("/pause 22222222", "/resume x", "/mode", "/mode all 22222222", "/media yes", "/quiet -1h", "/quiet 25h", "/quiet 0h", "/quiet nan", "/settings x", "/status x"):
            self.assertIn("Nieprawidłowe", self.command(text))
        self.assertEqual(self.command("/pause", chat={"id": USER, "type": "supergroup"}), "")
        self.assertEqual(self.command("/pause", **{"from": {"id": OTHER, "is_bot": False}}), "")
        self.assertEqual(self.command("/pause@another_bot"), "")
        self.assertEqual(before, self.controls())
        self.assertTrue(self.store.account(self.first)["desired"])

    def test_unpaired_and_pending_users_cannot_resume_or_target_another_account(self):
        for text in ("/resume", "/mode all", "/media off", "/quiet 1h", "/settings", "/status"):
            self.assertIn("nie jest przypisane", self.command(text, user=33333333))
        ident = self.store.add_account("Pending", COOKIES.replace("111222333", "777888999"))
        token = self.store.invite(ident).split("start=", 1)[1]
        self.command("/start " + token, user=44444444)
        self.command("/resume", user=44444444)
        self.assertFalse(self.store.account(ident)["desired"])
        self.assertTrue(self.store.account(self.first)["desired"])

    def test_quiet_can_end_early_or_expire_and_preserves_prior_windows(self):
        self.command("/quiet 1h")
        self.now += 10
        self.command("/cisza wyłącz")
        self.assertEqual(self.controls()["quiet_until"], 0)
        self.assertEqual(self.controls()["quiet_windows"], [[self.now - 10, self.now]])
        self.command("/quiet 5m")
        self.now += 301
        self.assertIn("Wyciszenie do: Wyłączone", self.command("/settings"))

    def test_control_and_heartbeat_endpoints_are_owned_strict_and_revision_bound(self):
        self.store.user_control(USER, "media", False)
        self.assertFalse(self.post("getControls", {}).json["result"]["media"])
        self.assertTrue(self.post("getControls", {}, self.second).json["result"]["media"])
        for value in ({"chat_id": OTHER}, {"media": True}, {"x": "a" * 2000}):
            self.assertEqual(self.post("getControls", value).status_code, 403)
        row = self.store.account(self.first)
        report = {"revision": row["revision"], "connected": True, "last_event": self.now - 1}
        self.assertEqual(self.post("reportStatus", report).status_code, 200)
        for changes in ({"chat_id": OTHER}, {"connected": "true"}, {"last_event": self.now + 999}, {"last_event": True}, {"revision": 0}, {"text": "PRIVATE"}):
            self.assertEqual(self.post("reportStatus", dict(report, **changes)).status_code, 403)
        self.assertIsNone(self.store.setting("worker_status:" + self.second))
        self.assertEqual(self.api.calls, [])

    def test_status_reports_fresh_stale_paused_and_real_delivery_outcomes(self):
        row = self.store.account(self.first)
        self.assertIn("brak aktualnego", self.command("/status"))
        report = {"revision": row["revision"], "connected": True, "last_event": self.now - 1}
        self.post("reportStatus", report)
        response = self.post("sendMessage", {"chat_id": USER, "text": "synthetic"})
        self.assertEqual(response.status_code, 200)
        text = self.command("/stan")
        self.assertIn("Połączono", text)
        self.assertIn("wysyłka potwierdzona", text)
        self.api.error = True
        self.assertEqual(self.post("sendMessage", {"chat_id": USER, "text": "synthetic"}).status_code, 502)
        self.api.error = False
        self.assertIn("wysyłka niepotwierdzona", self.command("/status"))
        self.now += 91
        self.assertIn("brak aktualnego", self.command("/status"))
        self.command("/pause")
        self.assertIn("Wstrzymany", self.command("/status"))
        self.assertIsNotNone(self.store.setting("delivery_status:" + self.first)["last_sent"])

    def test_gateway_quiet_guard_blocks_automatic_text_and_files_without_upstream_send(self):
        self.store.user_control(USER, "quiet", 3600)
        self.assertTrue(self.post("sendMessage", {"chat_id": USER, "text": "synthetic"}).json["result"]["_logger_suppressed"])
        key = self.store.worker_key(self.store.account(self.first, private=True))
        response = self.client.post("/telegram/sendDocument", data={"chat_id": str(USER), "document": (io.BytesIO(b"FILE"), "voice.ogg")}, headers={"Authorization": "Bearer " + key})
        self.assertTrue(response.json["result"]["_logger_suppressed"])
        self.assertEqual(self.api.calls, [])
        self.assertIsNone(self.store.setting("delivery_status:" + self.first))
        self.assertEqual(self.post("sendMessage", {"chat_id": OTHER, "text": "synthetic"}).status_code, 403)
        self.assertEqual(self.post("sendMessage", {"chat_id": OTHER, "text": "other"}, self.second).status_code, 200)

    def test_welcome_and_control_updates_are_not_replayed_after_uncertain_reply(self):
        self.api.error = True
        self.command("/pause")
        revision = self.store.account(self.first)["revision"]
        receiver = PairingReceiver(self.store, self.api)
        receiver.handle({"update_id": self.update, "message": {"text": "/pause", "chat": {"id": USER, "type": "private"}, "from": {"id": USER, "is_bot": False}}})
        self.assertEqual(self.store.account(self.first)["revision"], revision)
        self.api.error = False
        self.assertNotIn("Witaj", self.command("/settings"))

    def test_new_approved_owner_does_not_inherit_prior_owners_controls_or_telemetry(self):
        self.store.user_control(USER, "media", False)
        self.store.user_control(USER, "quiet", 3600)
        self.store.record_delivery(self.first, "sent")
        self.store.report_worker(self.store.account(self.first), {"revision": self.store.account(self.first)["revision"], "connected": True, "last_event": self.now})
        self.store.action(self.first, "disconnect")
        token = self.store.invite(self.first).split("start=", 1)[1]
        self.assertTrue(self.store.claim_invite(token, 33333333, "new owner"))
        self.store.action(self.first, "approve")
        self.assertTrue(self.controls()["media"])
        self.assertEqual(self.controls()["quiet_until"], 0)
        self.assertIsNone(self.store.setting("worker_status:" + self.first))
        self.assertIsNone(self.store.setting("delivery_status:" + self.first))

    def test_live_worker_controls_media_and_quiet_without_reconnection_or_late_replay(self):
        self.store.user_control(USER, "mode", "all")
        archive = EventStore(self.root / "archive", own_id="111222333", started_at=self.now - 1, run_id="live-controls", clock=lambda: self.now)
        self.addCleanup(archive.close)
        row = self.store.account(self.first, private=True)
        env = {"TELEGRAM_ENABLED": "true", "TELEGRAM_TOPICS": "false", "TELEGRAM_MODE": "deleted", "TELEGRAM_SEND_MEDIA": "true",
               "TELEGRAM_RELAY_URL": "http://127.0.0.1:8788", "TELEGRAM_ACCOUNT_KEY": self.store.worker_key(row),
               "TELEGRAM_BOT_ID": str(BOT["id"]), "TELEGRAM_CHAT_ID": str(USER)}
        with patch.dict(os.environ, env, clear=True):
            relay = TelegramRelay(lambda _: None, archive, start=False)
        self.addCleanup(relay.close)
        relay.interval = 0

        class Response:
            def __init__(self, data): self.data = data
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return self.data[:size]

        quiet_race = [False]
        def opened(request, **kwargs):
            body = request.data if isinstance(request.data, bytes) else b"".join(request.data)
            if quiet_race[0] and request.full_url.endswith("/sendMessage"):
                self.store.user_control(USER, "quiet", 60)
                quiet_race[0] = False
            result = self.client.post("/telegram/" + request.full_url.rsplit("/", 1)[-1], data=body, content_type=request.get_header("Content-type"), headers={"Authorization": request.get_header("Authorization")})
            return Response(result.data)

        def capture(mid, attachments=None):
            event = {"type": "message", "timestamp": self.now * 1000, "data": {"id": mid, "threadId": 42, "senderId": 222, "timestampMs": self.now * 1000, "text": "ORIGINAL 😀", "attachments": attachments or []}}
            return event, archive.accept(event)

        with patch("telegram_relay.urlopen", side_effect=opened), patch("telegram_relay.time.time", side_effect=lambda: self.now):
            _, normal = capture("normal")
            relay._deleted("42", "normal", self.now)
            self.assertEqual(len(self.api.calls), 1)
            self.assertIn("ORIGINAL 😀", json.loads(self.api.calls[-1][1])["text"])
            self.store.user_control(USER, "quiet", 60)
            event, summary = capture("quiet", [{"type": "voice", "url": "https://fbcdn.net/synthetic"}])
            relay.submit(summary)
            # Process the queued job synchronously, as the real worker does;
            # submit may still have a sub-second cached control snapshot.
            if not relay.items.empty():
                kind, item = relay.items.get_nowait()
                relay._deleted(*item)
                relay.items.task_done()
            self.assertEqual(archive.db.execute("SELECT status FROM telegram_attempts WHERE message_id='quiet'").fetchone()[0], "skipped")
            self.assertIsNotNone(archive.relay_snapshot("42", "quiet"))
            self.now += 61
            relay._deleted("42", "quiet", summary["received_at"])
            self.assertEqual(len(self.api.calls), 1)
            self.store.user_control(USER, "media", False)
            relay.refresh_controls(force=True)
            saver = MediaSaver(None, archive, lambda _: None, threading.Event(), allow_download=relay.download_media)
            saver.submit(event, summary)
            self.assertTrue(saver.items.empty())
            self.assertEqual(archive.relay_snapshot("42", "quiet")["attachments"][0]["downloadStatus"], "disabled")
            self.store.user_control(USER, "media", True)
            self.store.user_control(USER, "mode", "deleted")
            relay.refresh_controls(force=True)
            self.assertTrue(relay.download_media())
            self.now += 1
            _, fresh = capture("deleted-only")
            relay._deleted("42", "deleted-only")
            self.assertEqual(len(self.api.calls), 1)
            archive.accept({"type": "messageUnsend", "timestamp": self.now * 1000, "data": {"threadId": 42, "messageId": "deleted-only"}})
            relay._deleted("42", "deleted-only")
            self.assertEqual(len(self.api.calls), 2)
            self.assertEqual(self.store.account(self.first)["revision"], row["revision"])
            # Quiet can begin after the worker reads settings but before its
            # upload reaches the gateway. The final guard is a definitive skip.
            self.store.user_control(USER, "mode", "all")
            self.now += 1
            capture("quiet-race")
            quiet_race[0] = True
            relay._deleted("42", "quiet-race", self.now)
            self.assertEqual(archive.db.execute("SELECT status FROM telegram_attempts WHERE message_id='quiet-race'").fetchone()[0], "skipped")
            self.now += 61
            relay._deleted("42", "quiet-race", self.now)
            self.assertEqual(len(self.api.calls), 2)
