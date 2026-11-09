from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge_rpc import Bridge, BridgeBusy, BridgeFailure
from event_store import EventStore, StoreFailure
from media_saver import MediaSaver, media_limit
from view_messages import load_messages, render_message
import logger

ROOT = Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\nsynthetic image fixture"
KEY = base64.b64encode(b"K" * 32).decode()
DIGEST = base64.b64encode(hashlib.sha256(PNG).digest()).decode()


def message(mid="photo-1", *, encrypted=False, sent=1001000, attachments=None):
    return {"type": "e2eeMessage" if encrypted else "message", "timestamp": 1002000, "data": {
        "id": mid, "threadId": 42, "senderId": 222, "text": "caption", "timestampMs": sent,
        "attachments": attachments if attachments is not None else [{"type": "image", "url": "https://scontent.xx.fbcdn.net/photo", "mimeType": "image/png"}],
    }}


class DownloadBridge:
    def __init__(self, data=PNG, error=None):
        self.calls = []
        self.data = data
        self.error = error

    def call(self, method, params, **options):
        self.calls.append((method, params, options))
        if self.error:
            raise self.error
        return {"data": base64.b64encode(self.data).decode()}


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.temp.name)
        self.store = EventStore(self.root, own_id="12345", started_at=1000, run_id="media-run")
        self.stop = threading.Event()
        self.reports = []
        self.bridge = DownloadBridge()
        self.saver = MediaSaver(self.bridge, self.store, self.reports.append, self.stop)

    def tearDown(self):
        self.saver.close()
        if self.store:
            self.store.close()
        self.temp.cleanup()

    def submit(self, event):
        summary = self.store.accept(event)
        if summary:
            self.saver.submit(event, summary)
        return summary

    def settle(self):
        self.saver.start()
        deadline = time.monotonic() + 3
        while self.saver.items.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNone(self.saver.failure)
        self.assertEqual(self.saver.items.unfinished_tasks, 0)

    def attachment(self):
        return load_messages(self.root / "messenger-modern.sqlite3")[1][0]["attachments"][0]

    def test_user_can_disable_queued_and_new_downloads_then_resume_future_files(self):
        allowed = [True]
        self.saver.allow_download = lambda: allowed[0]
        self.submit(message("queued"))
        allowed[0] = False
        self.submit(message("disabled"))
        self.settle()
        self.assertEqual(self.bridge.calls, [])
        for mid in ("queued", "disabled"):
            self.assertEqual(self.store.relay_snapshot("42", mid)["attachments"][0]["downloadStatus"], "disabled")
        allowed[0] = True
        self.submit(message("future"))
        self.settle()
        self.assertEqual(len(self.bridge.calls), 1)
        media = self.store.relay_snapshot("42", "future")["attachments"][0]
        self.assertEqual((self.root / media["localPath"]).read_bytes(), PNG)

    def test_regular_image_bytes_private_atomic_file_and_view_path(self):
        self.submit(message())
        self.settle()
        media = self.attachment()
        self.assertEqual(media["downloadStatus"], "saved")
        target = self.root / media["localPath"]
        self.assertEqual(target.read_bytes(), PNG)
        self.assertEqual(target.suffix, ".png")
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root / "media").stat().st_mode & 0o777, 0o700)
        self.assertEqual(media["sha256"], hashlib.sha256(PNG).hexdigest())
        self.assertEqual(media["savedBytes"], len(PNG))
        self.assertFalse(list(self.root.rglob(".download-*")))
        self.assertEqual(self.bridge.calls[0][0], "downloadMedia")
        rendered = render_message(load_messages(self.root / "messenger-modern.sqlite3")[1][0], ZoneInfo("UTC"))
        self.assertIn("Saved file: /data/media/", rendered)

    def test_encrypted_download_uses_keys_but_never_persists_them(self):
        attachment = {"type": "image", "mimeType": "image/png", "fileSize": len(PNG),
                      "directPath": "/private/media/path", "mediaKey": KEY, "mediaSha256": DIGEST}
        self.submit(message(encrypted=True, attachments=[attachment]))
        self.settle()
        method, params, _ = self.bridge.calls[0]
        self.assertEqual(method, "downloadE2EEMedia")
        self.assertEqual(params["mediaKey"], KEY)
        self.assertEqual(params["mediaType"], "image")
        self.assertEqual(self.attachment()["downloadStatus"], "saved")
        for file in self.root.rglob("*"):
            if file.is_file():
                self.assertNotIn(KEY.encode(), file.read_bytes())
                self.assertNotIn(b"/private/media/path", file.read_bytes())

    def test_downloaded_media_survives_unsend_and_is_in_deleted_view(self):
        self.submit(message())
        self.settle()
        self.store.accept({"type": "messageUnsend", "timestamp": 1003000, "data": {"threadId": 42, "messageId": "photo-1"}})
        _, rows = load_messages(self.root / "messenger-modern.sqlite3", deleted_only=True)
        media = rows[0]["attachments"][0]
        self.assertEqual((self.root / media["localPath"]).read_bytes(), PNG)
        self.assertIn("Saved file:", render_message(rows[0], ZoneInfo("UTC")))

    def test_history_duplicates_and_non_content_do_not_download(self):
        self.assertIsNone(self.submit(message(sent=999999)))
        self.submit(message())
        self.assertIsNone(self.submit(message()))
        self.assertIsNone(self.submit({"type": "e2eeReceipt", "data": {}}))
        self.settle()
        self.assertEqual(len(self.bridge.calls), 1)
        self.assertEqual(self.store.counts(), {"message": 1})

    def test_filename_cannot_escape_message_directory_and_names_do_not_collide(self):
        attachments = [
            {"type": "image", "url": "https://fbcdn.net/a", "fileName": "../../outside.jpg"},
            {"type": "image", "url": "https://fbcdn.net/b", "fileName": "../../outside.jpg"},
        ]
        self.submit(message(attachments=attachments))
        self.settle()
        files = [self.root / item["localPath"] for item in load_messages(self.root / "messenger-modern.sqlite3")[1][0]["attachments"]]
        self.assertNotEqual(files[0], files[1])
        self.assertEqual(files[0].parent, files[1].parent)
        self.assertTrue(all(file.resolve().is_relative_to(self.root / "media") for file in files))
        self.assertTrue(all(file.suffix == ".png" for file in files))

    def test_missing_encryption_keys_never_saves_ciphertext_or_preview(self):
        self.submit(message(encrypted=True, attachments=[{"type": "image", "url": "https://fbcdn.net/cipher", "previewUrl": "https://fbcdn.net/preview"}]))
        self.settle()
        self.assertEqual(self.bridge.calls, [])
        self.assertEqual(self.attachment()["downloadError"], "missing_download_metadata")
        self.assertFalse(list(self.root.rglob("*.png")))

    def test_link_previews_and_locations_are_not_downloaded(self):
        self.submit(message(attachments=[{"type": "link", "url": "https://example.com"}, {"type": "location", "latitude": 1}]))
        self.settle()
        self.assertEqual(self.bridge.calls, [])

    def test_other_media_types_use_the_expected_encrypted_download_type(self):
        for kind, expected in (("video", "video"), ("voice", "audio"), ("audio", "audio"),
                               ("file", "document"), ("document", "document"), ("sticker", "sticker"), ("gif", "image")):
            self.submit(message(kind, encrypted=True, attachments=[{
                "type": kind, "directPath": "/media/path", "mediaKey": KEY, "mediaSha256": DIGEST,
            }]))
        self.settle()
        self.assertEqual([call[1]["mediaType"] for call in self.bridge.calls],
                         ["video", "audio", "audio", "document", "document", "sticker", "image"])
        self.assertEqual(self.saver.saved, 7)

    def test_advertised_and_actual_size_limits_do_not_stop_message_capture(self):
        self.saver.max_bytes = 10
        self.submit(message("large-advertised", attachments=[{"type": "image", "url": "https://fbcdn.net/a", "fileSize": 11}]))
        self.submit(message("large-actual"))
        self.settle()
        self.assertEqual(len(self.bridge.calls), 1)
        _, rows = load_messages(self.root / "messenger-modern.sqlite3")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["attachments"][0]["downloadError"] == "too_large" for row in rows))
        self.assertEqual(self.store.counts(), {"message": 2})

    def test_failed_download_redacts_provider_details_and_continues(self):
        self.bridge.error = BridgeFailure("PRIVATE_PROVIDER_URL_AND_KEY")
        self.submit(message())
        self.settle()
        self.assertEqual(self.attachment()["downloadStatus"], "failed")
        self.assertEqual(self.attachment()["downloadError"], "download_failed")
        self.assertNotIn("PRIVATE_PROVIDER_URL_AND_KEY", " ".join(self.reports) + json.dumps(self.attachment()))
        self.bridge.error = None
        self.submit(message("next-photo"))
        self.settle()
        self.assertEqual(self.saver.saved, 1)

    def test_invalid_base64_and_integrity_failure_do_not_leave_files(self):
        self.bridge.call = lambda *a, **kw: {"data": "invalid&base64"}
        self.submit(message())
        self.settle()
        self.assertEqual(self.attachment()["downloadError"], "invalid_download_response")
        self.bridge.call = DownloadBridge().call
        self.submit(message("bad-hash", encrypted=True, attachments=[{
            "type": "image", "directPath": "/x", "mediaKey": KEY, "mediaSha256": KEY,
        }]))
        self.settle()
        errors = {item["attachments"][0]["downloadError"] for item in load_messages(self.root / "messenger-modern.sqlite3")[1]}
        self.assertEqual(errors, {"invalid_download_response", "integrity_check_failed"})
        self.assertFalse((self.root / "media").exists())

    def test_queue_overflow_and_shutdown_mark_unfinished_jobs(self):
        self.saver.items = queue.Queue(maxsize=1)
        self.submit(message())
        self.submit(message("overflow"))
        self.saver.close()
        _, rows = load_messages(self.root / "messenger-modern.sqlite3")
        statuses = {item["message_id"]: item["attachments"][0]["downloadStatus"] for item in rows}
        self.assertEqual(statuses, {"photo-1": "interrupted", "overflow": "failed"})
        self.assertEqual(self.bridge.calls, [])

    def test_restart_marks_pending_interrupted_without_backfilling(self):
        self.store.accept(message())
        self.store.close()
        self.store = EventStore(self.root, own_id="12345", started_at=2000, run_id="next-run")
        _, rows = load_messages(self.root / "messenger-modern.sqlite3", run="all")
        self.assertEqual(rows[0]["attachments"][0]["downloadStatus"], "interrupted")
        self.assertEqual(self.bridge.calls, [])
        self.saver.store = self.store

    def test_remote_metadata_cannot_forge_a_saved_local_path(self):
        event = message(attachments=[{"type": "image", "downloadStatus": "saved", "localPath": "../../secret", "sha256": "forged"}])
        self.store.accept(event)
        media = self.attachment()
        self.assertEqual(media["downloadStatus"], "pending")
        self.assertNotIn("localPath", media)
        self.assertNotIn("sha256", media)

    def test_symlinked_media_directory_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "media").symlink_to(outside, target_is_directory=True)
        self.submit(message())
        self.settle()
        self.assertEqual(self.attachment()["downloadError"], "invalid_local_path")
        self.assertEqual(list(outside.iterdir()), [])

    def test_media_limit_configuration_is_bounded(self):
        for value in ("0", "101", "-1", "nonsense", "25.5"):
            with patch.dict(os.environ, {"LOGGER_MEDIA_MAX_MB": value}), self.assertRaises(StoreFailure):
                media_limit()
        with patch.dict(os.environ, {"LOGGER_MEDIA_MAX_MB": "100"}):
            self.assertEqual(media_limit(), 100 * 1024 * 1024)


def rpc_binary(root, *, delay=0.25, instagram=False):
    script = root / "media-bridge"
    code = '''import json,sys,time,base64
connected=False
encrypted=False
for line in sys.stdin:
    req=json.loads(line)
    method=req['method']
    data={}
    if method=='hello':
        data={'bridgeVersion':'2.3.4-logger.2','protocolVersion':1,'capabilities':['newClient','connect','connectE2EE','isConnected','events','structuredConnectionStatus','viewOnceMedia','contactLookup','instagramDMs']}
    elif method=='newClient' and INSTAGRAM:
        assert req['params']['platform']=='instagram' and 'devicePath' not in req['params']
    elif method=='connect':
        connected=True
        data={'user':{'id':12345,'name':'Fixture'}}
        fresh={'type':'message','timestamp':int(time.time()*1000),'data':{'id':'new-photo','threadId':42,'senderId':222,'timestampMs':int(time.time()*1000),'text':'caption','attachments':[{'type':'image','url':'https://fbcdn.net/photo','mimeType':'image/png'}]}}
        if INSTAGRAM:
            data['user']['senderId']='987654'
            fresh['data']['threadId']='340282366841710300949128777'
            fresh['data']['attachments'][0].update(viewOnce=True,viewMode='once',url='https://cdninstagram.com/photo')
        old=json.loads(json.dumps(fresh)); old['data']['id']='old-photo';old['data']['timestampMs']=1
        for event in [old,fresh,fresh]: print(json.dumps({'event':event}),flush=True)
        if INSTAGRAM:
            print(json.dumps({'event':{'type':'messageUnsend','timestamp':int(time.time()*1000),'data':{'messageId':'new-photo','threadId':fresh['data']['threadId']}}}),flush=True)
    elif method=='connectE2EE':
        assert not INSTAGRAM
        encrypted=True
    elif method=='isConnected': data={'connected':connected,'e2eeConnected':encrypted}
    elif method=='downloadMedia':
        print(json.dumps({'event':{'type':'message','timestamp':1002000,'data':{'id':'interleaved','threadId':42,'senderId':222,'timestampMs':1002000,'text':'while downloading'}}}),flush=True)
        time.sleep(DELAY)
        data={'data':ENCODED}
    print(json.dumps({'id':req['id'],'ok':True,'data':data}),flush=True)
'''.replace("DELAY", repr(delay)).replace("ENCODED", repr(base64.b64encode(PNG).decode())).replace("INSTAGRAM", repr(instagram))
    script.write_text("#!" + sys.executable + "\n" + code)
    script.chmod(0o700)
    return script


class MediaIntegrationTests(unittest.TestCase):
    def test_shutdown_interrupts_an_active_download_without_closing_storage_early(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            root = Path(folder)
            stop = threading.Event()
            bridge = Bridge(rpc_binary(root, delay=30), verify=False, stop_event=stop)
            store = EventStore(root, started_at=1000, run_id="shutdown-run")
            saver = MediaSaver(bridge, store, lambda text: None, stop)
            try:
                event = message()
                saver.submit(event, store.accept(event))
                saver.start()
                bridge.events.get(timeout=2)
                stop.set()
                self.assertTrue(saver.close())
                self.assertIsNone(saver.failure)
                self.assertEqual(load_messages(root / "messenger-modern.sqlite3")[1][0]["attachments"][0]["downloadStatus"], "interrupted")
            finally:
                bridge.process.terminate()
                bridge.process.wait(timeout=2)
                bridge.close()
                saver.close()
                store.close()

    def test_health_does_not_time_out_behind_download_and_reader_stays_live(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            root = Path(folder)
            bridge = Bridge(rpc_binary(root), verify=False)
            errors = []
            def download():
                try:
                    bridge.call("downloadMedia", {"url": "https://fbcdn.net/photo"})
                except Exception as error:
                    errors.append(error)
            worker = threading.Thread(target=download)
            try:
                worker.start()
                event = bridge.events.get(timeout=2)
                self.assertEqual(event["data"]["text"], "while downloading")
                with self.assertRaises(BridgeBusy):
                    bridge.call("isConnected", timeout=0.02)
                worker.join(timeout=2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])
                self.assertIsNone(bridge.failure)
                self.assertIs(bridge.call("isConnected")["connected"], False)
            finally:
                bridge.close()
                worker.join(timeout=2)

    def test_runtime_filters_history_and_duplicates_before_download(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            root = Path(folder)
            runtime = logger.LoggerRuntime(root, "UTC", threading.Event())
            with patch("logger.Bridge", side_effect=lambda path, **kw: Bridge(path, verify=False, **kw)), patch.dict(os.environ, {"TELEGRAM_ENABLED": "false"}), contextlib.redirect_stdout(io.StringIO()):
                try:
                    runtime.connect({"c_user": "12345", "xs": "synthetic", "datr": "synthetic"}, rpc_binary(root, delay=0), True)
                    deadline = time.monotonic() + 3
                    while runtime.media.saved < 1 and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertEqual(runtime.messages_this_run, 1)
                    self.assertEqual(runtime.media.saved, 1)
                    self.assertIsNone(runtime.consumer_failure)
                finally:
                    runtime.close()
            _, rows = load_messages(root / "messenger-modern.sqlite3")
            self.assertEqual([item["message_id"] for item in rows], ["new-photo"])
            self.assertEqual((root / rows[0]["attachments"][0]["localPath"]).read_bytes(), PNG)

    def test_instagram_runtime_saves_view_once_bytes_and_matches_unsend(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            root = Path(folder)
            runtime = logger.LoggerRuntime(root, "UTC", threading.Event(), platform="instagram")
            cookies = {"ds_user_id": "12345", "sessionid": "synthetic", "csrftoken": "synthetic"}
            with patch("logger.Bridge", side_effect=lambda path, **kw: Bridge(path, verify=False, **kw)), patch.dict(os.environ, {"TELEGRAM_ENABLED": "false"}), contextlib.redirect_stdout(io.StringIO()):
                try:
                    runtime.connect(cookies, rpc_binary(root, delay=0, instagram=True), False)
                    deadline = time.monotonic() + 3
                    while (runtime.media.saved < 1 or runtime.deleted_this_run < 1) and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertEqual(runtime.messages_this_run, 1)
                    self.assertEqual(runtime.deleted_this_run, 1)
                    self.assertIsNone(runtime.consumer_failure)
                finally:
                    runtime.close()
            _, rows = load_messages(root / "messenger-modern.sqlite3")
            self.assertEqual([item["message_id"] for item in rows], ["new-photo"])
            media = rows[0]["attachments"][0]
            self.assertEqual(media["viewMode"], "once")
            self.assertEqual((root / media["localPath"]).read_bytes(), PNG)


if __name__ == "__main__":
    unittest.main()
