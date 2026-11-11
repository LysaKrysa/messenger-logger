"""Capture warning windows and idempotent, bounded dashboard projections."""
import json
from pathlib import Path
import tempfile
import unittest

from capture_monitor import CaptureMonitor, ArchiveSync
from event_store import EventStore


class CaptureMonitorTests(unittest.TestCase):
    def test_warning_windows_and_activity_are_independent_of_connection(self):
        now = [1000]
        monitor = CaptureMonitor(900, clock=lambda: now[0])
        monitor.report('An encrypted message was unavailable or could not be decrypted.')
        monitor.report('Media attachment was not saved (category=missing_download_metadata).')
        monitor.report('Telegram queue full; one notification was skipped.')
        monitor.captured()
        report = monitor.snapshot()
        self.assertEqual(report['counts'], {'decryption': 1, 'media': 1, 'queue': 1})
        self.assertEqual(report['last_capture'], 1000)
        now[0] += 601
        self.assertFalse(any(monitor.snapshot()['recent'].values()))
        self.assertEqual(monitor.snapshot()['counts']['decryption'], 1)
        now[0] += 259200
        self.assertIsNone(monitor.snapshot()['last_capture'])
        self.assertIsNone(monitor.snapshot()['last']['decryption'])

    def test_archive_sync_retries_failed_batches_and_reports_changes_after_initial_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp), own_id='111', started_at=999, clock=lambda: 1005, retention_seconds=259200)
            try:
                summary = store.accept({'type': 'message', 'timestamp': 1000000, 'data': {
                    'id': 'm1', 'threadId': 42, 'senderId': 222, 'timestampMs': 1000000,
                    'text': '🙂' * 30000, 'attachments': [{'type': 'voice', 'url': 'SECRET_URL', 'mediaKey': 'SECRET_KEY'}]}})
                sync = ArchiveSync(store)
                calls = []
                def api(method, data):
                    calls.append(data)
                    if len(calls) == 1:
                        raise OSError('synthetic network failure')
                with self.assertRaises(OSError):
                    sync.report(api, 1)
                sync.next_at = 0
                sync.report(api, 1)
                item = calls[-1]['messages'][0]
                self.assertTrue(item['preview_truncated'])
                self.assertEqual(len(item['text']), 20000)
                self.assertNotIn('SECRET_', json.dumps(item))
                self.assertLess(len(json.dumps(calls[-1]).encode()), 700000)
                store.accept({'type': 'messageUnsend', 'timestamp': 1003000, 'data': {'threadId': 42, 'messageId': 'm1'}})
                sync.changed('42', 'm1')
                sync.next_at = 0
                sync.report(api, 1)
                self.assertTrue(calls[-1]['messages'][0]['deleted'])
            finally:
                store.close()
