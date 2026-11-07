"""Shared-bot file callbacks: bytes, ownership, topic and replay safety."""
import copy
import io
import json
from email.parser import BytesParser
from email.policy import default

from dashboard.broker import DOWNLOAD_CALLBACK, PairingReceiver, create_app
from dashboard.tests.test_dashboard import Base, FakeAPI, BOT, COOKIES


class DownloadButtonTests(Base):
    def setUp(self):
        super().setUp()
        self.ident = self.paired()
        self.key = self.store.worker_key(self.store.account(self.ident, private=True))
        self.api = FakeAPI()
        self.client = create_app(self.store, self.api).test_client()
        self.receiver = PairingReceiver(self.store, self.api)

    def update(self, ident=10, **extra):
        value = {"update_id": ident, "callback_query": {
            "id": "synthetic_callback_" + str(ident), "data": DOWNLOAD_CALLBACK,
            "from": {"id": 87654321, "is_bot": False},
            "message": {"message_id": 45, "message_thread_id": 7,
                        "chat": {"id": 87654321, "type": "private"},
                        "from": {"id": BOT["id"], "is_bot": True},
                        "document": {"file_id": "SYNTHETIC_DOCUMENT_FILE_ID_12345", "file_name": "voice.ogg"}}}}
        value.update(extra)
        return value

    def decoded(self, method):
        return [json.loads(body) for name, body, _ in self.api.calls if name == method]

    def upload(self, filename="voice.ogg", **extra):
        return self.client.post("/telegram/sendDocument", headers={"Authorization": "Bearer " + self.key},
                                data={"chat_id": "87654321", "message_thread_id": "7",
                                      "caption": "Original deleted message", **extra,
                                      "document": (io.BytesIO(b"SYNTHETIC_VOICE_BYTES"), filename)})

    def test_future_voice_and_attachment_uploads_have_gateway_owned_buttons(self):
        for filename, label in (("voice.ogg", "⬇ Pobierz plik"), ("photo.jpg", "⬇ Pobierz plik"),
                                ("message-attachments.zip", "⬇ Pobierz ZIP")):
            with self.subTest(filename=filename):
                self.api.calls.clear()
                response = self.upload(filename)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(self.api.calls), 1, "Original notification was split")
                method, raw, options = self.api.calls[0]
                parts = BytesParser(policy=default).parsebytes(
                    ("MIME-Version: 1.0\r\nContent-Type: " + options["content_type"] + "\r\n\r\n").encode() + raw)
                fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True) for part in parts.iter_parts()}
                self.assertEqual(fields["document"], b"SYNTHETIC_VOICE_BYTES")
                self.assertEqual(fields["caption"], b"Original deleted message")
                self.assertEqual(fields["disable_content_type_detection"], b"true")
                self.assertEqual(json.loads(fields["reply_markup"]), {"inline_keyboard": [[{"text": label, "callback_data": DOWNLOAD_CALLBACK}]]})
                self.assertEqual(options["length"], len(raw))
                self.assertEqual(method, "sendDocument")

    def test_button_requests_exactly_one_file_copy_in_original_private_topic(self):
        update = self.update()
        self.receiver.handle(update)
        copies = self.decoded("sendDocument")
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0]["document"], "SYNTHETIC_DOCUMENT_FILE_ID_12345")
        self.assertEqual(copies[0]["chat_id"], 87654321)
        self.assertEqual(copies[0]["message_thread_id"], 7)
        self.assertEqual(copies[0]["reply_parameters"], {"message_id": 45, "allow_sending_without_reply": False})
        self.assertTrue(copies[0]["disable_content_type_detection"])
        self.assertNotIn("reply_markup", copies[0], "Copy should not create a chain of download buttons")
        self.assertEqual(len(self.decoded("answerCallbackQuery")), 1)
        self.receiver.handle(copy.deepcopy(update))
        self.assertEqual(len(self.decoded("sendDocument")), 1, "A repeated update replayed a file")

    def test_regular_private_chat_without_topic_and_paused_logger_can_download(self):
        self.store.action(self.ident, "stop")
        value = self.update()
        del value["callback_query"]["message"]["message_thread_id"]
        self.receiver.handle(value)
        self.assertEqual(len(self.decoded("sendDocument")), 1)
        self.assertNotIn("message_thread_id", self.decoded("sendDocument")[0])

    def test_cross_user_other_bot_group_pending_and_revoked_destinations_cannot_download(self):
        variants = []
        for path, value in ((("from", "id"), 22222222), (("from", "is_bot"), True),
                            (("message", "chat", "type"), "supergroup"),
                            (("message", "chat", "id"), 22222222),
                            (("message", "from", "id"), 999999),
                            (("message", "from", "is_bot"), False)):
            update = self.update()
            target = update["callback_query"]
            for segment in path[:-1]:
                target = target[segment]
            target[path[-1]] = value
            variants.append(update)
        for index, update in enumerate(variants, 10):
            update["update_id"] = index
            self.receiver.handle(update)
        self.assertEqual(self.decoded("sendDocument"), [])
        self.store.action(self.ident, "disconnect")
        self.receiver.handle(self.update(100))
        self.assertEqual(self.decoded("sendDocument"), [])

    def test_unapproved_pairing_and_removed_account_are_unavailable(self):
        other = self.store.add_account("Other", COOKIES.replace("111222333", "444555666"))
        token = self.store.invite(other).split("start=")[1]
        self.store.claim_invite(token, 22222222, "@synthetic_other")
        update = self.update()
        update["callback_query"]["from"]["id"] = 22222222
        update["callback_query"]["message"]["chat"]["id"] = 22222222
        self.receiver.handle(update)
        self.assertEqual(self.decoded("sendDocument"), [])
        self.store.action(self.ident, "remove")
        self.receiver.handle(self.update(11))
        self.assertEqual(self.decoded("sendDocument"), [])

    def test_malformed_inaccessible_and_url_payloads_never_request_a_file(self):
        for index, change in enumerate(("missing_document", "url", "path", "topic", "message_id", "sender"), 10):
            value = self.update(index)
            query = value["callback_query"]
            if change == "missing_document":
                del query["message"]["document"]
            elif change == "url":
                query["message"]["document"]["file_id"] = "https://example.test/private"
            elif change == "path":
                query["message"]["document"]["file_id"] = "/private/credential-file"
            elif change == "topic":
                query["message"]["message_thread_id"] = -1
            elif change == "message_id":
                query["message"]["message_id"] = True
            elif change == "sender":
                query["from"] = []
            self.receiver.handle(value)
        self.assertEqual(self.decoded("sendDocument"), [])
        value = self.update(100)
        value["callback_query"]["data"] = DOWNLOAD_CALLBACK + ":ANOTHER_FILE_ID"
        self.receiver.handle(value)
        self.assertEqual(self.decoded("sendDocument"), [])

    def test_rapid_distinct_taps_are_limited_without_automatic_replay(self):
        self.receiver.handle(self.update())
        self.receiver.handle(self.update(11))
        self.assertEqual(len(self.decoded("sendDocument")), 1)
        self.assertTrue(self.decoded("answerCallbackQuery")[-1]["show_alert"])
        self.now += 16
        self.receiver.handle(self.update(12))
        self.assertEqual(len(self.decoded("sendDocument")), 2)

    def test_unconfirmed_delivery_is_not_replayed_after_receiver_restart(self):
        self.api.error = True
        value = self.update()
        self.receiver.handle(value)
        self.assertEqual(len(self.decoded("sendDocument")), 1)
        replacement = PairingReceiver(self.store, self.api)
        replacement.handle(value)
        self.assertEqual(len(self.decoded("sendDocument")), 1)

    def test_single_receiver_polls_callback_queries_alongside_pairing_messages(self):
        calls = []
        def api_json(method, params):
            calls.append((method, params))
            if method == "getWebhookInfo":
                return {"url": ""}
            if method == "getUpdates":
                self.receiver.stop.set()
                return []
            raise AssertionError(method)
        self.api.json = api_json
        self.receiver.run()
        params = next(params for method, params in calls if method == "getUpdates")
        self.assertEqual(params["allowed_updates"], ["message", "callback_query"])

    def test_worker_cannot_inject_buttons_or_call_callback_and_file_apis(self):
        self.assertEqual(self.upload(reply_markup=json.dumps({"inline_keyboard": [[{"text": "x", "url": "https://example.test"}]]})).status_code, 403)
        for method in ("answerCallbackQuery", "getFile", "copyMessage"):
            response = self.client.post("/telegram/" + method, json={}, headers={"Authorization": "Bearer " + self.key})
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.api.calls, [])
