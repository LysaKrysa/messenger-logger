"""Bounded, content-free diagnosis through the real RPC and admission paths."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

from bridge_rpc import Bridge, MAX_NATIVE_LOG_LINE, NativeDiagnostics
from event_store import EventStore
from logger import LoggerRuntime

SECRET = "PRIVATE_DIAGNOSTIC_CONTENT_MUST_NOT_ESCAPE"


def incoming(mid="incoming", **fields):
    return {"type": "e2eeMessage", "timestamp": 1001000, "data": {
        "id": mid, "threadId": 42, "senderId": "222", "timestampMs": 1001000,
        "text": "synthetic incoming text", **fields}}


class CaptureDiagnosticsTests(unittest.TestCase):
    def test_each_admission_stage_is_counted_without_persisting_rejected_bodies(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp), own_id="111", started_at=1000, clock=lambda: 1003)
            try:
                self.assertIsNotNone(store.accept(incoming()))
                self.assertIsNone(store.accept(incoming()))
                for mid, sender in (("own", "111"), ("unknown", "")):
                    self.assertIsNone(store.accept(incoming(mid, senderId=sender, text=SECRET)))
                self.assertIsNone(store.accept(incoming("stale", timestampMs=999000, text=SECRET)))
                store.capture_allowed = lambda tid: False
                self.assertIsNone(store.accept(incoming("filtered", text=SECRET)))
                self.assertIsNone(store.accept({"type": "message", "data": [SECRET]}))
                self.assertEqual(store.intake_snapshot(), {
                    "received": 7, "invalid_data": 1, "timestamp_rejected": 1,
                    "capture_filtered": 1, "own_or_unattributed": 2, "duplicate": 1, "saved": 1})
                self.assertNotIn(SECRET, "\n".join(store.db.iterdump()))
                for file in store.logs.rglob("*"):
                    if file.is_file():
                        self.assertNotIn(SECRET, file.read_text())
            finally:
                store.close()

    def test_native_stderr_is_drained_and_classified_without_exposing_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lines = [
                'Unavailable message ' + SECRET + ' from source (type: "")',
                'Unavailable message ' + SECRET + ' from source (type: "view_once")',
                'Error decrypting message ' + SECRET + ' from source: no session found for user',
                'Error decrypting message ' + SECRET + ' from source: no sender key',
                "Error decrypting message " + SECRET + " from source: prekey store didn't return one-time key",
                'Error decrypting message ' + SECRET + ' from source: untrusted identity',
                'Error decrypting message ' + SECRET + ' from source: mismatching MAC in signal message',
                'Error decrypting message ' + SECRET + ' from source: unrecognized ' + SECRET,
                'Other native warning ' + SECRET,
            ]
            huge = b'x' * (MAX_NATIVE_LOG_LINE * 5) + b' Error decrypting message hidden: no session found for user\n'
            signal = '[ERROR] 2026-01-02T03:04:05Z SessionCipher.go:123 \u25b6  no valid sessions ' + SECRET + '\n'
            binary = root / 'fake-bridge'
            binary.write_text('#!' + sys.executable + '\nimport json, os, sys\n'
                              + 'os.write(2, ' + repr(huge) + ')\n'
                              + 'os.write(2, ' + repr(('\n'.join(lines) + '\n').encode()) + ')\n'
                              + 'os.write(1, ' + repr(signal.encode()) + ')\n'
                              + 'for raw in sys.stdin:\n'
                              + '    request=json.loads(raw)\n'
                              + '    print(json.dumps({"id":request["id"],"ok":True,"data":{"connected":True}}),flush=True)\n')
            binary.chmod(0o700)
            with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as errors:
                bridge = Bridge(binary, verify=False)
                try:
                    self.assertTrue(bridge.call('isConnected', timeout=5)['connected'])
                    deadline = time.monotonic() + 5
                    while bridge.native_snapshot()['stderr_lines'] < len(lines) and time.monotonic() < deadline:
                        time.sleep(.01)
                    counts = bridge.native_snapshot()
                    self.assertEqual(counts['stderr_lines'], len(lines))
                    self.assertEqual(counts['oversized_logs'], 1)
                    self.assertEqual(counts['missing_session'], 2)
                    for key in ('unavailable', 'view_once_unavailable', 'missing_sender_key', 'missing_prekey',
                                'identity_changed', 'bad_mac', 'other_decrypt'):
                        self.assertEqual(counts[key], 1, key)
                    self.assertIsNone(bridge.failure)
                    self.assertNotIn(SECRET, repr(counts))
                finally:
                    bridge.close()
                self.assertFalse(bridge._stderr_reader.is_alive())
            self.assertNotIn(SECRET, output.getvalue() + errors.getvalue())

    def test_runtime_reports_only_aggregate_counters_with_a_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LoggerRuntime(Path(tmp), "UTC", threading.Event(), started_at=1000)
            store = EventStore(Path(tmp), own_id="111", started_at=1000, clock=lambda: 1003)
            try:
                store.accept(incoming('own', senderId='111', text=SECRET))
                diagnostics = NativeDiagnostics()
                diagnostics.record(('Error decrypting message ' + SECRET + ': no sender key').encode())
                runtime.store = store
                runtime.bridge = SimpleNamespace(native_snapshot=diagnostics.snapshot)
                runtime.relay = SimpleNamespace(enabled=True, gateway='http://127.0.0.1:8788', filters_ready=False)
                reports = []
                runtime.report = reports.append
                runtime.report_capture_diagnostics()
                runtime.report_capture_diagnostics()
                self.assertEqual(len(reports), 2)
                self.assertIn('own_or_unattributed=1', reports[0])
                self.assertIn('controls_ready=false', reports[0])
                self.assertIn('missing_sender_key=1', reports[1])
                self.assertNotIn(SECRET, '\n'.join(reports))
                runtime.next_capture_diagnostics = 0
                runtime.relay.filters_ready = True
                runtime.report_capture_diagnostics()
                self.assertEqual(len(reports), 4)
                self.assertIn('controls_ready=true', reports[2])
            finally:
                store.close()
