from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge_rpc import Bridge, BridgeFailure, prepare_event
from event_store import EventStore, StoreFailure, identifier
from install_bridge import smoke_test
import logger

TEST_ROOT = Path(__file__).resolve().parents[1]
SECRET = "PRIVATE_VALUE_MUST_NOT_BE_LOGGED"


def message(mid="m1", **extra):
    data = {"id": mid, "threadId": 42, "senderId": 222, "text": "hello <world>", "timestampMs": 1793746800000}
    data.update(extra)
    return {"type": "e2eeMessage", "data": data, "timestamp": 1793746800000}


class TemporaryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()


class CookieTests(unittest.TestCase):
    def test_browser_export_and_foreign_domain(self):
        data = [
            {"name": "c_user", "value": "12345", "domain": ".facebook.com"},
            {"name": "xs", "value": SECRET}, {"name": "datr", "value": "device"},
            {"name": "unrelated", "value": "other", "domain": ".example.com"},
        ]
        self.assertEqual(logger.parse_cookies(json.dumps(data)), {"c_user": "12345", "xs": SECRET, "datr": "device"})

    def test_missing_cookie_error_has_names_only(self):
        with self.assertRaises(logger.ConfigurationError) as result:
            logger.parse_cookies(json.dumps({"c_user": "12345", "xs": SECRET}))
        self.assertIn("datr", str(result.exception))
        self.assertNotIn(SECRET, str(result.exception))

    def test_reject_injected_header_and_non_numeric_account(self):
        for cookies in (
            {"c_user": "12345", "xs": "value\r\nInjected", "datr": "d"},
            {"c_user": "invalid", "xs": SECRET, "datr": "d"},
        ):
            with self.assertRaises(logger.ConfigurationError):
                logger.parse_cookies(json.dumps(cookies))


class StoreTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        self.store = EventStore(self.root, timezone="Europe/Warsaw", own_id="12345")

    def tearDown(self):
        if self.store is not None:
            self.store.close()
        super().tearDown()

    def log_text(self):
        return "\n".join(path.read_text() for path in (self.root / "message_logs_modern").rglob("*") if path.is_file())

    def test_text_names_replies_and_sensitive_media_exclusion(self):
        self.store.accept({"type": "metadata", "data": {
            "contacts": [{"id": 222, "name": "A <B>"}],
            "threads": [{"id": 42, "name": "Group / ../../Łódź", "type": 2}],
        }})
        self.store.accept(message(attachments=[{"type": "image", "mediaKey": SECRET, "directPath": SECRET, "fileName": "pic.jpg"}], extraSecret=SECRET))
        self.store.accept(message("m2", text="reply", replyTo={"messageId": "m1", "senderId": 222, "text": ""}))
        log = self.log_text()
        self.assertIn("A &lt;B&gt;", log)
        self.assertIn("hello &lt;world&gt;", log)
        self.assertIn("pic.jpg", log)
        self.assertIn("<b>Reply</b>: reply", log)
        self.assertNotIn(SECRET, log)
        stored = " ".join(row[0] for row in self.store.db.execute("SELECT payload FROM events"))
        self.assertNotIn(SECRET, stored)
        self.assertNotIn("mediaKey", stored)
        self.assertEqual(len(list((self.root / "message_logs_modern").iterdir())), 1)

    def test_message_dedup_survives_restart_and_preserves_legacy_logs(self):
        old = self.root / "message_logs" / "old"
        old.mkdir(parents=True)
        (old / "3_11_2026").write_text("old content\n")
        self.assertIsNotNone(self.store.accept(message()))
        self.store.close()
        self.store = EventStore(self.root, own_id="12345")
        self.assertIsNone(self.store.accept(message()))
        self.assertEqual(self.store.counts(), {"message": 1})
        self.assertEqual(self.log_text().count("hello &lt;world&gt;"), 1)
        self.assertEqual((old / "3_11_2026").read_text(), "old content\n")

    def test_edits_resolve_thread_and_keep_original_history(self):
        self.store.accept(message())
        self.store.accept({"type": "messageEdit", "data": {"messageId": "m1", "threadId": 0, "newText": "changed", "timestampMs": 1793746801000}})
        row = self.store.db.execute("SELECT thread_id,payload FROM events WHERE type='edit'").fetchone()
        self.assertEqual(row[0], "42")
        self.assertEqual(json.loads(row[1])["transport"], "encrypted")
        self.assertIn("hello &lt;world&gt;", self.log_text())
        self.assertIn("was edited: changed", self.log_text())
        self.assertEqual(self.store.db.execute("SELECT text FROM messages").fetchone()[0], "changed")

    def test_reactions_and_unsend_are_kept_but_receipts_and_typing_are_filtered(self):
        self.store.accept(message())
        for event in (
            {"type": "e2eeReaction", "timestamp": 100, "data": {"messageId": "m1", "chatJid": "42@msgr", "senderId": 333, "reaction": "👍"}},
            {"type": "e2eeReaction", "timestamp": 101, "data": {"messageId": "m1", "chatJid": "42@msgr", "senderId": 333, "reaction": ""}},
            {"type": "e2eeReceipt", "timestamp": 102, "data": {"chat": "42@msgr", "sender": "333@msgr", "type": "read", "messageIds": ["m1"]}},
            {"type": "typing", "timestamp": 103, "data": {"threadId": 42, "senderId": 333, "isTyping": True}},
            {"type": "messageUnsend", "timestamp": 104, "data": {"messageId": "m1", "threadId": "42@msgr", "isE2EE": True}},
        ):
            self.store.accept(event)
        self.assertEqual(self.store.counts(), {"message": 1, "reaction": 2, "unsend": 1})
        text = self.log_text()
        for part in ("reacted", "removed a reaction", "was deleted/unsent", "hello &lt;world&gt;"):
            self.assertIn(part, text)
        self.assertNotIn("receipt", text)
        self.assertNotIn("typing", text)

    def test_crash_recovery_replays_pending_event_without_duplicate_append(self):
        self.store.accept(message())
        path = next(path for path in self.store.logs.rglob("*") if path.is_file())
        with patch.object(self.store, "_export_pending", side_effect=OSError("simulated interruption")):
            with self.assertRaises(OSError):
                self.store.accept(message("m2", text="second event"))
        self.store.close()
        with path.open("ab") as handle:
            handle.write(b"INTERRUPTED APPEND")
        self.store = EventStore(self.root)
        text = self.log_text()
        self.assertNotIn("INTERRUPTED APPEND", text)
        self.assertEqual(text.count("hello &lt;world&gt;"), 1)
        self.assertEqual(text.count("second event"), 1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM events WHERE exported=0").fetchone()[0], 0)

    def test_warsaw_date_routing_and_unattributed_edit_exclusion(self):
        from datetime import datetime, timezone
        received = datetime(2026, 11, 3, 23, 30, tzinfo=timezone.utc).timestamp()
        self.store.accept(message(), received=received)
        self.assertTrue(list(self.store.logs.rglob("4_11_2026")))
        self.store.accept({"type": "messageEdit", "data": {"messageId": "not-observed", "newText": "edit without original"}, "timestamp": 777})
        self.assertIsNone(self.store.db.execute("SELECT thread_id FROM events WHERE type='edit'").fetchone())

    def test_never_store_raw_device_key_events(self):
        for event in ({"type": "deviceDataChanged", "data": {"deviceData": SECRET}}, {"type": "raw", "data": {"from": "whatsmeow", "data": SECRET}}):
            self.assertIsNone(prepare_event(event))
            self.assertIsNone(self.store.accept(event))
        self.assertEqual(self.store.counts(), {})

    def test_another_account_cannot_reuse_the_same_database(self):
        self.store.accept(message())
        self.store.close()
        self.store = None
        with self.assertRaises(StoreFailure):
            EventStore(self.root, own_id="98765")


def fake_binary(root, *, wrong_user=False, rejected=False, preamble=""):
    path = root / "fake_bridge"
    code = '''import json, sys, time
connected = False
encrypted = False
for raw in sys.stdin:
    request = json.loads(raw)
    method = request['method']
    data = {}
    ok = True
    if method == 'hello':
        data = {'bridgeVersion':'2.3.4-logger.2','protocolVersion':1,'capabilities':['newClient','connect','connectE2EE','isConnected','events','structuredConnectionStatus','viewOnceMedia','contactLookup','instagramDMs']}
    elif method == 'connect':
        print(json.dumps({'event':{'type':'e2eeMessage','data':{'id':'startup-m1','threadId':42,'senderId':222,'text':'during startup','timestampMs':int(time.time()*1000)}}}), flush=True)
        connected = True
        data = {'user':{'id':USER_ID,'name':'Local Test User'}}
    elif method == 'connectE2EE':
        encrypted = True
    elif method == 'isConnected':
        data = {'connected':connected,'e2eeConnected':encrypted,'nativeReconnecting':False}
    elif method == 'disconnect':
        connected = False
        encrypted = False
    response = {'id':request['id'],'ok':ok,'data':data}
    if REJECTED and method == 'connect':
        response={'id':request['id'],'ok':False,'errorCode':'checkpoint_required','error':'PRIVATE_VALUE_MUST_NOT_BE_LOGGED'}
    print(json.dumps(response),flush=True)
'''
    code = code.replace("USER_ID", "56789" if wrong_user else "12345").replace("REJECTED", repr(rejected))
    path.write_text("#!" + sys.executable + "\nimport json, os\n" + preamble + "\n" + code)
    path.chmod(0o700)
    return path


class RPCTests(TemporaryTest):
    def _await_reader_failure(self, bridge):
        deadline = time.monotonic() + 2
        while bridge.failure is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(bridge.failure)
        return str(bridge.failure)

    def test_blank_records_do_not_interrupt_a_live_stream(self):
        preamble = "print('\\n  \\t\\n', end='', flush=True)"
        bridge = Bridge(fake_binary(self.root, preamble=preamble), verify=False)
        try:
            bridge.check_contract()
            bridge.call("connect")
            self.assertEqual(bridge.events.get(timeout=1)["data"]["text"], "during startup")
            self.assertIsNone(bridge.failure)
        finally:
            bridge.close()

    def test_bad_json_reports_only_stage_category_and_size(self):
        bridge = Bridge(fake_binary(self.root, preamble="print(" + repr(SECRET) + ", flush=True)"), verify=False)
        try:
            error = self._await_reader_failure(bridge)
            self.assertIn("stage=json_decode", error)
            self.assertIn("json_syntax", error)
            self.assertIn("bytes=", error)
            self.assertNotIn(SECRET, error)
            self.assertFalse(bridge.failure.terminal)
        finally:
            bridge.close()

    def test_122_byte_signal_diagnostic_does_not_stop_reader(self):
        prefix = "[ERROR] 2026-01-02T03:04:05Z SessionCipher.go:123 ▶  ".encode()
        raw = prefix + SECRET.encode()
        raw += b"x" * (121 - len(raw)) + b"\n"
        self.assertEqual(len(raw), 122)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            bridge = Bridge(fake_binary(self.root, preamble="os.write(1, " + repr(raw) + ")"), verify=False)
            try:
                bridge.check_contract()
                bridge.call("connect")
                self.assertEqual(bridge.events.get(timeout=1)["data"]["text"], "during startup")
                self.assertIsNone(bridge.failure)
                self.assertEqual(bridge.native_diagnostics, 1)
            finally:
                bridge.close()
        self.assertIn("level=ERROR", output.getvalue())
        self.assertNotIn(SECRET, output.getvalue())

    def test_signal_log_levels_and_timezone_offsets_are_filtered(self):
        for level, zone in (("INFO", "Z"), ("WARNING", "+01:00"), ("ERROR", "-05:30")):
            with self.subTest(level=level), contextlib.redirect_stdout(io.StringIO()) as output:
                line = "[" + level + "] 2026-01-02T03:04:05" + zone + " GroupCipher.go:42 ▶  " + SECRET
                bridge = Bridge(fake_binary(self.root, preamble="print(" + repr(line) + ", flush=True)"), verify=False)
                try:
                    bridge.check_contract()
                    self.assertIsNone(bridge.failure)
                    self.assertEqual(bridge.native_diagnostics, 1)
                finally:
                    bridge.close()
                self.assertNotIn(SECRET, output.getvalue())

    def test_signal_log_between_event_and_rpc_reply_preserves_both(self):
        line = "[WARNING] 2026-01-02T03:04:05Z SessionCipher.go:42 ▶  " + SECRET
        binary = fake_binary(self.root)
        source = binary.read_text()
        old = "    print(json.dumps(response),flush=True)"
        self.assertEqual(source.count(old), 1)
        binary.write_text(source.replace(old, "    print(" + repr(line) + ",flush=True)\n" + old))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            bridge = Bridge(binary, verify=False)
            try:
                bridge.check_contract()
                bridge.call("connect")
                self.assertTrue(bridge.call("isConnected")["connected"])
                self.assertEqual(bridge.events.get(timeout=1)["data"]["text"], "during startup")
                self.assertEqual(bridge.native_diagnostics, 3)
                self.assertIsNone(bridge.failure)
            finally:
                bridge.close()
        self.assertEqual(output.getvalue().count("Native Signal diagnostic"), 1)
        self.assertNotIn(SECRET, output.getvalue())

    def test_signal_log_text_inside_a_chat_message_is_preserved(self):
        line = "[ERROR] 2026-01-02T03:04:05Z SessionCipher.go:42 ▶  synthetic chat text"
        binary = fake_binary(self.root)
        source = binary.read_text()
        binary.write_text(source.replace("'text':'during startup'", "'text':" + repr(line)))
        bridge = Bridge(binary, verify=False)
        try:
            bridge.check_contract()
            bridge.call("connect")
            self.assertEqual(bridge.events.get(timeout=1)["data"]["text"], line)
            self.assertEqual(bridge.native_diagnostics, 0)
            self.assertIsNone(bridge.failure)
        finally:
            bridge.close()

    def test_unrecognized_bracketed_output_remains_a_retryable_failure(self):
        line = "[ERROR] not-a-native-log " + SECRET
        bridge = Bridge(fake_binary(self.root, preamble="print(" + repr(line) + ",flush=True)"), verify=False)
        try:
            error = self._await_reader_failure(bridge)
            self.assertIn("json_syntax", error)
            self.assertFalse(bridge.failure.terminal)
            self.assertNotIn(SECRET, error)
        finally:
            bridge.close()

    def test_content_event_with_wrong_shape_fails_without_leaking_data(self):
        line = json.dumps({"event":{"type":"message","data":[SECRET]}})
        bridge = Bridge(fake_binary(self.root, preamble="print(" + repr(line) + ", flush=True)"), verify=False)
        try:
            error = self._await_reader_failure(bridge)
            self.assertIn("stage=event_filter", error)
            self.assertIn("content_data_not_object", error)
            self.assertIn("event=message", error)
            self.assertTrue(bridge.failure.terminal)
            self.assertNotIn(SECRET, error)
        finally:
            bridge.close()

    def test_wrong_envelope_type_fails_without_printing_values(self):
        line = json.dumps([SECRET])
        bridge = Bridge(fake_binary(self.root, preamble="print(" + repr(line) + ", flush=True)"), verify=False)
        try:
            error = self._await_reader_failure(bridge)
            self.assertIn("stage=envelope", error)
            self.assertIn("envelope_not_object", error)
            self.assertTrue(bridge.failure.terminal)
            self.assertNotIn(SECRET, error)
        finally:
            bridge.close()

    def test_unhashable_event_type_has_a_safe_shape_diagnostic(self):
        line = json.dumps({"event":{"type":[SECRET],"data":{}}})
        bridge = Bridge(fake_binary(self.root, preamble="print(" + repr(line) + ", flush=True)"), verify=False)
        try:
            error = self._await_reader_failure(bridge)
            self.assertIn("event_type_not_string", error)
            self.assertNotIn(SECRET, error)
        finally:
            bridge.close()

    def test_valid_null_control_data_and_metadata_arrays(self):
        ready = prepare_event({"type":"ready","data":None})
        self.assertEqual(ready["data"], {})
        raw = {"type":"raw", "data":{"from":"lightspeed","type":"LSTable","data":{
            "LSDeleteThenInsertContact":None,
            "LSDeleteThenInsertThread":[None,{"ThreadKey":42,"ThreadName":"Group"}],
        }}}
        self.assertEqual(prepare_event(raw)["data"]["threads"][0]["id"], 42)

    def test_invalid_utf8_reports_no_raw_bytes(self):
        preamble = "os.write(1, b'" + SECRET + "\\xff\\n')"
        bridge = Bridge(fake_binary(self.root, preamble=preamble), verify=False)
        try:
            error = self._await_reader_failure(bridge)
            self.assertIn("stage=json_decode", error)
            self.assertIn("invalid_encoding", error)
            self.assertFalse(bridge.failure.terminal)
            self.assertNotIn(SECRET, error)
        finally:
            bridge.close()

    def test_startup_waits_for_retryable_native_connection(self):
        bridge = object.__new__(Bridge)
        pending = {
            "connected": False, "e2eeConnected": False,
            "failureCode": "network_disconnected", "retryable": True,
            "permanent": False, "nativeReconnecting": True,
        }
        ready = {"connected": True, "e2eeConnected": True, "nativeReconnecting": False}
        statuses = iter([pending, pending, ready])
        calls = []
        def call(method, **kwargs):
            calls.append(method)
            return next(statuses)
        bridge.call = call
        with patch("bridge_rpc.time.sleep"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bridge.wait_connected(timeout=2), ready)
        self.assertEqual(calls, ["isConnected"] * 3)

    def test_startup_terminal_and_inactive_recovery_states_stop_immediately(self):
        for code, retryable, permanent, native in (
            ("auth_failed", False, True, False),
            ("checkpoint_required", False, True, False),
            ("protocol_error", False, True, False),
            ("network_disconnected", True, False, False),
            ("network_disconnected", True, True, True),
            ("network_disconnected", False, False, True),
            ("auth_failed", True, False, True),
        ):
            with self.subTest(code=code, retryable=retryable, permanent=permanent, native=native):
                bridge = object.__new__(Bridge)
                calls = []
                def call(method, **kwargs):
                    calls.append(method)
                    return {"connected": False, "failureCode": code, "retryable": retryable, "permanent": permanent, "nativeReconnecting": native}
                bridge.call = call
                with self.assertRaises(BridgeFailure):
                    bridge.wait_connected(timeout=120)
                self.assertEqual(calls, ["isConnected"])

    def test_startup_native_recovery_still_has_a_deadline(self):
        bridge = object.__new__(Bridge)
        calls = []
        def call(method, **kwargs):
            calls.append(kwargs["timeout"])
            return {"connected": False, "failureCode": "network_disconnected", "retryable": True, "permanent": False, "nativeReconnecting": True}
        bridge.call = call
        clock = [0.0]
        def sleep(delay):
            clock[0] += delay
        with patch("bridge_rpc.time.monotonic", side_effect=lambda: clock[0]), patch("bridge_rpc.time.sleep", side_effect=sleep), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(BridgeFailure) as failure:
                bridge.wait_connected(timeout=1)
        self.assertIn("deadline", str(failure.exception))
        self.assertEqual(calls, [1.0, 0.5])

    def test_encrypted_startup_waits_for_both_transports(self):
        bridge = object.__new__(Bridge)
        states = iter([
            {"connected": True, "e2eeConnected": False, "failureCode": "network_disconnected", "retryable": True, "permanent": False, "nativeReconnecting": True},
            {"connected": True, "e2eeConnected": True},
        ])
        bridge.call = lambda *args, **kwargs: next(states)
        with patch("bridge_rpc.time.sleep"), contextlib.redirect_stdout(io.StringIO()):
            self.assertIs(bridge.wait_connected(encrypted=True)["e2eeConnected"], True)

    def test_shutdown_interrupts_an_inflight_startup_request(self):
        script = self.root / "slow_bridge"
        script.write_text("#!" + sys.executable + "\nimport sys,time\nfor line in sys.stdin:\n time.sleep(30)\n")
        script.chmod(0o700)
        stop = threading.Event()
        bridge = Bridge(script, verify=False, stop_event=stop)
        timer = threading.Timer(0.1, stop.set)
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(BridgeFailure) as result:
                bridge.call("connect", timeout=120)
            self.assertIn("shutdown", str(result.exception))
            self.assertLess(time.monotonic() - started, 1.5)
        finally:
            bridge.process.terminate()
            bridge.process.wait(timeout=2)
            bridge.close()
            timer.join()

    def test_full_duplex_startup_event_and_receive_only_method_policy(self):
        bridge = Bridge(fake_binary(self.root), verify=False)
        try:
            bridge.check_contract()
            self.assertEqual(bridge.call("connect")["user"]["id"], 12345)
            self.assertEqual(bridge.events.get(timeout=1)["data"]["text"], "during startup")
            with self.assertRaises(BridgeFailure):
                bridge.call("sendMessage", {"text": "must never be sent"})
        finally:
            bridge.close()

    def test_provider_error_text_never_escapes(self):
        bridge = Bridge(fake_binary(self.root, rejected=True), verify=False)
        try:
            with self.assertRaises(BridgeFailure) as result:
                bridge.call("connect")
            self.assertIn("checkpoint_required", str(result.exception))
            self.assertNotIn(SECRET, str(result.exception))
        finally:
            bridge.close()

    def test_account_mismatch_cannot_write_queued_message(self):
        stop = threading.Event()
        runtime = logger.LoggerRuntime(self.root, "Europe/Warsaw", stop)
        fake = fake_binary(self.root, wrong_user=True)
        with patch("logger.Bridge", side_effect=lambda path, **kw: Bridge(path, verify=False, **kw)), contextlib.redirect_stdout(io.StringIO()):
            try:
                with self.assertRaises(BridgeFailure):
                    runtime.connect({"c_user":"12345","xs":SECRET,"datr":"test"}, fake, True)
            finally:
                runtime.close()
        self.assertFalse((self.root / "messenger-modern.sqlite3").exists())

    def test_complete_runtime_handles_startup_message_after_identity_validation(self):
        stop = threading.Event()
        runtime = logger.LoggerRuntime(self.root, "Europe/Warsaw", stop)
        fake = fake_binary(self.root)
        output = io.StringIO()
        with patch("logger.Bridge", side_effect=lambda path, **kw: Bridge(path, verify=False, **kw)), patch.dict(os.environ, {"TELEGRAM_ENABLED":"false"}), contextlib.redirect_stdout(output):
            try:
                runtime.connect({"c_user":"12345","xs":SECRET,"datr":"test"}, fake, True)
                deadline = time.monotonic() + 2
                while runtime.messages_this_run == 0 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(runtime.messages_this_run, 1)
                self.assertIsNotNone(runtime.last_event)
                self.assertLessEqual(runtime.last_event, time.time())
            finally:
                runtime.close()
        self.assertIn("Messenger listener ready (regular and encrypted)", output.getvalue())
        self.assertNotIn(SECRET, output.getvalue())
        self.assertNotIn("during startup", output.getvalue())

    def test_raw_table_extracts_names_but_not_unrecognized_values(self):
        event = {"type":"raw", "data":{"from":"lightspeed","type":"LSTable","data":{
            "LSDeleteThenInsertContact":[{"Id":222,"Name":"Alice Example","Unrecognized":{"secret":SECRET}}],
            "LSDeleteThenInsertThread":[{"ThreadKey":42,"ThreadName":"Group","ThreadType":2,"Snippet":SECRET}],
        }}}
        prepared = prepare_event(event)
        self.assertNotIn(SECRET, json.dumps(prepared))
        self.assertEqual(prepared["data"]["contacts"][0]["name"], "Alice Example")

    def test_real_pinned_binary_without_credentials(self):
        path = Path(os.environ.get("BRIDGE_TEST_BIN", "/usr/local/bin/messenger-bridge"))
        if not path.is_file():
            self.skipTest("Set BRIDGE_TEST_BIN to run the real, credential-free binary test")
        smoke_test(path)
        bridge = Bridge(path)
        try:
            bridge.check_contract()
            self.assertIs(bridge.call("isConnected")["connected"], False)
            bridge.call("newClient", {"platform": "instagram", "cookies": {
                "ds_user_id": "12345", "sessionid": "SYNTHETIC", "csrftoken": "SYNTHETIC",
            }})
            self.assertIs(bridge.call("isConnected")["connected"], False)
            with self.assertRaises(BridgeFailure):
                bridge.call("downloadMedia", {"url": "https://localhost/private"})
        finally:
            bridge.close()


if __name__ == "__main__":
    unittest.main()
