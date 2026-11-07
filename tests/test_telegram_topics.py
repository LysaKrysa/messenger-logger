from __future__ import annotations

from email.parser import BytesParser
from email.policy import default
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from event_store import EventStore
from telegram_relay import TelegramRelay
from telegram_format import message_card
from telegram_topics import TopicRouter, TopicError, TelegramRejected, topic_title
from check_telegram_topics import main as check_topics
import contextlib
import io

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "123456:" + "SYNTHETIC_TOPIC_TOKEN_NOT_REAL" * 2
PNG = b"\x89PNG\r\n\x1a\nsynthetic topic fixture"


def message(mid="m1", thread=42, *, attachments=None):
    return {"type": "message", "timestamp": 1001000, "data": {"id": mid, "threadId": thread, "senderId": 222, "text": "captured text", "timestampMs": 1001000, "attachments": attachments or []}}


def deletion(mid="m1", thread=42):
    return {"type": "messageUnsend", "timestamp": 1002000, "data": {"messageId": mid, "threadId": thread}}


class API:
    def __init__(self):
        self.calls = []
        self.private = True
        self.enabled = True
        self.manage = True
        self.forum = True
        self.chat_id = 7654321
        self.created = 0
        self.error = None

    def __call__(self, method, params):
        self.calls.append((method, params))
        if method == "getMe":
            return {"id": 123456, "is_bot": True, "has_topics_enabled": self.enabled}
        if method == "getChat":
            return {"id": self.chat_id, "type": "private" if self.private else "supergroup", "is_forum": self.forum}
        if method == "getChatMember":
            return {"status": "administrator", "can_manage_topics": self.manage}
        if method == "createForumTopic":
            self.created += 1
            if self.error:
                raise self.error
            return {"message_thread_id": 100 + self.created, "name": params["name"]}
        if method == "editForumTopic":
            return True
        return {"message_id": 99}


class TopicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        self.store = EventStore(self.root, started_at=1000, run_id="topics-run")
        self.api = API()
        self.router = TopicRouter(self.api, self.store, "123456", "7654321")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_one_topic_per_conversation_even_when_names_are_equal(self):
        a = self.router.topic("42", "Alex")
        b = self.router.topic("43", "Alex")
        self.assertNotEqual(a, b)
        self.assertEqual(self.router.topic("42", "Alex"), a)
        created = [params for method, params in self.api.calls if method == "createForumTopic"]
        self.assertEqual([item["name"] for item in created], ["Alex", "Alex (2)"])

    def test_restart_reuses_persisted_topic_without_recreating(self):
        topic = self.router.topic("42", "Friends")
        self.store.close()
        self.store = EventStore(self.root, started_at=2000, run_id="after-restart")
        router = TopicRouter(self.api, self.store, "123456", "7654321")
        self.assertEqual(router.topic("42", "Friends"), topic)
        self.assertEqual(self.api.created, 1)
        self.assertEqual(self.store.counts(), {})

    def test_destination_chat_and_bot_ids_scope_the_topic_map(self):
        original = self.router.topic("42", "Friends")
        self.api.chat_id = 888
        other = TopicRouter(self.api, self.store, "123456", "888")
        self.assertNotEqual(other.topic("42", "Friends"), original)
        self.assertIsNone(self.store.telegram_topic("999", "888", "42"))

    def test_private_chat_requires_bot_threaded_mode(self):
        self.api.enabled = False
        with self.assertRaisesRegex(TopicError, "Threaded mode"):
            self.router.topic("42", "Friends")
        self.assertEqual(self.api.created, 0)
        self.assertIsNone(self.store.telegram_topic("123456", "7654321", "42"))

    def test_failed_capability_check_refreshes_after_cooldown(self):
        self.api.enabled = False
        with patch("telegram_topics.time.monotonic", return_value=100):
            with self.assertRaises(TopicError):
                self.router.destination()
        self.api.enabled = True
        with patch("telegram_topics.time.monotonic", return_value=110):
            with self.assertRaises(TopicError):
                self.router.destination()
        with patch("telegram_topics.time.monotonic", return_value=131):
            self.assertEqual(self.router.destination(), "7654321")
        self.assertEqual(sum(method == "getMe" for method, params in self.api.calls), 2)
        self.assertEqual(self.api.created, 0)

    def test_new_router_rechecks_immediately_after_mode_is_enabled(self):
        self.api.enabled = False
        with self.assertRaises(TopicError):
            self.router.destination()
        self.api.enabled = True
        restarted = TopicRouter(self.api, self.store, "123456", "7654321")
        self.assertEqual(restarted.destination(), "7654321")
        self.assertEqual(self.api.created, 0)

    def test_forum_group_requires_manage_topics_permission(self):
        self.api.private, self.api.manage = False, False
        self.api.chat_id = -100888
        self.router = TopicRouter(self.api, self.store, "123456", "-100888")
        with self.assertRaisesRegex(TopicError, "Manage Topics"):
            self.router.topic("42", "Friends")
        self.api.manage = True
        self.router.retry_at = 0
        self.assertGreater(self.router.topic("42", "Friends"), 0)

    def test_nonforum_destination_is_rejected(self):
        self.api.private, self.api.forum = False, False
        with self.assertRaisesRegex(TopicError, "Topics enabled"):
            self.router.topic("42", "Friends")

    def test_ambiguous_creation_is_not_repeated_and_can_be_bound_locally(self):
        self.api.error = TimeoutError(TOKEN + " PRIVATE provider details")
        with self.assertRaises(TopicError) as failure:
            self.router.topic("42", "Friends")
        self.assertNotIn(TOKEN, str(failure.exception))
        self.api.error = None
        with self.assertRaisesRegex(TopicError, "unconfirmed"):
            self.router.topic("42", "Friends")
        self.assertEqual(self.api.created, 1)
        self.store.save_telegram_topic("123456", "7654321", "42", 555, "Friends")
        self.assertEqual(self.router.topic("42", "Friends"), 555)
        self.assertEqual(self.api.created, 1)

    def test_definitively_rejected_creation_can_retry_after_permission_fix(self):
        self.api.error = TelegramRejected(403)
        with self.assertRaises(TopicError):
            self.router.topic("42", "Friends")
        self.assertIsNone(self.store.telegram_topic("123456", "7654321", "42"))
        self.api.error = None
        self.assertGreater(self.router.topic("42", "Friends"), 0)

    def test_stale_failure_does_not_overwrite_a_concurrent_manual_bind(self):
        self.router.destination()
        self.store.save_telegram_topic("123456", "7654321", "42", 555, "Verified topic")
        self.assertEqual(self.router.replace_missing("42", 77, "Friends"), 555)
        self.assertEqual(self.api.created, 0)

    def test_repair_after_retention_uses_a_nonempty_title(self):
        self.router.destination()
        self.store.save_telegram_topic("123456", "7654321", "42", 77, "")
        replacement = self.router.replace_missing("42", 77, "Friends")
        mapping = self.store.telegram_topic("123456", "7654321", "42")
        self.assertEqual(mapping["name"], "Friends")
        self.assertEqual(mapping["status"], "repair_pending")
        self.router.confirm("42", replacement)
        self.assertEqual(self.store.telegram_topic("123456", "7654321", "42")["status"], "ready")

    def test_cancel_before_request_does_not_leave_an_ambiguous_mapping(self):
        self.api.error = InterruptedError()
        with self.assertRaises(InterruptedError):
            self.router.topic("42", "Friends")
        self.assertIsNone(self.store.telegram_topic("123456", "7654321", "42"))

    def test_unknown_title_is_improved_when_contact_name_arrives(self):
        topic = self.router.topic("42", "Unnamed conversation")
        self.assertEqual(self.router.topic("42", "Alex Real"), topic)
        edits = [params for method, params in self.api.calls if method == "editForumTopic"]
        self.assertEqual(edits, [{"chat_id": "7654321", "message_thread_id": topic, "name": "Alex Real"}])
        self.assertFalse(self.store.telegram_topic("123456", "7654321", "42")["is_fallback"])

    def test_titles_fit_api_limits_with_unicode_and_no_placeholder_names(self):
        for text in ("🙂" * 200, "ą" * 200, "long name " * 40, "username", ""):
            title = topic_title(text)
            self.assertTrue(title)
            self.assertLessEqual(len(title.encode("utf-8")), 128)
        self.assertEqual(topic_title("username"), "Conversation")

    def test_local_repair_command_does_not_require_bot_token_or_send_requests(self):
        self.router.topic("42", "Friends")
        command = [sys.executable, str(ROOT / "telegram_topics.py"), "bind", "--bot-id", "123456", "--chat-id", "7654321", "--conversation", "42", "--topic-id", "456", "--name", "Friends"]
        result = subprocess.run(command, env=dict(os.environ, DATA_DIR=str(self.root)), capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["topic_id"], 456)


class Response:
    def __init__(self, data):
        self.data = data
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, limit):
        return json.dumps(self.data).encode()


class TopicDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"TELEGRAM_ENABLED": "true", "TELEGRAM_LANGUAGE": "en", "TELEGRAM_TOPICS": "true", "TELEGRAM_MODE": "deleted", "TELEGRAM_CHAT_ID": "7654321", "TELEGRAM_BOT_TOKEN": TOKEN}, clear=True)
        self.env.start()
        self.api = API()
        self.output = io.StringIO()
        self.failure = None
        def request(req, **kwargs):
            method = req.full_url.rsplit("/", 1)[-1]
            self.assertIn(method, {"getMe", "getChat", "getChatMember"})
            if self.failure:
                raise self.failure
            result = self.api(method, json.loads(req.data))
            if method == "getMe":
                result["username"] = "CorrectConfiguredBot"
            return Response({"ok": True, "result": result})
        self.network = patch("telegram_relay.urlopen", side_effect=request)
        self.network.start()

    def tearDown(self):
        self.network.stop()
        self.env.stop()

    def check(self):
        with contextlib.redirect_stdout(self.output):
            result = check_topics()
        self.assertNotIn(TOKEN, self.output.getvalue())
        return result

    def test_fresh_check_identifies_bot_and_capability_without_creating_or_sending(self):
        self.assertEqual(self.check(), 0)
        self.assertIn("Configured bot: @CorrectConfiguredBot", self.output.getvalue())
        self.assertIn("Telegram bot Threaded mode: enabled", self.output.getvalue())
        self.assertIn("Destination type: private", self.output.getvalue())
        self.assertEqual([method for method, params in self.api.calls], ["getMe", "getChat"])

    def test_botfather_enabled_but_logger_routing_disabled_is_explained(self):
        os.environ["TELEGRAM_TOPICS"] = "false"
        self.assertEqual(self.check(), 2)
        self.assertIn("Logger conversation topics: disabled", self.output.getvalue())
        self.assertIn("Telegram bot Threaded mode: enabled", self.output.getvalue())
        self.assertIn("TELEGRAM_TOPICS=true", self.output.getvalue())

    def test_disabled_bot_capability_reports_configured_bot_and_creates_nothing(self):
        self.api.enabled = False
        self.assertEqual(self.check(), 2)
        self.assertIn("Telegram bot Threaded mode: disabled", self.output.getvalue())
        self.assertIn("same bot in BotFather", self.output.getvalue())
        self.assertEqual(self.api.created, 0)

    def test_disabled_forwarding_makes_no_requests(self):
        os.environ["TELEGRAM_ENABLED"] = "false"
        self.assertEqual(self.check(), 2)
        self.assertIn("Logger forwarding: disabled", self.output.getvalue())
        self.assertEqual(self.api.calls, [])

    def test_provider_failure_is_redacted(self):
        from urllib.error import HTTPError
        self.failure = HTTPError("https://api.telegram.org/bot" + TOKEN, 401, TOKEN + "PRIVATE_DESCRIPTION", {}, None)
        self.assertEqual(self.check(), 2)
        self.assertIn("getMe HTTP 401", self.output.getvalue())
        self.assertNotIn("PRIVATE_DESCRIPTION", self.output.getvalue())


class TopicRelayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        self.store = EventStore(self.root, started_at=1000, run_id="relay-topics")
        self.store.accept({"type": "metadata", "data": {"threads": [{"id": 42, "name": "Friends"}, {"id": 43, "name": "Work"}]}})
        self.env = patch.dict(os.environ, {"TELEGRAM_ENABLED": "true", "TELEGRAM_LANGUAGE": "en", "TELEGRAM_TOPICS": "true", "TELEGRAM_MODE": "all", "TELEGRAM_VIEW_ONCE": "true", "TELEGRAM_SEND_MEDIA": "true", "TELEGRAM_CHAT_ID": "7654321", "TELEGRAM_BOT_TOKEN": TOKEN})
        self.env.start()
        self.api = API()
        self.calls, self.reports = [], []
        self.delivery_hook = None
        self.mock = patch("telegram_relay.urlopen", side_effect=self.fake_open)
        self.mock.start()
        self.relay = TelegramRelay(self.reports.append, self.store)
        self.relay.interval = 0

    def tearDown(self):
        self.assertTrue(self.relay.close())
        self.mock.stop()
        self.env.stop()
        self.store.close()
        self.temp.cleanup()

    def fake_open(self, request, **options):
        body = request.data if isinstance(request.data, bytes) else b"".join(request.data)
        self.calls.append((request, body))
        method = request.full_url.rsplit("/", 1)[-1]
        if method == "sendDocument":
            fields = self.multipart_fields(request, body)
            params = {"message_thread_id": int(fields["message_thread_id"])} if "message_thread_id" in fields else {}
            result = {"message_id": 9}
        else:
            params = json.loads(body)
            result = None
        if self.delivery_hook:
            response = self.delivery_hook(method, params)
            if response is not None:
                return Response(response)
        if method != "sendDocument":
            result = self.api(method, params)
        return Response({"ok": True, "result": result})

    @staticmethod
    def multipart_fields(request, body):
        multipart = BytesParser(policy=default).parsebytes(("MIME-Version: 1.0\r\nContent-Type: " + request.get_header("Content-type") + "\r\n\r\n").encode() + body)
        return {part.get_param("name", header="content-disposition"): part.get_payload(decode=True) for part in multipart.iter_parts()}

    def missing_topic(self):
        return {"ok": False, "error_code": 400, "description": "Bad Request: message thread not found PRIVATE " + TOKEN}

    def seed_stale_topic(self):
        self.store.save_telegram_topic("123456", "7654321", "42", 77, "Friends")

    def accept(self, event):
        summary = self.store.accept(event)
        if summary:
            self.relay.submit(summary)

    def settle(self):
        deadline = time.monotonic() + 3
        while self.relay.items.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.relay.items.unfinished_tasks, 0)

    def sends(self):
        return [json.loads(body) for request, body in self.calls if request.full_url.endswith("/sendMessage")]

    def test_long_download_and_short_text_go_to_their_own_topics(self):
        first = message()
        first["data"]["text"] = "🙂" * 4000
        self.accept(first)
        self.accept(message("m2", thread=43))
        self.settle()
        topics = [item["message_thread_id"] for item in self.sends()]
        self.assertEqual(len(topics), 1)
        documents = [self.multipart_fields(request, body) for request, body in self.calls if request.full_url.endswith("/sendDocument")]
        self.assertEqual(len(documents), 1)
        self.assertNotEqual(int(documents[0]["message_thread_id"]), topics[0])
        self.assertIn(first["data"]["text"].encode(), documents[0]["document"])

    def test_deletion_and_late_media_followup_reuse_original_topic(self):
        self.accept(message(attachments=[{"type": "image"}]))
        self.settle()
        self.accept(deletion())
        self.settle()
        path = self.root / "media/test.png"
        path.parent.mkdir()
        path.write_bytes(PNG)
        self.store.update_media("42", "m1", 0, {"downloadStatus": "saved", "localPath": "media/test.png", "savedBytes": len(PNG), "sha256": hashlib.sha256(PNG).hexdigest()})
        self.relay.media_updated("42", "m1")
        self.settle()
        texts = self.sends()
        self.assertEqual(texts, [])
        request, body = next((request, body) for request, body in self.calls if request.full_url.endswith("/sendDocument"))
        multipart = BytesParser(policy=default).parsebytes(("MIME-Version: 1.0\r\nContent-Type: " + request.get_header("Content-type") + "\r\n\r\n").encode() + body)
        fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True) for part in multipart.iter_parts()}
        self.assertEqual(fields["message_thread_id"], b"101")
        self.assertEqual(fields["document"], PNG)
        self.assertEqual(self.api.created, 1)

    def test_edit_and_reaction_notices_stay_in_the_conversation_topic(self):
        self.accept(message())
        for kind, fields in (("messageEdit", {"newText": "edited"}), ("reaction", {"reaction": "👍"})):
            self.accept({"type": kind, "timestamp": 1002000, "data": {"messageId": "m1", "threadId": 42, **fields}})
        self.settle()
        self.assertEqual([item["message_thread_id"] for item in self.sends()], [101, 101, 101])

    def test_setup_failure_never_falls_back_to_general_chat(self):
        self.api.enabled = False
        self.accept(message())
        self.settle()
        self.assertEqual(self.sends(), [])
        self.assertEqual(self.store.counts(), {"message": 1})
        self.assertIn("Threaded mode", " ".join(self.reports))
        self.assertNotIn(TOKEN, " ".join(self.reports))

    def test_no_topics_are_created_merely_by_starting_logger(self):
        self.assertEqual(self.calls, [])

    def test_missing_topic_is_replaced_for_only_the_rejected_conversation(self):
        self.seed_stale_topic()
        self.relay.mode = "deleted"
        self.store.save_telegram_topic("123456", "7654321", "43", 88, "Work")
        self.store.save_telegram_topic("999999", "7654321", "42", 99, "Another bot")
        self.delivery_hook = lambda method, params: self.missing_topic() if method == "sendMessage" and params["message_thread_id"] == 77 else None
        self.accept(message())
        self.accept(deletion())
        self.settle()
        self.assertEqual([item["message_thread_id"] for item in self.sends()], [77, 101])
        self.assertEqual(self.api.created, 1)
        self.assertEqual(self.store.telegram_topic("123456", "7654321", "42")["status"], "ready")
        self.assertEqual(self.store.telegram_topic("123456", "7654321", "43")["topic_id"], 88)
        self.assertEqual(self.store.telegram_topic("999999", "7654321", "42")["topic_id"], 99)
        self.assertEqual(self.store.db.execute("SELECT status FROM telegram_attempts").fetchone()[0], "sent")
        self.assertIn("missing topic replaced", " ".join(self.reports))
        self.assertNotIn(TOKEN, " ".join(self.reports))
        self.assertNotIn("PRIVATE", " ".join(self.reports))
        self.assertFalse(any("delete" in request.full_url.rsplit("/", 1)[-1].lower() for request, _ in self.calls))

    def test_topic_repair_does_not_repeat_successful_text_pages(self):
        self.seed_stale_topic()
        received, accepted = [], []
        def hook(method, params):
            if method != "sendMessage":
                return None
            received.append(params)
            if len(received) == 2:
                return self.missing_topic()
            accepted.append(params)
        self.delivery_hook = hook
        event = message()
        event["data"]["text"] = "🙂 long literal text " * 600
        self.store.accept(event)
        card = message_card(self.store.relay_snapshot("42", "m1"), self.relay.zone, "en")
        self.relay._text(card, route=self.relay._route("42", "Friends"))
        expected = card.text
        self.assertEqual("".join(item["text"] for item in accepted), expected)
        self.assertEqual(received[1]["text"], received[2]["text"])
        self.assertEqual([item["message_thread_id"] for item in received[:3]], [77, 77, 101])
        self.assertEqual(self.api.created, 1)

    def test_summary_notice_can_repair_topic_without_replaying_original(self):
        self.seed_stale_topic()
        self.accept(message())
        self.settle()
        self.delivery_hook = lambda method, params: self.missing_topic() if method == "sendMessage" and params["message_thread_id"] == 77 else None
        self.accept({"type": "reaction", "timestamp": 1001001,
                     "data": {"threadId": 42, "messageId": "m1", "reaction": "👍"}})
        self.settle()
        sends = self.sends()
        self.assertEqual([item["message_thread_id"] for item in sends], [77, 77, 101])
        self.assertEqual(sends[1]["text"], sends[2]["text"])
        self.assertNotEqual(sends[0]["text"], sends[2]["text"])
        self.assertEqual(self.api.created, 1)

    def test_rejected_upload_is_rewound_and_rebuilt_for_replacement_topic(self):
        self.seed_stale_topic()
        self.accept(message(attachments=[{"type": "image"}]))
        self.settle()
        path = self.root / "media/test.png"
        path.parent.mkdir()
        path.write_bytes(PNG)
        self.store.update_media("42", "m1", 0, {"downloadStatus": "saved", "localPath": "media/test.png", "savedBytes": len(PNG), "sha256": hashlib.sha256(PNG).hexdigest()})
        self.delivery_hook = lambda method, params: self.missing_topic() if method == "sendDocument" and params["message_thread_id"] == 77 else None
        self.relay.media_updated("42", "m1")
        self.settle()
        uploads = [(request, body) for request, body in self.calls if request.full_url.endswith("/sendDocument")]
        self.assertEqual(len(uploads), 2)
        fields = [self.multipart_fields(request, body) for request, body in uploads]
        self.assertEqual([item["message_thread_id"] for item in fields], [b"77", b"101"])
        self.assertEqual([item["document"] for item in fields], [PNG, PNG])
        self.assertTrue(all(int(request.get_header("Content-length")) == len(body) for request, body in uploads))
        self.assertEqual(self.sends(), [])
        self.assertEqual([row[0] for row in self.store.db.execute("SELECT status FROM telegram_attempts")], ["sent"])

    def test_network_failure_does_not_change_mapping_or_resend(self):
        self.seed_stale_topic()
        def hook(method, params):
            if method == "sendMessage":
                raise TimeoutError("PRIVATE " + TOKEN)
        self.delivery_hook = hook
        self.accept(message())
        self.settle()
        self.delivery_hook = None
        self.relay.media_updated("42", "m1")
        self.settle()
        self.assertEqual(len(self.sends()), 1)
        self.assertEqual(self.api.created, 0)
        self.assertEqual(self.store.telegram_topic("123456", "7654321", "42")["topic_id"], 77)
        self.assertEqual(self.store.db.execute("SELECT status FROM telegram_attempts").fetchone()[0], "failed")
        self.assertNotIn(TOKEN, " ".join(self.reports))

    def test_unrelated_rejection_does_not_replace_topic(self):
        self.seed_stale_topic()
        self.delivery_hook = lambda method, params: {"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities"} if method == "sendMessage" else None
        self.accept(message())
        self.settle()
        self.assertEqual(len(self.sends()), 1)
        self.assertEqual(self.api.created, 0)
        self.assertEqual(self.store.telegram_topic("123456", "7654321", "42")["topic_id"], 77)
        self.assertIn("reason=invalid_entities", " ".join(self.reports))

    def test_missing_replacement_is_not_recreated_repeatedly_or_after_restart(self):
        self.seed_stale_topic()
        self.delivery_hook = lambda method, params: self.missing_topic() if method == "sendMessage" else None
        self.accept(message())
        self.settle()
        self.assertEqual(len(self.sends()), 2)
        self.assertEqual(self.api.created, 1)
        self.assertEqual(self.store.telegram_topic("123456", "7654321", "42")["status"], "missing")
        self.relay.topics = TopicRouter(self.relay._api, self.store, "123456", "7654321")
        self.accept(message("m2"))
        self.settle()
        self.assertEqual(len(self.sends()), 2)
        self.assertEqual(self.api.created, 1)
        self.assertIn("Automatic repair is paused", " ".join(self.reports))
        self.store.save_telegram_topic("123456", "7654321", "42", 555, "Verified topic")
        self.delivery_hook = None
        self.accept(message("m3"))
        self.settle()
        self.assertEqual(self.sends()[-1]["message_thread_id"], 555)

    def test_ambiguous_replacement_creation_keeps_unconfirmed_state(self):
        self.seed_stale_topic()
        def hook(method, params):
            if method == "sendMessage":
                return self.missing_topic()
            if method == "createForumTopic":
                raise TimeoutError("PRIVATE " + TOKEN)
        self.delivery_hook = hook
        self.accept(message())
        self.settle()
        self.assertEqual(self.store.telegram_topic("123456", "7654321", "42")["status"], "unconfirmed")
        self.accept(message("m2"))
        self.settle()
        self.assertEqual(len(self.sends()), 1)
        self.assertEqual(sum(request.full_url.endswith("/createForumTopic") for request, _ in self.calls), 1)
        self.assertNotIn(TOKEN, " ".join(self.reports))

    def test_uncertain_send_to_replacement_reuses_it_for_future_events(self):
        self.seed_stale_topic()
        def hook(method, params):
            if method == "sendMessage":
                if params["message_thread_id"] == 77:
                    return self.missing_topic()
                raise TimeoutError("PRIVATE " + TOKEN)
        self.delivery_hook = hook
        self.accept(message())
        self.settle()
        self.assertEqual(self.store.telegram_topic("123456", "7654321", "42")["status"], "repair_pending")
        self.delivery_hook = None
        self.relay.topics = TopicRouter(self.relay._api, self.store, "123456", "7654321")
        self.accept(message("m2"))
        self.settle()
        self.assertEqual([item["message_thread_id"] for item in self.sends()], [77, 101, 101])
        self.assertEqual(self.api.created, 1)
        self.assertEqual(self.store.telegram_topic("123456", "7654321", "42")["status"], "ready")
        self.assertEqual([row[0] for row in self.store.db.execute("SELECT status FROM telegram_attempts")], ["failed", "sent"])

    def test_username_destination_is_resolved_and_sends_use_verified_numeric_id(self):
        self.relay.topics.requested_chat = "@example_destination"
        self.relay.chat = "@example_destination"
        self.accept(message())
        self.settle()
        self.assertEqual(self.sends()[0]["chat_id"], "7654321")
        self.assertEqual(self.sends()[0]["message_thread_id"], 101)

    def test_http_rejections_are_classified_without_exposing_provider_text(self):
        from urllib.error import HTTPError
        self.mock.stop()
        self.mock = patch("telegram_relay.urlopen", side_effect=HTTPError("https://api.telegram.org/bot" + TOKEN, 403, "PRIVATE provider text", None, None))
        self.mock.start()
        with self.assertRaises(TelegramRejected) as error:
            self.relay._api("createForumTopic", {"chat_id": "7654321", "name": "Test"})
        self.assertEqual(error.exception.code, 403)
        self.assertNotIn(TOKEN, str(error.exception))
        self.assertNotIn("PRIVATE", str(error.exception))


if __name__ == "__main__":
    unittest.main()
