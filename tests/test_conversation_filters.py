"""Capture gates and independent forwarding policy, without network access."""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from conversation_filters import DEFAULT, capture, forwarding, valid
from event_store import EventStore
from media_saver import MediaSaver
from telegram_relay import TelegramRelay, TelegramRequestError


def message(mid="incoming", **fields):
    return {"type": "e2eeMessage", "timestamp": 1001000, "data": {
        "id": mid, "threadId": 42, "senderId": 222, "timestampMs": 1001000,
        "text": "FILTERED_CONTENT_MUST_NOT_PERSIST", **fields}}


class FiltersTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = EventStore(self.root, own_id="111", started_at=1000, run_id="filters", clock=lambda: 1002)
        self.policy = copy.deepcopy(DEFAULT)
        self.store.capture_allowed = lambda tid: capture(self.policy, tid)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def relay(self):
        env = {"TELEGRAM_ENABLED": "true", "TELEGRAM_BOT_TOKEN": "123456:" + "SYNTHETIC_TOKEN" * 3,
               "TELEGRAM_CHAT_ID": "87654321", "TELEGRAM_VIEW_ONCE": "true"}
        with patch.dict(os.environ, env, clear=True):
            relay = TelegramRelay(lambda text: None, self.store, start=False)
        relay.filters = self.policy
        return relay

    def test_excluded_direct_chat_and_group_never_store_content_or_queue_downloads(self):
        self.policy["rules"]["42"] = {"capture": False, "forward": "all", "since": 0}
        for extra in ({}, {"threadType": 2, "threadName": "Friends"}):
            event = message(attachments=[{"type": "voice", "url": "https://scontent.xx.fbcdn.net/SECRET"}], **extra)
            self.assertIsNone(self.store.accept(event))
        self.assertEqual(self.store.counts(), {})
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
        dump = "\n".join(self.store.db.iterdump())
        self.assertNotIn("FILTERED_CONTENT", dump)
        self.assertNotIn("SECRET", dump)
        self.assertEqual(self.store.conversation_catalog(), [{"id": "42", "name": "Friends"}])
        self.assertEqual(forwarding(self.policy, "42", "all"), "off")

    def test_include_only_unknown_chats_are_rejected_but_named_metadata_remains_selectable(self):
        self.policy["capture_default"] = False
        self.assertIsNone(self.store.accept(message(threadName="Study group")))
        self.policy["rules"]["42"] = {"capture": True, "forward": "off", "since": 0}
        self.assertIsNotNone(self.store.accept(message()))
        self.assertEqual(self.store.counts(), {"message": 1})
        self.assertEqual(forwarding(self.policy, "42", "all"), "off")
        self.assertFalse(capture(self.policy, "43"))

    def test_excluding_existing_chat_prevents_edits_deletions_and_future_content(self):
        self.store.accept(message())
        self.policy["rules"]["42"] = {"capture": False, "forward": "inherit", "since": 0}
        for kind, extra in (("messageEdit", {"newText": "EXCLUDED_EDIT"}), ("messageUnsend", {}), ("reaction", {"reaction": "EXCLUDED_REACTION"})):
            event = {"type": kind, "timestamp": 1002000, "data": {"threadId": 42, "messageId": "incoming", "senderId": 222, **extra}}
            self.assertIsNone(self.store.accept(event))
        self.assertEqual(self.store.counts(), {"message": 1})
        self.assertFalse(self.store.relay_snapshot("42", "incoming")["deleted"])
        self.assertNotIn("EXCLUDED_", "\n".join(self.store.db.iterdump()))

    def test_per_chat_all_overrides_global_deleted_and_off_keeps_local_capture(self):
        self.store.accept(message())
        snapshot = self.store.relay_snapshot("42", "incoming")
        self.policy["rules"]["42"] = {"capture": None, "forward": "all", "since": 1001}
        relay = self.relay()
        self.assertTrue(relay._eligible(snapshot))
        self.policy["rules"]["42"]["forward"] = "off"
        self.assertFalse(relay._eligible(snapshot))
        self.assertTrue(capture(self.policy, "42"))

    def test_explicit_deleted_rule_waits_for_deletion_even_for_view_once(self):
        self.store.accept(message(attachments=[{"type": "image", "viewOnce": True}]))
        relay = self.relay()
        self.assertTrue(relay._eligible(self.store.relay_snapshot("42", "incoming")))
        self.policy["rules"]["42"] = {"capture": None, "forward": "deleted", "since": 0}
        self.assertFalse(relay._eligible(self.store.relay_snapshot("42", "incoming")))
        self.store.accept({"type": "messageUnsend", "timestamp": 1002000, "data": {"threadId": 42, "messageId": "incoming"}})
        self.assertTrue(relay._eligible(self.store.relay_snapshot("42", "incoming")))

    def test_shared_worker_fails_closed_until_policy_arrives_then_uses_last_known_policy(self):
        relay = self.relay()
        relay.gateway = "http://127.0.0.1:8788"
        with patch.object(relay, "_api", side_effect=TelegramRequestError("sendMessage", "network_error")):
            self.assertFalse(relay.capture_allowed("42"))
        self.policy["capture_default"] = False
        with patch.object(relay, "_api", return_value={"filters": self.policy, "mode": "deleted"}):
            relay.refresh_controls(force=True)
        self.assertFalse(relay.capture_allowed("42"))
        self.policy["rules"]["42"] = {"capture": True, "forward": "inherit", "since": 0}
        with patch.object(relay, "_api", side_effect=TelegramRequestError("sendMessage", "network_error")):
            relay.refresh_controls(force=True)
        self.assertTrue(relay.capture_allowed("42"))

    def test_queued_media_is_rechecked_when_conversation_becomes_excluded(self):
        event = message(attachments=[{"type": "voice", "url": "https://scontent.xx.fbcdn.net/voice.ogg"}])
        summary = self.store.accept(event)
        bridge = Mock()
        saver = MediaSaver(bridge, self.store, lambda text: None, threading.Event(), allow_conversation=lambda tid: capture(self.policy, tid))
        saver.submit(event, summary)
        self.assertEqual(saver.items.qsize(), 1)
        self.policy["capture_default"] = False
        saver.start()
        saver.items.join()
        saver.close()
        bridge.call.assert_not_called()
        self.assertEqual(self.store.relay_snapshot("42", "incoming")["attachments"][0]["downloadStatus"], "disabled")

    def test_policy_and_catalogue_reject_invalid_types_and_normalize_display_names(self):
        self.assertTrue(valid(DEFAULT))
        for malformed in ({}, dict(DEFAULT, capture_default=1), dict(DEFAULT, rules={"42": {"capture": "yes", "forward": "all", "since": 0}}), dict(DEFAULT, rules={"42": {"capture": True, "forward": "all", "since": float("nan")}})):
            self.assertFalse(valid(malformed))
        self.store.accept(message(threadName="Study\ngroup"))
        self.assertEqual(self.store.conversation_catalog()[0]["name"], "Study group")
        self.assertEqual(self.store.conversation_catalog("42"), [])
