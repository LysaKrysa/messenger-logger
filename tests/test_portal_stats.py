"""Metadata-only owned capture reports, with no live HTTP or message bodies."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from event_store import EventStore
from telegram_relay import TelegramRelay


class PortalStatsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 1003
        self.store = EventStore(Path(self.temp.name), own_id="111", started_at=1000, run_id="stats", clock=lambda: self.now, retention_seconds=50)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def message(self, ident="incoming", **values):
        data = dict(id=ident, threadId=42, senderId=222, text="MESSAGE_BODY_MUST_NEVER_APPEAR_IN_STATS", timestampMs=1001000)
        data.update(values)
        return {"type": "message", "timestamp": 1001000, "data": data}

    def test_retained_counts_media_and_deletions_exclude_own_historical_and_filtered_messages(self):
        self.store.accept(self.message(attachments=[{"type": "voice", "url": "https://scontent.xx.fbcdn.net/voice"}, {"type": "image"}]))
        self.store.update_media("42", "incoming", 0, {"downloadStatus": "saved", "savedBytes": 2048, "localPath": "media/voice.ogg"})
        self.store.accept({"type": "messageUnsend", "timestamp": 1002000, "data": {"threadId": 42, "messageId": "incoming"}})
        self.store.accept(self.message("own", senderId=111))
        self.store.accept(self.message("past", timestampMs=999000))
        self.store.capture_allowed = lambda tid: tid != "43"
        self.store.accept(self.message("excluded", threadId=43))
        counts = self.store.dashboard_stats()
        self.assertEqual(counts, dict(messages=1, deletions=1, attachments=2, files=1, media_bytes=2048, conversations=1))
        self.assertNotIn("MESSAGE_BODY", json.dumps(counts))
        self.assertNotIn("url", json.dumps(counts))
        self.now += 51
        self.assertEqual(self.store.dashboard_stats(), dict.fromkeys(counts, 0))

    def test_worker_reports_are_bounded_metadata_and_throttled_independently_of_status(self):
        self.store.accept(self.message())
        env = {"TELEGRAM_ENABLED": "true", "TELEGRAM_RELAY_URL": "http://127.0.0.1:8788", "TELEGRAM_CHAT_ID": "87654321",
               "TELEGRAM_ACCOUNT_KEY": "a" * 32 + "." + "b" * 64, "TELEGRAM_BOT_ID": "123456", "LOGGER_ACCOUNT_REVISION": "3"}
        with patch.dict(os.environ, env, clear=True):
            relay = TelegramRelay(lambda line: None, self.store, start=False)
            with patch.object(relay, "_api", return_value=True) as api:
                for _ in range(2): relay.report_stats({"connected": True, "e2eeConnected": True}, 1000, 1, 0, 1, True)
                self.assertEqual(api.call_count, 1)
                method, report = api.call_args.args
                self.assertEqual(method, "reportStats")
                self.assertEqual(report["revision"], 3)
                self.assertEqual(report["messages"], 1)
                self.assertIs(report["encrypted_connected"], True)
                self.assertNotIn("MESSAGE_BODY", json.dumps(report))
                relay.next_stats = 0
                relay.report_stats({"connected": False}, 1000, 1, 0, 1, False)
                self.assertIsNone(api.call_args.args[1]["encrypted_connected"])

    def test_view_once_preference_changes_dynamically_with_owner_controls(self):
        env = {"TELEGRAM_ENABLED": "true", "TELEGRAM_RELAY_URL": "http://127.0.0.1:8788", "TELEGRAM_CHAT_ID": "87654321",
               "TELEGRAM_ACCOUNT_KEY": "a" * 32 + "." + "b" * 64, "TELEGRAM_BOT_ID": "123456"}
        with patch.dict(os.environ, env, clear=True): relay = TelegramRelay(lambda line: None, self.store, start=False)
        with patch.object(relay, "_api", return_value={"view_once": True}): relay.refresh_controls(force=True)
        self.assertTrue(relay.view_once)
        with patch.object(relay, "_api", return_value={"view_once": False}): relay.refresh_controls(force=True)
        self.assertFalse(relay.view_once)
