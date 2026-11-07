from __future__ import annotations

import hashlib
import io
import io
import json
import os
from pathlib import Path
import queue
import socket
import ssl
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from event_store import EventStore
from media_saver import MediaSaver
from telegram_relay import (TelegramRelay, TelegramConfigurationError, TelegramRequestError,
                            ERROR_RESPONSE_BYTES, chunks)
from telegram_format import RichText, units, message_card
from telegram_topics import TopicRouter, TopicError, TelegramRejected
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "123456:" + "SYNTHETIC_BOT_TOKEN_NOT_REAL" * 2
PNG = b"\x89PNG\r\n\x1a\nlocal synthetic file"


def message(mid="m1", attachments=None):
    return {"type": "message", "timestamp": 1001000, "data": {
        "id": mid, "threadId": 42, "senderId": 222, "timestampMs": 1001000,
        "text": "original <literal> & text", "attachments": attachments or [],
    }}


def deletion(mid="m1", tid=42):
    return {"type": "messageUnsend", "timestamp": 1002000, "data": {"threadId": tid, "messageId": mid}}


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return b'{"ok":true,"result":{}}'


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        self.store = EventStore(self.root, own_id="12345", started_at=1000, run_id="telegram-run")
        self.store.accept({"type": "metadata", "data": {"contacts": [{"id": 222, "name": "Alex", "username": "alex.user"}], "threads": [{"id": 42, "name": "Conversation"}]}})
        self.env = patch.dict(os.environ, {"TELEGRAM_ENABLED": "true", "TELEGRAM_LANGUAGE": "en", "TELEGRAM_MODE": "deleted", "TELEGRAM_SEND_MEDIA": "true", "TELEGRAM_VIEW_ONCE": "false", "TELEGRAM_TOPICS": "false", "TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "123456"})
        self.env.start()
        self.calls, self.reports = [], []
        self.network_error = None
        self.mock = patch("telegram_relay.urlopen", side_effect=self.fake_open)
        self.mock.start()
        self.relay = TelegramRelay(self.reports.append, self.store, timezone="UTC")
        self.relay.interval = 0

    def tearDown(self):
        self.assertTrue(self.relay.close())
        self.mock.stop()
        self.env.stop()
        self.store.close()
        self.temp.cleanup()

    def fake_open(self, request, **options):
        if self.network_error:
            raise self.network_error
        body = request.data if isinstance(request.data, bytes) else b"".join(request.data)
        self.calls.append((request, body))
        return Response()

    def accept(self, event):
        result = self.store.accept(event)
        if result:
            self.relay.submit(result)
        return result

    def settle(self):
        deadline = time.monotonic() + 3
        while self.relay.items.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.relay.items.unfinished_tasks, 0)

    def texts(self):
        return [json.loads(body)["text"] for request, body in self.calls if request.full_url.endswith("/sendMessage")]

    def save_media(self, mid="m1", index=0, data=PNG, filename=None):
        relative = Path("media") / "telegram-run" / (filename or (mid + ("_" + str(index) if index else "") + ".png"))
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.store.update_media("42", mid, index, {"downloadStatus": "saved", "localPath": relative.as_posix(), "savedBytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})

    def document_fields(self):
        from email.parser import BytesParser
        from email.policy import default
        self.assertEqual(len(self.calls), 1)
        request, body = self.calls[0]
        self.assertTrue(request.full_url.endswith("/sendDocument"))
        self.assertEqual(int(request.get_header("Content-length")), len(body))
        multipart = BytesParser(policy=default).parsebytes(("MIME-Version: 1.0\r\nContent-Type: " + request.get_header("Content-type") + "\r\n\r\n").encode() + body)
        return {part.get_param("name", header="content-disposition"): part.get_payload(decode=True) for part in multipart.iter_parts()}

    def test_voice_download_has_original_bytes_and_deletion_details_in_one_message(self):
        voice = b"OggSsynthetic-voice-recording"
        self.accept(message(attachments=[{"type": "voice", "fileName": "recording.ogg"}]))
        self.save_media(data=voice, filename="recording.ogg")
        self.accept(deletion())
        self.settle()
        fields = self.document_fields()
        self.assertEqual(fields["document"], voice)
        self.assertIn(b"Deleted message", fields["caption"])
        self.assertIn(b"original <literal> & text", fields["caption"])
        self.assertIn(b'filename="recording.ogg"', self.calls[0][1])

    def test_multiple_files_wait_for_all_downloads_then_send_one_safe_zip(self):
        self.accept(message(attachments=[{"type": "image", "url": "https://fbcdn.net/PRIVATE_URL"}, {"type": "voice"}]))
        self.accept(deletion())
        self.save_media()
        self.relay.media_updated("42", "m1")
        self.settle()
        self.assertEqual(self.calls, [])
        voice = b"OggSsynthetic-voice"
        self.save_media(index=1, data=voice, filename='voice"\r\n.ogg')
        self.relay.media_updated("42", "m1")
        self.settle()
        fields = self.document_fields()
        with zipfile.ZipFile(io.BytesIO(fields["document"])) as archive:
            self.assertEqual(archive.read("message.txt").decode().count("Deleted message"), 1)
            self.assertEqual(archive.read("01_m1.png"), PNG)
            self.assertEqual(archive.read("02_voice___.ogg"), voice)
            self.assertTrue(all("/" not in name and "\\" not in name for name in archive.namelist()))
            self.assertNotIn("PRIVATE_URL", archive.read("message.txt").decode())
        self.assertEqual(list(self.root.glob("*.zip")), [])
        for _ in range(3):
            self.relay.media_updated("42", "m1")
        self.settle()
        self.assertEqual(len(self.calls), 1)

    def test_long_deleted_text_is_one_complete_downloadable_transcript(self):
        event = message()
        original = "🙂<literal> & " * 1000
        event["data"]["text"] = original
        self.accept(event)
        self.accept(deletion())
        self.settle()
        fields = self.document_fields()
        self.assertIn(original.encode(), fields["document"])
        self.assertIn(b'filename="message.txt"', self.calls[0][1])
        self.assertLessEqual(units(fields["caption"].decode()), 900)

    def test_long_details_and_one_file_share_one_zip_without_truncation(self):
        event = message(attachments=[{"type": "image"}])
        event["data"]["text"] = "🙂" * 600
        self.accept(event)
        self.save_media()
        self.accept(deletion())
        self.settle()
        fields = self.document_fields()
        with zipfile.ZipFile(io.BytesIO(fields["document"])) as archive:
            self.assertIn(event["data"]["text"], archive.read("message.txt").decode())
            self.assertEqual(archive.read("01_m1.png"), PNG)

    def test_combined_upload_limit_produces_one_notice_and_keeps_both_local_files(self):
        self.accept(message(attachments=[{"type": "file"}, {"type": "file"}]))
        self.save_media(data=b"a" * 1300)
        self.save_media(index=1, data=b"b" * 1300)
        with patch("telegram_relay.MAX_UPLOAD_BYTES", 2000):
            self.accept(deletion())
            self.settle()
        self.assertEqual(len(self.calls), 1)
        self.assertIn("combined download exceeds", self.texts()[0])
        self.assertTrue((self.root / "media/telegram-run/m1.png").is_file())
        self.assertTrue((self.root / "media/telegram-run/m1_1.png").is_file())

    def test_ambiguous_file_upload_is_not_retried_on_later_media_callbacks(self):
        self.accept(message(attachments=[{"type": "image"}]))
        self.save_media()
        self.network_error = TimeoutError("SYNTHETIC_PRIVATE_DETAILS")
        self.accept(deletion())
        self.settle()
        self.network_error = None
        self.relay.media_updated("42", "m1")
        self.settle()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.db.execute("SELECT status FROM telegram_attempts").fetchone()[0], "failed")
        self.assertNotIn("SYNTHETIC_PRIVATE_DETAILS", " ".join(self.reports))

    def test_deleted_mode_sends_captured_text_names_times_and_edit(self):
        self.accept(message())
        self.accept({"type": "reaction", "timestamp": 1001001, "data": {"threadId": 42, "messageId": "m1", "reaction": "👍"}})
        self.assertEqual(self.calls, [])
        self.accept({"type": "messageEdit", "timestamp": 1001500, "data": {"threadId": 42, "messageId": "m1", "newText": "latest edit"}})
        self.accept(deletion())
        self.settle()
        self.assertEqual(len(self.texts()), 1)
        for text in ("Deleted message", "Conversation", "Alex (@alex.user)", "original <literal> & text", "latest edit", "Deleted  "):
            self.assertIn(text, self.texts()[0])
        self.assertNotIn("parse_mode", json.loads(self.calls[0][1]))

    def test_duplicate_deletions_and_media_callbacks_do_not_resend(self):
        self.accept(message(attachments=[{"type": "image"}]))
        self.save_media()
        self.accept(deletion())
        self.settle()
        self.assertIsNone(self.accept(deletion()))
        for _ in range(3):
            self.relay.media_updated("42", "m1")
        self.settle()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual([row[0] for row in self.store.db.execute("SELECT status FROM telegram_attempts")], ["sent"])

    def test_saved_media_is_uploaded_as_exact_file_bytes(self):
        self.accept(message(attachments=[{"type": "image", "fileName": "photo.png", "url": "https://fbcdn.net/PRIVATE_SIGNED_URL"}]))
        self.save_media()
        self.accept(deletion())
        self.settle()
        uploads = [(request, body) for request, body in self.calls if request.full_url.endswith("/sendDocument")]
        self.assertEqual(len(uploads), 1)
        request, body = uploads[0]
        self.assertIn(PNG, body)
        self.assertIn(b'name="document"', body)
        self.assertIn(b'filename="m1.png"', body)
        self.assertEqual(int(request.get_header("Content-length")), len(body))
        self.assertNotIn("PRIVATE_SIGNED_URL", " ".join(self.texts()))

    def test_media_finishing_after_deletion_sends_file_without_repeating_text(self):
        self.accept(message(attachments=[{"type": "image"}]))
        self.accept(deletion())
        self.settle()
        self.assertEqual(self.calls, [])
        self.save_media()
        self.relay.media_updated("42", "m1")
        self.settle()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.texts(), [])
        self.assertTrue(self.calls[0][0].full_url.endswith("/sendDocument"))

    def test_media_saver_callback_reaches_deleted_relay(self):
        import base64
        class FakeBridge:
            def call(self, *args, **kwargs):
                return {"data": base64.b64encode(PNG).decode()}
        saver = MediaSaver(FakeBridge(), self.store, self.reports.append, threading.Event(), on_update=self.relay.media_updated)
        event = message(attachments=[{"type": "image", "url": "https://fbcdn.net/photo"}])
        summary = self.accept(event)
        self.accept(deletion())
        self.settle()
        try:
            saver.submit(event, summary)
            saver.start()
            deadline = time.monotonic() + 3
            while saver.items.unfinished_tasks and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(saver.saved, 1)
            self.settle()
            self.assertEqual(len(self.calls), 1)
        finally:
            saver.close()

    def test_unknown_deleted_message_does_not_forward_unattributed_content(self):
        self.accept(deletion("not-captured"))
        self.settle()
        self.assertEqual(self.calls, [])

    def test_delayed_incoming_original_produces_one_complete_deletion_notice(self):
        self.relay.send_media = False
        self.accept(deletion())
        self.settle()
        self.accept(message())
        self.settle()
        self.assertEqual(len(self.texts()), 1)
        self.assertIn("Deleted message", self.texts()[0])
        self.assertIn("original <literal>", self.texts()[0])

    def test_enabling_relay_does_not_scan_previous_deletions(self):
        self.store.accept(message())
        self.store.accept(deletion())
        time.sleep(0.05)
        self.assertEqual(self.calls, [])

    def test_previous_run_deletions_do_not_forward_on_media_callback(self):
        self.store.accept(message(attachments=[{"type": "image"}]))
        self.store.accept(deletion())
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE messages SET deleted_run_id='earlier-run'")
        self.relay.media_updated("42", "m1")
        self.settle()
        self.assertEqual(self.calls, [])

    def test_failed_send_is_redacted_and_not_automatically_retried(self):
        self.network_error = RuntimeError(TOKEN + " private original message")
        self.accept(message())
        self.accept(deletion())
        self.settle()
        self.network_error = None
        self.relay.media_updated("42", "m1")
        self.settle()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.db.execute("SELECT status FROM telegram_attempts").fetchone()[0], "failed")
        self.assertNotIn(TOKEN, " ".join(self.reports))
        self.assertNotIn("private original", " ".join(self.reports))

    def test_http_rejections_report_fixed_categories_without_private_values(self):
        cases = [
            (400, "Bad Request: message thread not found", "topic_missing"),
            (400, "Bad Request: TOPIC_CLOSED", "topic_closed"),
            (400, "Bad Request: can't parse entities", "invalid_entities"),
            (403, "Forbidden: bot was blocked by the user", "bot_blocked"),
            (403, "Forbidden: bot can't initiate conversation with a user", "bot_not_started"),
            (400, "Bad Request: chat not found", "chat_not_found"),
            (400, "Bad Request: not enough rights to send text messages", "permission_denied"),
            (400, "Bad Request: message is too long", "message_too_long"),
            (400, "Bad Request: message text is empty", "empty_text"),
            (413, "Request Entity Too Large", "file_too_large"),
            (429, "Too Many Requests", "rate_limited"),
            (401, "Unauthorized", "unauthorized"),
            (400, "Unknown provider rejection", "request_rejected"),
        ]
        for code, description, reason in cases:
            with self.subTest(reason=reason):
                body = io.BytesIO(json.dumps({"ok": False, "error_code": code,
                    "description": description + " PRIVATE_MESSAGE " + TOKEN}).encode())
                error = HTTPError("https://api.telegram.org/bot" + TOKEN, code,
                                  "PRIVATE_MESSAGE", None, body)
                with patch("telegram_relay.urlopen", side_effect=error), self.assertRaises(TelegramRejected) as caught:
                    self.relay._api("sendMessage", {"text": "PRIVATE_MESSAGE"})
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.details,
                    "method=sendMessage; status={}; reason={}".format(code, reason))
                self.assertNotIn("PRIVATE_MESSAGE", str(caught.exception))
                self.assertNotIn(TOKEN, str(caught.exception))
                self.assertTrue(body.closed)

    def test_rejected_send_logs_details_and_keeps_failed_attempt_without_replay(self):
        error = HTTPError("https://api.telegram.org/bot" + TOKEN, 400, "PRIVATE_MESSAGE", None,
                          io.BytesIO(b'{"ok":false,"description":"Bad Request: message thread not found"}'))
        with patch("telegram_relay.urlopen", side_effect=error) as send:
            self.accept(message())
            self.accept(deletion())
            self.settle()
            self.relay.media_updated("42", "m1")
            self.settle()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.store.db.execute("SELECT status FROM telegram_attempts").fetchone()[0], "failed")
        report = " ".join(self.reports)
        self.assertIn("method=sendMessage; status=400; reason=topic_missing", report)
        self.assertIn("No automatic resend", report)
        self.assertNotIn(TOKEN, report)
        self.assertNotIn("PRIVATE_MESSAGE", report)

    def test_error_bodies_are_bounded_and_unrecognized_text_is_not_echoed(self):
        class BoundedBody(io.BytesIO):
            def read(self, size=-1):
                self.requested_size = size
                return super().read(size)
        for raw in (b"PRIVATE_MESSAGE " + TOKEN.encode(),
                    b'{"description":"message thread not found"}' + b" " * ERROR_RESPONSE_BYTES):
            body = BoundedBody(raw)
            error = HTTPError("https://api.telegram.org/bot" + TOKEN, 400, "PRIVATE_MESSAGE", None, body)
            with patch("telegram_relay.urlopen", side_effect=error), self.assertRaises(TelegramRejected) as caught:
                self.relay._api("PRIVATE_METHOD_" + TOKEN, {})
            self.assertEqual(body.requested_size, ERROR_RESPONSE_BYTES + 1)
            self.assertEqual(caught.exception.details,
                             "method=unknown; status=400; reason=request_rejected")
            self.assertTrue(body.closed)

    def test_network_failures_are_classified_without_echoing_exception_text(self):
        for cause, reason in ((socket.gaierror("PRIVATE_MESSAGE " + TOKEN), "dns_error"),
                              (ssl.SSLError("PRIVATE_MESSAGE " + TOKEN), "tls_error"),
                              (TimeoutError("PRIVATE_MESSAGE " + TOKEN), "timeout"),
                              ("PRIVATE_MESSAGE " + TOKEN, "connection_error")):
            with self.subTest(reason=reason), patch("telegram_relay.urlopen", side_effect=URLError(cause)), self.assertRaises(TelegramRequestError) as caught:
                self.relay._api("sendDocument", {})
            self.assertNotIsInstance(caught.exception, TelegramRejected)
            self.assertEqual(str(caught.exception),
                             "method=sendDocument; status=unknown; reason=" + reason)
        with patch("telegram_relay.urlopen", side_effect=TimeoutError(TOKEN)), self.assertRaises(TelegramRequestError) as caught:
            self.relay._api("sendMessage", {})
        self.assertIn("reason=timeout", str(caught.exception))

    def test_api_error_payloads_and_invalid_responses_keep_safe_diagnostics(self):
        for raw, exception, details in (
            (b'{"ok":false,"error_code":400,"description":"Bad Request: can\'t parse entities PRIVATE_MESSAGE"}',
             TelegramRejected, "status=400; reason=invalid_entities"),
            (b'{"ok":false,"error_code":502,"description":"PRIVATE_MESSAGE"}',
             TelegramRequestError, "status=502; reason=server_error"),
            (b'{"ok":false,"error_code":"PRIVATE_MESSAGE"}',
             TelegramRequestError, "status=unknown; reason=invalid_response"),
            (b'[]', TelegramRequestError, "status=unknown; reason=invalid_response"),
            (b'PRIVATE_MESSAGE', TelegramRequestError, "status=unknown; reason=invalid_response_json"),
            (b'\xff', TelegramRequestError, "status=unknown; reason=invalid_response_json"),
            (b'x' * (1024 * 1024 + 1), TelegramRequestError, "status=unknown; reason=response_too_large"),
        ):
            with self.subTest(details=details), patch.object(Response, "read", return_value=raw), self.assertRaises(exception) as caught:
                self.relay._api("sendMessage", {})
            self.assertEqual(caught.exception.details, "method=sendMessage; " + details)
            self.assertNotIn("PRIVATE_MESSAGE", str(caught.exception))

    def test_summary_delivery_failures_use_the_same_redacted_details(self):
        self.store.accept(message())
        self.relay.mode = "all"
        error = HTTPError("https://api.telegram.org/bot" + TOKEN, 403, "PRIVATE_MESSAGE", None,
                          io.BytesIO(b'{"ok":false,"description":"Forbidden: bot was blocked by the user"}'))
        with patch("telegram_relay.urlopen", side_effect=error):
            self.accept({"type": "reaction", "timestamp": 1001001,
                         "data": {"threadId": 42, "messageId": "m1", "reaction": "👍"}})
            self.settle()
        report = " ".join(self.reports)
        self.assertIn("method=sendMessage; status=403; reason=bot_blocked", report)
        self.assertNotIn(TOKEN, report)
        self.assertNotIn("PRIVATE_MESSAGE", report)

    def test_topic_creation_distinguishes_rejected_and_unconfirmed_outcomes(self):
        router = TopicRouter(self.relay._api, self.store, "123456", "123456")
        router.chat_id = "123456"
        for thread_id, error, status in (
            ("rejected", HTTPError("https://api.telegram.org/bot" + TOKEN, 400, "PRIVATE_MESSAGE", None,
                                   io.BytesIO(b'{"ok":false,"description":"Bad Request"}')), None),
            ("network", URLError(TimeoutError(TOKEN)), "unconfirmed"),
            ("server", HTTPError("https://api.telegram.org/bot" + TOKEN, 502, "PRIVATE_MESSAGE", None,
                                 io.BytesIO(b'PRIVATE_MESSAGE')), "unconfirmed"),
        ):
            with self.subTest(thread_id=thread_id), patch("telegram_relay.urlopen", side_effect=error), self.assertRaises(TopicError):
                router.topic(thread_id, "Conversation")
            mapping = self.store.telegram_topic("123456", "123456", thread_id)
            self.assertEqual(mapping["status"] if mapping else None, status)

    def test_all_mode_preserves_regular_content_relay(self):
        self.relay.mode = "all"
        self.accept(message())
        self.settle()
        self.assertIn("original <literal> & text", self.texts()[0])
        self.accept(deletion())
        self.settle()
        self.assertEqual(len(self.texts()), 2)
        self.assertIn("Deleted message", self.texts()[1])

    def test_send_media_false_sends_text_only(self):
        self.relay.send_media = False
        self.accept(message(attachments=[{"type": "image"}]))
        self.save_media()
        self.accept(deletion())
        self.settle()
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.calls[0][0].full_url.endswith("/sendMessage"))

    def test_large_upload_stays_local_with_notice(self):
        self.accept(message(attachments=[{"type": "file", "fileName": "huge.bin"}]))
        self.store.update_media("42", "m1", 0, {"downloadStatus": "saved", "localPath": "media/large.bin", "savedBytes": 50_000_001, "sha256": "hash"})
        self.accept(deletion())
        self.settle()
        self.assertEqual(len(self.texts()), 1)
        self.assertIn("local", self.texts()[0])
        self.assertFalse(any(request.full_url.endswith("/sendDocument") for request, _ in self.calls))

    def test_modified_media_is_not_uploaded(self):
        self.accept(message(attachments=[{"type": "image"}]))
        self.save_media()
        (self.root / "media/telegram-run/m1.png").write_bytes(b"X" * len(PNG))
        self.accept(deletion())
        self.settle()
        self.assertEqual(len(self.calls), 1)
        self.assertIn("integrity check", self.texts()[0])
        self.assertEqual([row[0] for row in self.store.db.execute("SELECT status FROM telegram_attempts")], ["sent"])

    def test_external_path_is_not_uploaded(self):
        outside = self.root / "private-state"
        outside.write_bytes(PNG)
        media = {"localPath": "private-state", "savedBytes": len(PNG), "sha256": hashlib.sha256(PNG).hexdigest()}
        with self.assertRaises(ValueError):
            self.relay._document(media, "caption")
        self.assertEqual(self.calls, [])

    def test_failed_media_is_reported_in_one_complete_notice(self):
        self.accept(message(attachments=[{"type": "image"}]))
        self.accept(deletion())
        self.settle()
        self.store.update_media("42", "m1", 0, {"downloadStatus": "failed", "downloadError": "download_failed"})
        self.relay.media_updated("42", "m1")
        self.settle()
        self.assertEqual(len(self.texts()), 1)
        self.assertIn("Unavailable", self.texts()[0])

    def test_long_unicode_text_is_split_without_losing_characters(self):
        text = "🙂" * 4000 + "abc"
        parts = list(chunks(text))
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(len(part.encode("utf-16-le")) // 2 <= 3500 for part in parts))
        self.relay._text(text)
        self.assertEqual("".join(self.texts()), text)

    def test_disabled_relay_never_queues_or_contacts_api(self):
        self.relay.enabled = False
        self.accept(message())
        self.accept(deletion())
        self.settle()
        self.assertEqual(self.calls, [])

    def test_configuration_errors_do_not_expose_token(self):
        for values in ({"TELEGRAM_BOT_TOKEN": "bad" + TOKEN}, {"TELEGRAM_MODE": "invalid"}):
            with patch.dict(os.environ, values), self.assertRaises(TelegramConfigurationError) as error:
                TelegramRelay(self.reports.append, self.store)
            self.assertNotIn(TOKEN, str(error.exception))

    def test_rate_pacing_waits_between_api_calls_and_can_be_cancelled(self):
        self.relay.interval = 1.05
        with patch("telegram_relay.time.monotonic", side_effect=[10, 10, 10.2, 11.05]), patch.object(self.relay.stopping, "wait", return_value=False) as wait:
            self.relay._text("first")
            self.relay._text("second")
        self.assertEqual(wait.call_args_list[0].args, (0.0,))
        self.assertAlmostEqual(wait.call_args_list[1].args[0], 0.85)
        with patch.object(self.relay.stopping, "wait", return_value=True), self.assertRaises(InterruptedError):
            self.relay._post("sendMessage", b"{}")
        self.assertEqual(len(self.calls), 2)

    def test_queue_overflow_does_not_interrupt_local_deletion_capture(self):
        self.relay.close()
        self.relay = TelegramRelay(self.reports.append, self.store, start=False)
        self.relay.items = queue.Queue(maxsize=1)
        self.relay.items.put(("summary", {}))
        self.accept(message())
        self.assertIsNotNone(self.accept(deletion()))
        self.assertTrue(self.store.relay_snapshot("42", "m1")["deleted"])
        self.assertIn("queue full", " ".join(self.reports))
        self.assertEqual(self.calls, [])

    def test_formatted_notification_uses_entities_and_preserves_literal_text(self):
        self.accept(message())
        self.accept(deletion())
        self.settle()
        payload = json.loads(self.calls[0][1])
        self.assertNotIn("parse_mode", payload)
        self.assertTrue(any(item["type"] == "bold" for item in payload["entities"]))
        quote = next(item for item in payload["entities"] if item["type"] == "blockquote")
        raw = payload["text"].encode("utf-16-le")
        quoted = raw[quote["offset"]*2:(quote["offset"]+quote["length"])*2].decode("utf-16-le")
        self.assertEqual(quoted, "original <literal> & text")
        self.assertNotIn("<b>", payload["text"])

    def test_long_formatted_messages_keep_valid_entity_offsets_per_page(self):
        original = "🙂<b>literal</b> & " * 700
        rich = RichText().add("🗑 Deleted message", "bold").add("\n\n").quote(original)
        self.relay._text(rich)
        payloads = [json.loads(body) for request, body in self.calls]
        self.assertEqual("".join(item["text"] for item in payloads), rich.text)
        self.assertGreater(len(payloads), 1)
        for payload in payloads:
            self.assertLessEqual(units(payload["text"]), 3500)
            encoded = payload["text"].encode("utf-16-le")
            for entity in payload["entities"]:
                self.assertGreater(entity["length"], 0)
                self.assertLessEqual(entity["offset"] + entity["length"], units(payload["text"]))
                encoded[entity["offset"]*2:(entity["offset"]+entity["length"])*2].decode("utf-16-le")

    def test_view_once_opt_in_forwards_immediately_and_keeps_deletion_distinct(self):
        self.relay.view_once = True
        self.accept(message(attachments=[{"type": "image", "viewOnce": True, "viewMode": "once"}]))
        self.settle()
        self.assertEqual(self.calls, [])
        self.save_media()
        self.relay.media_updated("42", "m1")
        self.settle()
        self.assertEqual(len(self.calls), 1)
        self.assertIn(b"View-once message", self.calls[0][1])
        self.accept(deletion())
        self.settle()
        self.assertEqual(self.texts(), [])
        self.assertIn(b"Deleted message", self.calls[1][1])
        self.assertEqual(sum(request.full_url.endswith("/sendDocument") for request, _ in self.calls), 2)

    def test_view_once_does_not_expand_deleted_only_routing_without_opt_in(self):
        self.accept(message(attachments=[{"type": "image", "viewOnce": True, "viewMode": "once"}]))
        self.settle()
        self.assertEqual(self.calls, [])

    def test_formatted_caption_contains_valid_entities_and_no_raw_paths(self):
        from email.parser import BytesParser
        from email.policy import default
        self.accept(message(attachments=[{"type": "image", "fileName": "photo.png"}]))
        self.save_media()
        self.accept(deletion())
        self.settle()
        request, body = next((request, body) for request, body in self.calls if request.full_url.endswith("/sendDocument"))
        message_body = BytesParser(policy=default).parsebytes(("MIME-Version: 1.0\r\nContent-Type: " + request.get_header("Content-type") + "\r\n\r\n").encode() + body)
        fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True) for part in message_body.iter_parts()}
        caption = fields["caption"].decode()
        entities = json.loads(fields["caption_entities"])
        self.assertIn("Deleted message", caption)
        self.assertIn("original <literal> & text", caption)
        self.assertIn("Alex (@alex.user)", caption)
        self.assertNotIn("/data/", caption)
        self.assertTrue(entities)
        self.assertTrue(all(entity["offset"]+entity["length"] <= units(caption) for entity in entities))


if __name__ == "__main__":
    unittest.main()
