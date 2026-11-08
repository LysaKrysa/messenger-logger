"""Synthetic owner isolation, encrypted payloads, outcomes and explicit resends."""
import base64
import copy
from concurrent.futures import ThreadPoolExecutor
from email.parser import BytesParser
from email.policy import default
import io
import json
import os
from pathlib import Path
import struct
from unittest.mock import patch
import uuid

from dashboard.broker import create_app, PairingReceiver, BotUnavailable
from dashboard.delivery_cache import BLOCK, MAGIC, PayloadUnavailable
from dashboard.deliveries import Deliveries
from dashboard.store import DashboardError
from dashboard.tests.test_dashboard import Base, FakeAPI, BOT, COOKIES, ORIGIN, PASSWORD
from dashboard.web import create_app as web_app

USER, OTHER = 87654321, 22222222
SECRET = "RETAINED_ORIGINAL_TEXT_NOT_METADATA"


class ResultAPI(FakeAPI):
    def __init__(self):
        super().__init__()
        self.response = (200, {"ok": True, "result": {"message_id": 1}})

    def post(self, method, body, **kwargs):
        super().post(method, body, **kwargs)
        return self.response


def multipart(call):
    method, body, options = call
    parsed = BytesParser(policy=default).parsebytes(("MIME-Version: 1.0\r\nContent-Type: " + options["content_type"] + "\r\n\r\n").encode() + body)
    return {part.get_param("name", header="content-disposition"): part.get_payload(decode=True) for part in parsed.iter_parts()}


class HistoryBase(Base):
    def setUp(self):
        super().setUp()
        self.first = self.paired()
        self.second = self.paired(OTHER, COOKIES.replace("111222333", "444555666"))
        self.row = self.store.account(self.first, private=True)
        self.other = self.store.account(self.second, private=True)
        self.api = ResultAPI()
        self.app = create_app(self.store, self.api)
        self.client = self.app.test_client()
        self.history = self.app.extensions["deliveries"]
        self.conversations = self.history.conversations

    def context(self, tid="42", **values):
        return dict(group=uuid.uuid4().hex, thread=tid, name="Study group", kind="deleted", expires=self.now + 600, **values)

    def post(self, method="sendMessage", params=None, context=None, row=None):
        row = row or self.row
        headers = {"Authorization": "Bearer " + self.store.worker_key(row)}
        if context is not None:
            headers["X-Logger-Context"] = base64.urlsafe_b64encode(json.dumps(context).encode()).decode()
        params = params if params is not None else {"chat_id": row["chat_id"], "text": SECRET}
        return self.client.post("/telegram/" + method, headers=headers, **({"data": params} if method == "sendDocument" else {"json": params}))

    def notify(self, **values):
        context = self.context()
        context.update(values)
        self.assertEqual(self.post(context=context).status_code, 200)
        return self.history.rows(self.row)[0]

    def chat(self, row=None, tid="42", name="Study group"):
        row = row or self.row
        self.conversations.report(row, {"revision": row["revision"], "conversations": [{"id": tid, "name": name}]})
        return next(value for value in self.conversations.rows(row) if value["thread_id"] == tid)


class ConversationGatewayTests(HistoryBase):
    def test_policy_is_per_owner_and_capture_and_forwarding_are_independent(self):
        chat = self.chat()
        self.conversations.update(self.row, chat["id"], mode="off")
        value = self.conversations.rows(self.row)[0]
        self.assertTrue(value["capture_enabled"])
        self.assertEqual(value["forward_mode"], "off")
        self.assertEqual(self.store.filters(self.other["id"])["rules"], {})
        response = self.post(context=self.context())
        self.assertEqual(response.json["result"], {"_logger_suppressed": True})
        self.assertFalse(self.api.calls)
        self.assertFalse(self.history.rows(self.row))
        self.conversations.update(self.row, chat["id"], include=False, mode="all")
        self.assertFalse(self.conversations.rows(self.row)[0]["capture_enabled"])
        self.assertEqual(self.post(context=self.context()).json["result"], {"_logger_suppressed": True})
        self.conversations.update(self.row, default=False)
        self.assertEqual(self.post(context=self.context(tid="999")).json["result"], {"_logger_suppressed": True})
        self.conversations.update(self.row, chat["id"], include=True, mode="all")
        context = self.context(); context["kind"] = "message"
        self.assertEqual(self.post(context=context).status_code, 200)
        self.assertEqual(len(self.api.calls), 1)

    def test_legacy_unknown_context_cannot_bypass_configured_filters(self):
        self.assertEqual(self.post().status_code, 200)
        self.conversations.update(self.row, self.chat()["id"], mode="off")
        self.assertEqual(self.post().json["result"], {"_logger_suppressed": True})
        self.assertEqual(len(self.api.calls), 1)

    def test_reports_are_bounded_and_reject_stale_foreign_and_content_fields(self):
        key = {"Authorization": "Bearer " + self.store.worker_key(self.row)}
        value = {"revision": self.row["revision"], "conversations": [{"id": "42", "name": "Friends"}]}
        self.assertEqual(self.client.post("/telegram/reportConversations", json=value, headers=key).status_code, 200)
        for bad in (dict(value, revision=1), dict(value, conversations=value["conversations"] * 51),
                    dict(value, conversations=[{"id": "42", "name": "Friends", "text": SECRET}]),
                    dict(value, conversations=[{"id": "42", "name": "bad\nname"}])):
            self.assertEqual(self.client.post("/telegram/reportConversations", json=bad, headers=key).status_code, 403)
        with self.assertRaises(DashboardError):
            self.conversations.update(self.other, self.chat()["id"], include=False)
        self.store.user_control(USER, "pause")
        with self.assertRaises(DashboardError):
            self.conversations.report(self.row, value)
        self.assertEqual(self.client.post("/telegram/reportConversations", json=value, headers=key).status_code, 403)

    def test_metadata_preserves_known_name_and_survives_pause_but_not_repairing(self):
        chat = self.chat(name="Friends")
        self.conversations.remember(self.row, "42", "")
        self.assertEqual(self.conversations.rows(self.row)[0]["name"], "Friends")
        self.conversations.update(self.row, chat["id"], include=False)
        self.store.user_control(USER, "pause")
        self.assertTrue(self.store.filters(self.first)["rules"])
        self.store.action(self.first, "disconnect")
        self.assertFalse(self.store.filters(self.first)["rules"])
        self.assertFalse(self.conversations.rows(self.store.account(self.first)))

    def test_view_once_only_inherits_the_explicit_account_setting(self):
        context = self.context(); context["kind"] = "view_once"
        self.assertEqual(self.post(context=context).json["result"], {"_logger_suppressed": True})
        self.store.action(self.first, "options", view_once=True)
        self.row = self.store.account(self.first, private=True)
        context["group"] = uuid.uuid4().hex
        self.assertEqual(self.post(context=context).status_code, 200)
        self.assertEqual(len(self.api.calls), 1)
        self.conversations.update(self.row, self.chat()["id"], mode="deleted")
        context["group"] = uuid.uuid4().hex
        self.assertEqual(self.post(context=context).json["result"], {"_logger_suppressed": True})


class HistoryTests(HistoryBase):
    def test_sent_rejected_and_network_outcomes_are_distinct_with_no_automatic_replay(self):
        for response, expected in (((200, {"ok": True}), "sent"), ((400, {"ok": False, "error_code": 400, "description": "PRIVATE_PROVIDER_DATA"}), "failed"), ((502, {"ok": False, "error_code": 502}), "uncertain")):
            self.api.response = response
            context = self.context()
            self.post(context=context)
            item = self.history.rows(self.row)[0]
            self.assertEqual(item["outcome"], expected)
            self.assertEqual(self.post(context=context).status_code, 403)
        self.api.error = True
        self.post(context=self.context())
        self.assertEqual(self.history.rows(self.row)[0]["outcome"], "uncertain")
        self.assertEqual(len(self.api.calls), 4)
        with self.store.db() as db:
            dump = "\n".join(db.iterdump()).encode()
        self.assertNotIn(b"PRIVATE_PROVIDER_DATA", dump)
        self.assertNotIn(SECRET.encode(), dump)

    def test_only_definite_topic_missing_may_retry_same_group_once(self):
        context = self.context()
        self.api.response = (400, {"ok": False, "error_code": 400, "description": "Bad Request: message thread not found"})
        self.post(context=context)
        first = self.history.rows(self.row)[0]
        self.assertEqual(first["reason"], "topic_missing")
        self.api.response = (200, {"ok": True})
        self.post(context=context)
        values = self.history.rows(self.row)
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0]["attempts"], 2)
        self.assertEqual(values[0]["outcome"], "sent")
        self.assertEqual(self.post(context=context).status_code, 403)
        self.assertEqual(len(self.api.calls), 2)

    def test_resend_needs_acknowledgement_is_single_use_and_retains_original_content(self):
        item = self.notify()
        token = self.history.intent(self.row, item["id"])
        with self.assertRaises(DashboardError):
            self.history.resend(self.row, token)
        self.assertEqual(len(self.api.calls), 1)
        with self.assertRaises(DashboardError):
            self.history.resend(self.other, token, acknowledged=True)
        self.now += 10
        child = self.history.resend(self.row, token, acknowledged=True)
        self.assertEqual(child["outcome"], "sent")
        self.assertEqual(child["expires"], item["expires"])
        self.assertEqual(child["retry_of"], item["id"])
        self.assertEqual(self.history.get(self.row, item["id"])["outcome"], "sent")
        self.assertEqual(json.loads(self.api.calls[-1][1]), {"chat_id": USER, "text": SECRET})
        with self.assertRaises(DashboardError):
            self.history.resend(self.row, token, acknowledged=True)
        self.assertEqual(len(self.api.calls), 2)

    def test_concurrent_confirmation_sends_once(self):
        item = self.notify()
        token = self.history.intent(self.row, item["id"])
        def resend(_):
            try:
                return self.history.resend(self.row, token, acknowledged=True)
            except DashboardError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(resend, range(2)))
        self.assertEqual(sum(value is not None for value in values), 1)
        self.assertEqual(len(self.api.calls), 2)

    def test_paused_and_quiet_owner_may_explicitly_resend_but_filters_still_block(self):
        item = self.notify()
        token = self.history.intent(self.row, item["id"])
        pair = self.row["pair_epoch"]
        self.store.user_control(USER, "pause")
        self.store.user_control(USER, "quiet", 3600)
        paused = self.store.account(self.first, private=True)
        self.assertEqual(paused["pair_epoch"], pair)
        self.assertEqual(self.history.resend(paused, token, acknowledged=True)["outcome"], "sent")
        token = self.history.intent(paused, item["id"])
        self.conversations.update(paused, self.chat(paused)["id"], include=False)
        with self.assertRaises(DashboardError):
            self.history.resend(paused, token, acknowledged=True)
        self.assertEqual(len(self.api.calls), 2)

    def test_unlink_repair_remove_or_expiry_invalidates_history_cache_and_intents(self):
        item = self.notify()
        token = self.history.intent(self.row, item["id"])
        path = self.history.cache.path(item["id"])
        self.assertTrue(path.is_file())
        self.store.action(self.first, "disconnect")
        with self.assertRaises(DashboardError):
            self.history.resend(self.row, token, acknowledged=True)
        invite = self.store.invite(self.first).split("start=")[1]
        self.assertTrue(self.store.claim_invite(invite, USER, "Same owner"))
        self.store.action(self.first, "approve")
        self.history.cleanup(force=True)
        self.assertFalse(path.exists())
        self.assertIsNone(self.history.get(self.store.account(self.first), item["id"]))
        self.store.action(self.first, "start")
        self.row = self.store.account(self.first, private=True)
        item = self.notify()
        self.now = item["expires"]
        self.history.cleanup(force=True)
        self.assertIsNone(self.history.get(self.row, item["id"]))
        self.assertFalse(self.history.cache.path(item["id"]).exists())


class HistoryMenuTests(HistoryBase):
    def setUp(self):
        super().setUp()
        self.receiver = self.app.extensions["pairing"]
        self.sequence = 0

    def command(self, text, owner=USER):
        self.sequence += 1
        self.receiver.handle({"update_id": self.sequence, "message": {"text": text, "from": {"id": owner, "is_bot": False}, "chat": {"id": owner, "type": "private"}}})
        return json.loads(self.api.calls[-1][1])

    def callback(self, data, owner=USER, **changes):
        self.sequence += 1
        query = {"id": "menu_" + str(self.sequence), "data": data, "from": {"id": owner, "is_bot": False},
                 "message": {"message_id": 17, "from": {"id": BOT["id"], "is_bot": True}, "chat": {"id": owner, "type": "private"}}}
        query.update(changes)
        self.receiver.handle({"update_id": self.sequence, "callback_query": query})
        return json.loads(self.api.calls[-1][1])

    def test_localized_chats_and_deliveries_commands_are_owned_and_filter_changes_are_independent(self):
        first = self.chat(name="Friends PL")
        second = self.chat(self.other, name="Private other owner")
        reply = self.command("/rozmowy")
        self.assertIn("Twoje rozmowy", reply["text"])
        self.assertIn("Friends PL", json.dumps(reply["reply_markup"]))
        self.assertNotIn("Private other owner", json.dumps(reply))
        self.command("/en")
        self.assertIn("Your conversations", self.command("/rozmowy")["text"])
        self.callback("fc." + first["id"] + ".off")
        self.callback("cc." + first["id"] + ".on")
        rule = self.store.filters(self.first)["rules"]["42"]
        self.assertTrue(rule["capture"])
        self.assertEqual(rule["forward"], "off")
        self.callback("cd.include")
        self.assertFalse(self.store.filters(self.first)["capture_default"])
        self.assertEqual(self.store.filters(self.second)["rules"], {})
        self.assertIn("Twoje rozmowy", self.command("/chats", OTHER)["text"])
        self.assertIn("Twoje wysyłki", self.command("/deliveries", OTHER)["text"])
        self.assertIn("Your Telegram deliveries", self.command("/wysyłki")["text"])

    def test_resend_button_requires_owner_confirmation_once_and_warns_about_duplicates(self):
        item = self.notify()
        reply = self.command("/deliveries")
        self.assertIn("Wysłano", reply["text"])
        confirmation = self.callback("dl." + item["id"])
        self.assertIn("duplikat", confirmation["text"])
        data = confirmation["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        original_sends = sum(method == "sendMessage" for method, _, _ in self.api.calls)
        # The confirmation button is unusable by another paired person.
        self.callback(data, OTHER)
        self.assertEqual(sum(method == "sendMessage" for method, _, _ in self.api.calls), original_sends)
        result = self.callback(data)
        self.assertIn("Wynik ponowienia: Wysłano", result["text"])
        self.assertEqual(sum(method == "sendMessage" for method, _, _ in self.api.calls), original_sends + 1)
        self.callback(data)
        self.assertEqual(sum(method == "sendMessage" for method, _, _ in self.api.calls), original_sends + 1)

    def test_forwarded_group_bot_and_foreign_callbacks_do_not_modify_or_reveal_state(self):
        own = self.chat()
        foreign = self.chat(self.other)
        self.callback("cf." + foreign["id"])
        self.assertEqual(self.api.calls[-1][0], "answerCallbackQuery")
        for changes in ({"from": {"id": USER, "is_bot": True}}, {"message": {"message_id": 1, "chat": {"id": USER, "type": "group"}, "from": {"id": BOT["id"], "is_bot": True}}}, {"message": {"message_id": 1, "chat": {"id": USER, "type": "private"}, "from": {"id": 999, "is_bot": True}}}):
            self.callback("cc." + own["id"] + ".off", **changes)
            self.assertEqual(self.store.filters(self.first)["rules"], {})
            self.assertEqual(self.api.calls[-1][0], "answerCallbackQuery")
        item = self.notify()
        self.callback("dl." + item["id"], OTHER)
        self.assertEqual(self.api.calls[-1][0], "answerCallbackQuery")
        self.assertNotIn(SECRET.encode(), self.api.calls[-1][1])


class HistoryWebTests(HistoryBase):
    def setUp(self):
        super().setUp()
        self.store.set_password(PASSWORD)
        self.web = web_app(self.store, origin=ORIGIN, testing=True, delivery_api=self.api)
        self.browser = self.web.test_client()
        with self.browser.session_transaction(base_url=ORIGIN) as session:
            session.update(sid=self.store.login(PASSWORD, "synthetic"), csrf="synthetic-csrf")

    def url(self, suffix):
        return "/accounts/" + self.first + suffix

    def test_pages_show_metadata_and_independent_policy_controls_with_csrf_and_origin_checks(self):
        chat = self.chat()
        self.notify()
        for suffix in ("/conversations", "/deliveries"):
            response = self.browser.get(self.url(suffix), base_url=ORIGIN)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(SECRET.encode(), response.data)
            anonymous = self.web.test_client().get(self.url(suffix), base_url=ORIGIN)
            self.assertEqual(anonymous.status_code, 302)
        data = {"action": "rule", "conversation": chat["id"], "capture": "include", "forward": "off"}
        self.assertEqual(self.browser.post(self.url("/conversations"), data=data, base_url=ORIGIN).status_code, 403)
        data["csrf"] = "synthetic-csrf"
        self.assertEqual(self.browser.post(self.url("/conversations"), data=data, base_url=ORIGIN, headers={"Origin": "https://evil.test"}).status_code, 403)
        self.assertEqual(self.browser.post(self.url("/conversations"), data=data, base_url=ORIGIN).status_code, 302)
        self.assertEqual(self.store.filters(self.first)["rules"]["42"]["forward"], "off")
        foreign = self.chat(self.other)
        data["conversation"] = foreign["id"]
        self.assertEqual(self.browser.post(self.url("/conversations"), data=data, base_url=ORIGIN).status_code, 400)

    def test_resend_get_is_read_only_and_post_needs_acknowledged_matching_single_use_confirmation(self):
        first = self.notify()
        second = self.notify()
        path = self.url("/deliveries/" + first["id"] + "/resend")
        response = self.browser.get(path, base_url=ORIGIN)
        self.assertIn(b"This may create a duplicate", response.data)
        self.assertNotIn(SECRET.encode(), response.data)
        self.assertEqual(len(self.api.calls), 2)
        import re
        token = re.search(rb'name="confirmation" value="([a-f0-9]+)"', response.data)[1].decode()
        data = {"csrf": "synthetic-csrf", "confirmation": token}
        self.assertEqual(self.browser.post(path, data=data, base_url=ORIGIN).status_code, 400)
        data["acknowledge"] = "yes"
        other_path = self.url("/deliveries/" + second["id"] + "/resend")
        self.assertEqual(self.browser.post(other_path, data=data, base_url=ORIGIN).status_code, 400)
        self.assertEqual(self.browser.post(path, data=data, base_url=ORIGIN).status_code, 302)
        self.assertEqual(self.browser.post(path, data=data, base_url=ORIGIN).status_code, 400)
        self.assertEqual(len(self.api.calls), 3)
        foreign = self.history.begin(self.other, "sendMessage", self.context())
        self.assertEqual(self.browser.get(self.url("/deliveries/" + foreign + "/resend"), base_url=ORIGIN).status_code, 404)



class HistoryLifecycleTests(HistoryBase):
    def test_filter_change_during_confirmation_keeps_valid_cached_content_for_later(self):
        from contextlib import contextmanager
        original = self.notify()
        chat = self.chat()
        token = self.history.intent(self.row, original["id"])
        read = self.history.cache.read
        @contextmanager
        def change_after_validation(*args):
            with read(*args) as payload:
                self.conversations.update(self.row, chat["id"], include=False)
                yield payload
        with patch.object(self.history.cache, "read", change_after_validation):
            child = self.history.resend(self.row, token, acknowledged=True)
        self.assertEqual(child["outcome"], "failed")
        self.assertEqual(child["reason"], "not_sent")
        self.assertEqual(len(self.api.calls), 1)
        self.conversations.update(self.row, chat["id"], include=True)
        self.assertTrue(self.history.get(self.row, original["id"])["available"])
        self.assertEqual(self.history.resend(self.row, self.history.intent(self.row, original["id"]), acknowledged=True)["outcome"], "sent")

    def test_stale_sending_turns_uncertain_without_network_or_extension(self):
        ident = self.history.begin(self.row, "sendMessage", self.context())
        self.history.save(self.row, ident, "sendMessage", {"chat_id": USER, "text": SECRET})
        self.now += 121
        self.history.cleanup(force=True)
        self.assertEqual(self.history.get(self.row, ident)["outcome"], "uncertain")
        self.assertFalse(self.api.calls)

    def test_definite_failed_and_uncertain_manual_resends_keep_both_attempt_results(self):
        original = self.notify()
        for response, outcome in (((400, {"ok": False, "error_code": 400}), "failed"), ((502, {"ok": False}), "uncertain")):
            self.api.response = response
            child = self.history.resend(self.row, self.history.intent(self.row, original["id"]), acknowledged=True)
            self.assertEqual(child["outcome"], outcome)
            self.assertEqual(self.history.get(self.row, original["id"])["outcome"], "sent")
        self.assertEqual(len(self.api.calls), 3)

    def test_quota_does_not_block_initial_send_or_offer_unavailable_resend(self):
        self.history.account_limit = 1
        item = self.notify()
        self.assertEqual(item["outcome"], "sent")
        self.assertFalse(item["available"])
        self.assertEqual(item["cache_reason"], "quota")
        with self.assertRaises(PayloadUnavailable):
            self.history.intent(self.row, item["id"])
        self.assertEqual(len(self.api.calls), 1)

    def test_context_and_expired_payload_validation_never_send(self):
        for bad in ("!", "a===", base64.urlsafe_b64encode(b"[]").decode()):
            headers = {"Authorization": "Bearer " + self.store.worker_key(self.row), "X-Logger-Context": bad}
            response = self.client.post("/telegram/sendMessage", json={"chat_id": USER, "text": SECRET}, headers=headers)
            self.assertEqual(response.status_code, 403)
        context = self.context(); context["expires"] = self.now
        self.assertEqual(self.post(context=context).json["result"], {"_logger_suppressed": True})
        self.assertFalse(self.api.calls)


class CacheTests(HistoryBase):
    def upload(self, body, filename="voice.ogg"):
        self.api.response = (502, {"ok": False, "error_code": 502})
        response = self.post("sendDocument", {"chat_id": str(USER), "message_thread_id": "7", "caption": SECRET, "document": (io.BytesIO(body), filename)}, self.context())
        self.assertEqual(response.status_code, 502)
        return self.history.rows(self.row)[0]

    def test_failed_voice_and_zip_resend_as_single_exact_document_with_download_button(self):
        for filename in ("voice.ogg", "message-attachments.zip"):
            body = b"OggS_SYNTHETIC_MEDIA" + bytes(range(256)) * 4096
            item = self.upload(body, filename)
            path = self.history.cache.path(item["id"])
            self.assertNotIn(body[:100], path.read_bytes())
            self.assertNotIn(SECRET.encode(), path.read_bytes())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.history.cache.root.stat().st_mode & 0o777, 0o700)
            self.api.response = (200, {"ok": True})
            before = len(self.api.calls)
            self.conversations.topic(self.row, "42", 19)
            result = self.history.resend(self.row, self.history.intent(self.row, item["id"]), acknowledged=True)
            self.assertEqual(result["outcome"], "sent")
            self.assertEqual(len(self.api.calls), before + 1)
            fields = multipart(self.api.calls[-1])
            self.assertEqual(fields["document"], body)
            self.assertEqual(fields["caption"].decode(), SECRET)
            self.assertEqual(fields["message_thread_id"], b"19")
            self.assertIn("callback_data", fields["reply_markup"].decode())
            self.assertEqual(self.api.calls[-1][2]["length"], len(self.api.calls[-1][1]))

    def test_confirmed_document_uses_telegram_file_id_without_reuploading_bytes(self):
        self.api.response = (200, {"ok": True, "result": {"document": {"file_id": "SYNTHETIC_FILE_ID_123456789"}}})
        self.post("sendDocument", {"chat_id": str(USER), "caption": SECRET, "document": (io.BytesIO(b"VOICE" * 50000), "voice.ogg")}, self.context())
        item = self.history.rows(self.row)[0]
        self.assertLess(item["cache_size"], 4096)
        child = self.history.resend(self.row, self.history.intent(self.row, item["id"]), acknowledged=True)
        self.assertEqual(child["outcome"], "sent")
        fields = json.loads(self.api.calls[-1][1])
        self.assertEqual(fields["document"], "SYNTHETIC_FILE_ID_123456789")
        self.assertIsInstance(fields["reply_markup"], dict)
        self.assertIs(fields["disable_content_type_detection"], True)

    def test_corrupt_truncated_reordered_cross_account_and_symlink_cache_never_send(self):
        item = self.upload(b"SECRET_BINARY" * (BLOCK // 2))
        path = self.history.cache.path(item["id"])
        original = path.read_bytes()
        size = struct.unpack(">I", original[len(MAGIC):len(MAGIC) + 4])[0]
        first_end = len(MAGIC) + 4 + size
        block_size = struct.unpack(">I", original[first_end:first_end + 4])[0]
        second_end = first_end + 4 + block_size
        variants = (original[:-1], original[:first_end] + original[second_end:] + original[first_end:second_end], original[:-40] + b"x" * 40)
        for changed in variants:
            path.write_bytes(changed)
            with self.assertRaises(PayloadUnavailable):
                with self.history.cache.read(self.first, item["id"]):
                    pass
        path.write_bytes(original)
        with self.assertRaises(PayloadUnavailable):
            with self.history.cache.read(self.second, item["id"]):
                pass
        target = self.root / "foreign.cache"; target.write_bytes(original)
        path.unlink(); path.symlink_to(target)
        with self.assertRaises(PayloadUnavailable):
            with self.history.cache.read(self.first, item["id"]):
                pass
        path.unlink(); path.write_bytes(original[:-1])
        token = self.history.intent(self.row, item["id"])
        before = len(self.api.calls)
        child = self.history.resend(self.row, token, acknowledged=True)
        self.assertEqual(child["outcome"], "failed")
        self.assertEqual(child["reason"], "payload_unavailable")
        self.assertEqual(len(self.api.calls), before)
        self.assertFalse(self.history.get(self.row, item["id"])["available"])

    def test_cache_quota_applies_globally_and_expired_cache_is_removed_without_history_visit(self):
        self.history.total_limit = 1
        item = self.upload(b"VOICE")
        self.assertEqual(item["cache_reason"], "quota")
        self.assertFalse(item["available"])
        self.history.total_limit = 500 * 1024 * 1024
        item = self.upload(b"VOICE")
        self.assertTrue(self.history.cache.path(item["id"]).exists())
        self.now = item["expires"]
        self.app.extensions["pairing"].connection_alerts.maintenance()
        self.assertFalse(self.history.cache.path(item["id"]).exists())
