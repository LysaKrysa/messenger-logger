"""One bot receiver and a destination-checked, paced Telegram gateway."""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from .store import DashboardError, from_environment

METHODS = {
    "getMe": set(), "getChat": {"chat_id"},
    "getChatMember": {"chat_id", "user_id"},
    "createForumTopic": {"chat_id", "name", "icon_color"},
    "editForumTopic": {"chat_id", "message_thread_id", "name"},
    "sendMessage": {"chat_id", "message_thread_id", "text", "entities", "disable_notification", "link_preview_options"},
    "sendDocument": {"chat_id", "message_thread_id", "caption", "caption_entities", "disable_notification"},
}
MAX_FILE = 50_000_000
MAX_FORM_FIELD = 32768
# Werkzeug reads multipart input in 64 KiB chunks. This decoder window must
# fit a chunk plus boundary/header tails, independently of each text field.
MULTIPART_MEMORY = 128 * 1024
DOWNLOAD_CALLBACK = "download_document:v1"


def download_markup(filename):
    label = "⬇ Download ZIP" if filename.lower().endswith(".zip") else "⬇ Download file"
    return {"inline_keyboard": [[{"text": label, "callback_data": DOWNLOAD_CALLBACK}]]}


class BotUnavailable(RuntimeError):
    pass


class Pacer:
    def __init__(self):
        self.lock, self.chats, self.next = threading.Lock(), {}, 0.0

    def wait(self, chat):
        now = time.monotonic()
        with self.lock:
            when = max(now, self.next, self.chats.get(str(chat), 0.0))
            self.next = when + .05
            if chat is not None:
                self.chats[str(chat)] = when + 1.1
            self.chats = {key: value for key, value in self.chats.items() if value > now - 60}
        time.sleep(max(0, when - now))


class TelegramAPI:
    def __init__(self, store, opener=urlopen):
        self.store, self.opener, self.pacer = store, opener, Pacer()
        self.slots = threading.BoundedSemaphore(4)

    def post(self, method, body, *, chat=None, content_type="application/json", length=None, timeout=35):
        bot = self.store.bot(token=True)
        if not bot:
            raise BotUnavailable
        if not self.slots.acquire(timeout=10):
            raise BotUnavailable
        try:
            if method not in {"getUpdates", "getWebhookInfo", "getMe"}:
                self.pacer.wait(chat)
            headers = {"Content-Type": content_type}
            if length is not None:
                headers["Content-Length"] = str(length)
            req = Request("https://api.telegram.org/bot" + bot["token"] + "/" + method, data=body, headers=headers, method="POST")
            try:
                with self.opener(req, timeout=timeout) as response:
                    raw = response.read(1024 * 1024 + 1)
                    status = response.status
            except HTTPError as error:
                try:
                    raw, status = error.read(8193), error.code
                finally:
                    error.close()
            if len(raw) > 1024 * 1024:
                raise BotUnavailable
            payload = json.loads(raw)
            if not isinstance(payload, dict) or type(payload.get("ok")) is not bool:
                raise BotUnavailable
            return status, payload
        except (OSError, ValueError, URLError):
            raise BotUnavailable from None
        finally:
            self.slots.release()

    def json(self, method, params):
        status, payload = self.post(method, json.dumps(params).encode(), chat=params.get("chat_id"))
        if payload.get("ok") is not True:
            raise BotUnavailable
        return payload.get("result")


class PairingReceiver:
    def __init__(self, store, api):
        self.store, self.api = store, api
        self.stop = threading.Event()

    def handle(self, update):
        ident = update.get("update_id")
        if type(ident) is not int or ident <= self.store.setting("bot_offset", -1):
            return
        # Commit receipt before any reply: an ambiguous reply must not be replayed.
        self.store.set_setting("bot_offset", ident)
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            self.download(callback)
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat, sender = message.get("chat", {}), message.get("from", {})
        chat_id = chat.get("id")
        if chat.get("type") != "private" or type(chat_id) is not int or chat_id <= 0 or sender.get("id") != chat_id or sender.get("is_bot"):
            return
        if not self.store.take_limit("pair:" + str(chat_id), 6, 60):
            return
        text = message.get("text", "")
        if not isinstance(text, str) or len(text) > 256:
            return
        command, _, parameter = text.partition(" ")
        command = command.split("@", 1)[0]
        if command == "/start" and parameter:
            username = sender.get("username")
            label = "@" + username if isinstance(username, str) and re.fullmatch(r"[A-Za-z0-9_]{1,64}", username) else "Telegram user " + str(chat_id)
            accepted = self.store.claim_invite(parameter.strip(), chat_id, label)
            reply = ("Pairing requested. Ask the administrator to approve your Telegram identity in the dashboard. "
                     "Your linked Messenger messages and media can then be saved and relayed here. "
                     "The server administrator has access to that account's archive. Local copies expire after three days; Telegram copies remain. "
                     "Use /disconnect to cancel or stop logging.") if accepted else "This invitation is expired, already used, or unavailable. Ask the administrator for a new invitation."
        elif command in {"/disconnect", "/stop"}:
            self.store.disconnect_user(chat_id)
            reply = "Telegram pairing removed. Your linked logger is being stopped. Existing Telegram copies remain."
        elif command == "/privacy":
            reply = "This bot delivers copies from a Messenger account linked by the administrator. The administrator can access stored credentials and archives. Local message copies expire after three days; Telegram copies remain. Use /disconnect to stop your linked logger."
        elif command in {"/start", "/help"}:
            reply = "Open the administrator's invitation link to request pairing. Your account remains separate from other users. Commands: /privacy and /disconnect. Never send Messenger cookies or passwords to this bot."
        else:
            return
        try:
            self.api.json("sendMessage", {"chat_id": chat_id, "text": reply, "disable_notification": True})
        except BotUnavailable:
            pass  # Pairing state is durable; do not repeat an uncertain reply.

    def answer_download(self, query_id, text, *, alert=False):
        try:
            self.api.json("answerCallbackQuery", {"callback_query_id": query_id, "text": text,
                                                 "show_alert": alert, "cache_time": 0})
        except BotUnavailable:
            pass  # A failed acknowledgement does not replay a download.

    def download(self, query):
        if query.get("data") != DOWNLOAD_CALLBACK:
            return
        query_id = query.get("id")
        if not isinstance(query_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", query_id):
            return
        sender, message = query.get("from"), query.get("message")
        if not isinstance(sender, dict) or not isinstance(message, dict):
            self.answer_download(query_id, "This download is unavailable.", alert=True)
            return
        sender_id = sender.get("id")
        if type(sender_id) is int and sender_id > 0 and not self.store.take_limit("download-callback:" + str(sender_id), 12, 60):
            return
        chat, author, document = message.get("chat"), message.get("from"), message.get("document")
        bot = self.store.bot()
        owner = sender.get("id")
        valid = (type(owner) is int and owner > 0 and sender.get("is_bot") is False
                 and isinstance(chat, dict) and chat.get("type") == "private"
                 and type(chat.get("id")) is int and chat["id"] == owner
                 and isinstance(author, dict) and bot and author.get("id") == bot["id"]
                 and author.get("is_bot") is True and isinstance(document, dict))
        account = next((row for row in self.store.accounts() if row["chat_id"] == owner), None) if valid else None
        file_id = document.get("file_id") if isinstance(document, dict) else None
        message_id, topic_id = message.get("message_id"), message.get("message_thread_id")
        # Only a Telegram-provided document ID is reused: never accept URLs,
        # filesystem paths or a file ID supplied through callback_data.
        valid = (account is not None and isinstance(file_id, str)
                 and re.fullmatch(r"[A-Za-z0-9_-]{16,1024}", file_id)
                 and type(message_id) is int and 0 < message_id < 2**53
                 and (topic_id is None or type(topic_id) is int and 0 < topic_id < 2**53))
        if not valid:
            self.answer_download(query_id, "This download is unavailable for this conversation.", alert=True)
            return
        if not self.store.take_limit("download-user:" + str(owner), 6, 60) or not self.store.take_limit("download-message:" + str(owner) + ":" + str(message_id), 1, 15):
            self.answer_download(query_id, "Wait a moment before requesting another copy.", alert=True)
            return
        self.answer_download(query_id, "Sending a downloadable file copy.")
        params = {"chat_id": account["chat_id"], "document": file_id,
                  "caption": "Requested download. Open the attachment and use Telegram’s Save/Download option.",
                  "disable_content_type_detection": True, "disable_notification": True,
                  "reply_parameters": {"message_id": message_id, "allow_sending_without_reply": False}}
        if topic_id is not None:
            params["message_thread_id"] = topic_id
        try:
            self.api.json("sendDocument", params)
        except BotUnavailable:
            # handle() durably committed the update before entering here. A
            # network outcome must not trigger a second automatic file copy.
            print("Telegram download copy could not be confirmed; no automatic resend.", flush=True)

    def run(self):
        checked_bot = None
        while not self.stop.is_set():
            try:
                bot = self.store.bot()
                if not bot:
                    self.stop.wait(3)
                    continue
                if checked_bot != bot["id"]:
                    info = self.api.json("getWebhookInfo", {})
                    if not isinstance(info, dict) or info.get("url"):
                        self.store.set_setting("bot_status", "An existing webhook prevents invitation polling. Remove it deliberately before using this receiver.")
                        self.stop.wait(15)
                        continue
                    checked_bot = bot["id"]
                updates = self.api.json("getUpdates", {"offset": self.store.setting("bot_offset", -1) + 1, "timeout": 20, "allowed_updates": ["message", "callback_query"], "limit": 25})
                if not isinstance(updates, list):
                    raise BotUnavailable
                for update in updates:
                    if isinstance(update, dict):
                        self.handle(update)
                self.store.set_setting("bot_status", "Invitation receiver connected")
            except Exception:
                self.store.set_setting("bot_status", "Telegram receiver unavailable. Check the bot token, webhook configuration, and connectivity.")
                self.stop.wait(10)


def validate_destination(method, params, account, bot):
    if set(params) - METHODS[method]:
        raise DashboardError("Request fields are not permitted.")
    if method != "getMe" and str(params.get("chat_id")) != str(account["chat_id"]):
        raise DashboardError("Destination does not belong to this account.")
    if method == "getChatMember" and str(params.get("user_id")) != str(bot["id"]):
        raise DashboardError("Only the shared bot's membership may be checked.")


def create_app(store=None, api=None):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=MAX_FILE + 131072, MAX_FORM_MEMORY_SIZE=MULTIPART_MEMORY, MAX_FORM_PARTS=12)
    store = store or from_environment()
    api = api or TelegramAPI(store)
    app.extensions.update(store=store, telegram=api, pairing=PairingReceiver(store, api))

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/telegram/<method>")
    def proxy(method):
        authorization = request.headers.get("Authorization", "")
        account = store.authenticate_worker(authorization.removeprefix("Bearer ")) if authorization.startswith("Bearer ") else None
        if not account:
            return jsonify(ok=False, error_code=403, description="Gateway account is not authorized"), 403
        if not store.take_limit("gateway:" + account["id"], 180, 60):
            return jsonify(ok=False, error_code=429, description="Gateway account rate limit reached"), 429
        if method not in METHODS:
            return jsonify(ok=False, error_code=403, description="Gateway method is not permitted"), 403
        try:
            bot = store.bot()
            if not bot:
                raise BotUnavailable
            if method == "sendDocument":
                if set(request.files) != {"document"} or len(request.files.getlist("document")) != 1 or any(len(request.form.getlist(key)) != 1 for key in request.form):
                    raise DashboardError("Upload fields are not permitted.")
                params = request.form.to_dict()
                if any(len(value.encode("utf-8")) > MAX_FORM_FIELD for value in params.values()):
                    raise RequestEntityTooLarge()
                validate_destination(method, params, account, bot)
                upload = request.files["document"]
                upload.stream.seek(0, 2)
                size = upload.stream.tell()
                upload.stream.seek(0)
                if size > MAX_FILE:
                    return jsonify(ok=False, error_code=413, description="Request Entity Too Large"), 413
                # The gateway, not a worker, owns the keyboard and callback.
                # Future uploads remain one document notification; only an
                # explicit, owner-checked button press requests another copy.
                params["reply_markup"] = json.dumps(download_markup(upload.filename or "attachment.bin"))
                params["disable_content_type_detection"] = "true"
                boundary = "gateway-" + secrets.token_hex(16)
                prefix = b""
                for key, value in params.items():
                    prefix += ("--" + boundary + '\r\nContent-Disposition: form-data; name="' + key + '"\r\n\r\n' + value + "\r\n").encode()
                filename = re.sub(r"[^A-Za-z0-9._-]", "_", upload.filename or "attachment.bin")[:150]
                prefix += ("--" + boundary + '\r\nContent-Disposition: form-data; name="document"; filename="' + filename + '"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
                suffix = ("\r\n--" + boundary + "--\r\n").encode()
                def content():
                    yield prefix
                    while block := upload.stream.read(65536):
                        yield block
                    yield suffix
                status, payload = api.post(method, content(), chat=account["chat_id"], content_type="multipart/form-data; boundary=" + boundary, length=len(prefix) + size + len(suffix))
            else:
                if request.content_length is None or request.content_length > 65536:
                    raise DashboardError("Request is too large.")
                params = request.get_json(silent=True)
                if not isinstance(params, dict):
                    raise DashboardError("Request must be JSON.")
                validate_destination(method, params, account, bot)
                status, payload = api.post(method, json.dumps(params).encode(), chat=account["chat_id"])
            # The worker performs the existing safe diagnostic classification.
            return jsonify(payload), status if 100 <= status <= 599 else 502
        except DashboardError:
            return jsonify(ok=False, error_code=403, description="Gateway request is not permitted"), 403
        except HTTPException:
            # Parsing/size rejections happen before delivery. Let Flask retain
            # the real 4xx status rather than labelling them ambiguous sends.
            raise
        except Exception:
            # A network outcome is unconfirmed, never a definitive Telegram 4xx.
            return jsonify(ok=False, error_code=502, description="Gateway delivery outcome is unconfirmed"), 502

    @app.errorhandler(413)
    def too_large(error):
        return jsonify(ok=False, error_code=413, description="Request Entity Too Large"), 413

    return app
