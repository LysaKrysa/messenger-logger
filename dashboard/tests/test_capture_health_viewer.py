"""Owner isolation, encrypted retention, capture alerts and real gateway reporting."""
import json
from pathlib import Path
from unittest.mock import patch

from capture_monitor import CaptureMonitor, ArchiveSync
from event_store import EventStore
from dashboard.broker import create_app as broker_app
from dashboard.capture_health import CaptureHealth
from dashboard.message_archive import MessageArchive
from dashboard.tests.test_portal import PortalBase, USER, OTHER
from dashboard.tests.test_dashboard import ORIGIN, FakeAPI
from dashboard.portal_i18n import TEXT


class HealthViewerTests(PortalBase):
    def setUp(self):
        super().setUp()
        self.archive = self.web.extensions['message_archive']
        self.health = self.web.extensions['capture_health']
        self.value = {'thread_id': '42', 'message_id': 'm1', 'sender': 'Ada <literal>',
                      'text': 'ARCHIVE_PRIVATE_TOKEN', 'original_text': 'ARCHIVE_PRIVATE_TOKEN',
                      'created_ms': self.now * 1000, 'captured_at': self.now, 'deleted': False,
                      'deleted_at_ms': None, 'last_edit_ms': None, 'conversation': 'Friends',
                      'attachments': [], 'preview_truncated': False}

    def report(self, row=None, **changes):
        row = row or self.row
        value = dict(self.value, **changes)
        self.archive.report(row, {'revision': row['revision'], 'messages': [value]})
        chats = self.archive.conversations_for(row)
        return chats[0]['id'] if chats else None

    def health_report(self, row=None, **changes):
        row = row or self.row
        data = {'revision': row['revision'], 'started_at': self.now - 10, 'last_capture': self.now,
                'counts': {'decryption': 3, 'media': 0, 'queue': 0},
                'recent': {'decryption': 3, 'media': 0, 'queue': 0},
                'last': {'decryption': self.now, 'media': None, 'queue': None}}
        data.update(changes)
        self.health.report(row, data)

    def test_viewer_is_encrypted_owner_only_grouped_and_escaped(self):
        chat = self.report()
        self.report(self.other, text='OTHER_PRIVATE_MESSAGE', conversation='OTHER_PRIVATE_CHAT')
        self.login()
        response = self.client.get('/me/messages', base_url=ORIGIN)
        self.assertEqual(response.status_code, 200, response.data)
        page = response.get_data(as_text=True)
        self.assertIn('ARCHIVE_PRIVATE_TOKEN', page)
        self.assertIn('Ada &lt;literal&gt;', page)
        self.assertNotIn('OTHER_PRIVATE', page)
        with self.store.db() as db:
            self.assertNotIn('ARCHIVE_PRIVATE_TOKEN', '\n'.join(db.iterdump()))
        foreign = self.archive.conversations_for(self.other)[0]['id']
        self.assertEqual(self.client.get('/me/messages?conversation=' + foreign, base_url=ORIGIN).status_code, 400)
        self.assertEqual(self.web.test_client().get('/me/messages', base_url=ORIGIN).status_code, 302)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(len(self.archive.page(self.row, chat, {})[0]), 1)

    def test_deleted_undeleted_type_sender_text_and_date_filters(self):
        chat = self.report()
        self.report(message_id='voice', deleted=True, deleted_at_ms=self.now * 1000,
                    attachments=[{'type': 'voice', 'fileName': 'hello.ogg', 'downloadStatus': 'saved'}])
        for filters, count in [({'status': 'deleted'}, 1), ({'status': 'undeleted'}, 1),
                               ({'kind': 'voice'}, 1), ({'kind': 'text'}, 1), ({'q': 'hello.ogg'}, 1),
                               ({'sender': 'Ada'}, 2), ({'sender': 'Other'}, 0),
                               ({'q': "' OR 1=1 --"}, 0), ({'from': '2030-01-01'}, 0)]:
            self.assertEqual(len(self.archive.page(self.row, chat, filters)[0]), count, filters)
        self.login()
        self.assertEqual(self.client.get('/me/messages?conversation=' + chat + '&status=undeleted', base_url=ORIGIN).status_code, 200)
        self.assertEqual(self.client.get('/me/messages?conversation=' + chat + '&from=broken', base_url=ORIGIN).status_code, 400)

    def test_expiry_uses_original_birth_and_edits_do_not_extend_it(self):
        chat = self.report()
        self.now += 259190
        self.report(text='edited', last_edit_ms=self.now * 1000)
        self.now += 11
        self.assertEqual(self.archive.page(self.row, chat, {})[0], [])
        self.archive.cleanup()
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM message_archive').fetchone()[0], 0)

    def test_delayed_snapshots_cannot_undo_a_known_deletion_or_newer_edit(self):
        chat = self.report(text='Edited', last_edit_ms=self.now * 1000, deleted=True, deleted_at_ms=self.now * 1000)
        self.report()
        values, _ = self.archive.page(self.row, chat, {})
        self.assertTrue(values[0]['deleted'])
        self.assertEqual(values[0]['text'], 'Edited')

    def test_new_pairing_cannot_import_previous_owners_archive(self):
        self.report()
        stale = self.row
        self.store.action(self.first, 'disconnect')
        token = self.store.invite(self.first).split('start=')[1]
        self.store.claim_invite(token, 33333333, 'New owner')
        self.now += 1
        self.store.action(self.first, 'approve')
        self.store.action(self.first, 'start')
        fresh = self.store.account(self.first, private=True)
        from dashboard.store import DashboardError
        with self.assertRaises(DashboardError):
            self.report(stale)
        self.report(fresh)
        self.assertEqual(self.archive.conversations_for(fresh), [])

    def test_gateway_rejects_stale_reports_foreign_destinations_and_secret_fields(self):
        gateway = broker_app(self.store, FakeAPI()).test_client()
        headers = {'Authorization': 'Bearer ' + self.store.worker_key(self.row)}
        body = {'revision': self.row['revision'], 'messages': [self.value]}
        self.assertEqual(gateway.post('/telegram/reportArchive', json=body, headers=headers).status_code, 200)
        for bad in [dict(body, revision=0), dict(body, account_id=self.second),
                    dict(body, messages=[dict(self.value, attachments=[{'url': 'SECRET_URL'}])])]:
            self.assertEqual(gateway.post('/telegram/reportArchive', json=bad, headers=headers).status_code, 403)
        self.store.action(self.first, 'stop')
        self.assertEqual(gateway.post('/telegram/reportArchive', json=body, headers=headers).status_code, 403)

    def test_capture_alerts_are_localized_per_owner_deduplicated_and_toggleable(self):
        self.health_report()
        self.store.set_telegram_language(USER, 'en')
        self.health_report(self.other)
        self.health.tick(self.api)
        messages = [json.loads(body) for method, body, _ in self.api.calls if method == 'sendMessage']
        self.assertEqual(len(messages), 2)
        self.assertIn('Capture warning', next(m['text'] for m in messages if m['chat_id'] == USER))
        self.assertIn('Ostrzeżenie zapisu', next(m['text'] for m in messages if m['chat_id'] == OTHER))
        self.api.calls.clear()
        self.health.tick(self.api)
        self.assertEqual(self.api.calls, [])
        self.login()
        self.assertEqual(self.post('/me/health', {}).status_code, 302)
        self.assertFalse(self.health.enabled(self.row))
        self.assertTrue(self.health.enabled(self.other))
        self.assertEqual(self.client.post('/me/health', data={'alerts': 'on'}, base_url=ORIGIN).status_code, 403)

    def test_ambiguous_alert_is_not_repeated_and_recovery_requires_new_capture(self):
        self.health_report()
        self.api.error = True
        self.health.tick(self.api)
        calls = len(self.api.calls)
        self.api.error = False
        self.health.tick(self.api)
        self.assertEqual(len(self.api.calls), calls)
        self.now += 601
        self.health_report(recent=dict.fromkeys(('decryption','media','queue'), 0),
                           last_capture=self.now, last={'decryption': self.now - 601, 'media': None, 'queue': None})
        self.health.tick(self.api)
        self.assertEqual(len(self.api.calls), calls + 1)
        self.health.tick(self.api)
        self.assertEqual(len(self.api.calls), calls + 1)

    def test_health_pages_show_freshness_without_leaking_other_users(self):
        self.health_report()
        self.health_report(self.other, counts={'decryption': 999, 'media': 0, 'queue': 0})
        self.login()
        response = self.client.get('/me/health', base_url=ORIGIN)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn('class="health-number">999<', response.get_data(as_text=True))
        self.assertEqual(self.client.get('/health', base_url=ORIGIN).status_code, 302)
        self.now += 100
        self.assertFalse(self.health.view(self.row)['fresh'])
        self.assertEqual(self.health.view(self.row)['state'], 'unknown')
        self.store.set_password('long-synthetic-password')
        with self.client.session_transaction(base_url=ORIGIN) as session:
            session['sid'] = self.store.login('long-synthetic-password', 'test')
        self.assertEqual(self.client.get('/health', base_url=ORIGIN).status_code, 200)
        self.assertEqual(self.client.get('/accounts/' + self.first + '/health', base_url=ORIGIN).status_code, 200)

    def test_real_worker_snapshot_reaches_gateway_and_viewer_without_tokens(self):
        data = self.root / 'worker'
        events = EventStore(data, own_id='111', started_at=self.now - 10, run_id='test', clock=lambda: self.now, retention_seconds=259200)
        try:
            event = {'type': 'message', 'timestamp': self.now * 1000, 'data': {'id': 'worker', 'threadId': 777,
                     'senderId': 222, 'timestampMs': self.now * 1000, 'text': 'WORKER_TO_WEB', 'senderName': 'Maya'}}
            summary = events.accept(event)
            sync = ArchiveSync(events)
            sync.changed(summary['thread_id'], summary['message_id'])
            gateway = broker_app(self.store, FakeAPI()).test_client()
            def api(method, value):
                response = gateway.post('/telegram/' + method, json=value,
                                        headers={'Authorization': 'Bearer ' + self.store.worker_key(self.row)})
                self.assertEqual(response.status_code, 200, response.data)
            sync.report(api, self.row['revision'])
            self.login()
            self.assertIn('WORKER_TO_WEB', self.client.get('/me/messages', base_url=ORIGIN).get_data(as_text=True))
        finally:
            events.close()

    def test_all_new_translations_exist_in_both_languages(self):
        self.assertEqual(set(TEXT['en']), set(TEXT['pl']))

    def test_unsupported_legacy_rows_cannot_block_fresh_worker_copies(self):
        events = EventStore(self.root / 'legacy-worker', own_id='111', started_at=self.now - 10,
                            run_id='legacy-test', clock=lambda: self.now, retention_seconds=259200)
        try:
            with events.db:
                events.db.execute("INSERT INTO messages(thread_id,message_id,sender_id,text,transport,captured_at) VALUES (?,?,?,?,?,?)",
                                  ('42', 'legacy-no-time', '222', None, 'encrypted', self.now - 5))
                events.db.execute("INSERT INTO messages(thread_id,message_id,sender_id,text,transport,captured_at,created_ms,attachments) VALUES (?,?,?,?,?,?,?,?)",
                                  ('42', 'legacy-invalid-json', '222', 'LEGACY_PRIVATE_BODY', 'encrypted', self.now - 5,
                                   (self.now - 5) * 1000, 'not-json'))
                events.db.execute("INSERT INTO messages(thread_id,message_id,sender_id,text,transport,captured_at,created_ms,metadata_json) VALUES (?,?,?,?,?,?,?,?)",
                                  ('42', 'legacy-invalid-shape', '222', 'LEGACY_PRIVATE_BODY', 'encrypted', self.now - 5,
                                   (self.now - 5) * 1000, '[]'))
                events.db.execute("INSERT INTO messages(thread_id,message_id,sender_id,text,transport,captured_at,created_ms) VALUES (?,?,?,?,?,?,?)",
                                  ('42', 'legacy-null-body', '222', None, 'encrypted', self.now, self.now * 1000))
            fresh = events.accept({'type': 'message', 'timestamp': self.now * 1000, 'data': {
                'id': 'fresh-after-legacy', 'threadId': 42, 'senderId': 222,
                'timestampMs': self.now * 1000, 'text': 'FRESH_COPY_AFTER_LEGACY'}})
            notices = []
            sync = ArchiveSync(events, report=notices.append)
            sync.changed(fresh['thread_id'], fresh['message_id'])
            gateway = broker_app(self.store, FakeAPI()).test_client()
            def api(method, value):
                response = gateway.post('/telegram/' + method, json=value,
                                        headers={'Authorization': 'Bearer ' + self.store.worker_key(self.row)})
                self.assertEqual(response.status_code, 200, response.data)
            self.assertTrue(sync.report(api, self.row['revision']))
            self.login()
            page = self.client.get('/me/messages', base_url=ORIGIN).get_data(as_text=True)
            self.assertIn('FRESH_COPY_AFTER_LEGACY', page)
            self.assertNotIn('LEGACY_PRIVATE_BODY', page)
            self.assertEqual(len(notices), 1)
            self.assertIn('3 unsupported records', notices[0])
            self.assertNotIn('LEGACY_PRIVATE', notices[0])
            chats = self.archive.conversations_for(self.row)
            self.assertEqual(len(self.archive.page(self.row, chats[0]['id'], {})[0]), 2)
            self.assertEqual(events.db.execute("SELECT text,created_ms FROM messages WHERE message_id='legacy-no-time'").fetchone(), (None, 0))
            self.assertEqual(events.db.execute("SELECT attachments FROM messages WHERE message_id='legacy-invalid-json'").fetchone()[0], 'not-json')
            self.assertGreater(sync.after, 0)
        finally:
            events.close()
