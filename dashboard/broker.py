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
from .bot_commands import ALIASES, CONTROLS, handle_control
from .connection_alerts import ConnectionAlerts
from telegram_i18n import tr

METHODS = {
    "getMe": set(), "getLocale": set(), "getControls": set(), "reportStatus": {"revision", "connected", "last_event"}, "getChat": {"chat_id"},
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


def download_markup(filename, language="pl"):
    label = tr(language, "download_zip" if filename.lower().endswith(".zip") else "download_file")
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
    def __init__(self, store, api, *, origin=None):
        self.store, self.api = store, api
        self.stop = threading.Event()
        self.connection_alerts = ConnectionAlerts(store, api, origin=origin)

    def handle(self, update):
        ident = update.get("update_id")
        if type(ident) is not int or ident <= self.store.setting("bot_offset", -1):
            return
        # Commit receipt before any reply: an ambiguous reply must not be replayed.
        self.store.set_setting("bot_offset", ident)
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            if isinstance(callback.get("data"), str) and callback["data"].startswith(("rca.", "rcr.")):
                self.connection_alerts.confirm(callback)
            else:
                self.download(callback)
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat, sender = message.get("chat", {}), message.get("from", {})
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            return
        chat_id = chat.get("id")
        if chat.get("type") != "private" or type(chat_id) is not int or chat_id <= 0 or type(sender.get("id")) is not int or sender.get("id") != chat_id or sender.get("is_bot"):
            return
        if not self.store.take_limit("bot-command:" + str(chat_id), 20, 60):
            return
        text = message.get("text", "")
        if not isinstance(text, str) or len(text) > 256:
            return
        parts = text.strip().split(maxsplit=1)
        command, parameter = (parts + [""])[:2] if parts else ("", "")
        command, addressed, username = command.partition("@")
        bot = self.store.bot()
        if addressed and (not bot or username.casefold() != bot["username"].casefold()):
            return
        language = self.store.telegram_language(chat_id)
        canonical = ALIASES.get(command.removeprefix("/").casefold(), command.removeprefix("/").casefold())
        if not command.startswith("/"):
            canonical = ""
        if canonical == "reconnect":
            reply = self.connection_alerts.request(chat_id, language) if not parameter else tr(language, "command_usage")
        elif canonical in CONTROLS:
            reply = handle_control(self.store, chat_id, canonical, parameter, language)
        elif canonical in {"en", "pl"}:
            if parameter.strip():
                return
            language = canonical
            self.store.set_telegram_language(chat_id, language)
            reply = tr(language, "language_saved")
        elif canonical == "start" and parameter:
            username = sender.get("username")
            label = "@" + username if isinstance(username, str) and re.fullmatch(r"[A-Za-z0-9_]{1,64}", username) else "Telegram user " + str(chat_id)
            accepted = self.store.claim_invite(parameter.strip(), chat_id, label)
            reply = tr(language, "pair_requested" if accepted else "pair_unavailable")
        elif parameter:
            reply = tr(language, "command_usage")
        elif canonical in {"disconnect", "stop"}:
            self.store.disconnect_user(chat_id)
            reply = tr(language, "disconnected")
        elif canonical == "privacy":
            reply = tr(language, "privacy")
        elif canonical in {"start", "help"}:
            reply = tr(language, "help")
        else:
            reply = ""
        welcome_key = "bot_welcomed:" + str(bot["id"] if bot else 0) + ":" + str(chat_id)
        first = not self.store.setting(welcome_key, False)
        if first or canonical == "start":
            self.store.set_setting(welcome_key, True)
            reply = tr(language, "welcome") + ("\n\n" + reply if reply else "")
        if not reply:
            return
        try:
            params = {"chat_id": chat_id, "text": reply, "disable_notification": True}
            topic = message.get("message_thread_id")
            if type(topic) is int and 0 < topic < 2**53:
                params["message_thread_id"] = topic
            self.api.json("sendMessage", params)
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
        language = self.store.telegram_language(sender.get("id")) if isinstance(sender, dict) else "pl"
        if not isinstance(sender, dict) or not isinstance(message, dict):
            self.answer_download(query_id, tr(language, "download_unavailable"), alert=True)
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
            self.answer_download(query_id, tr(language, "download_denied"), alert=True)
            return
        if not self.store.take_limit("download-user:" + str(owner), 6, 60) or not self.store.take_limit("download-message:" + str(owner) + ":" + str(message_id), 1, 15):
            self.answer_download(query_id, tr(language, "download_wait"), alert=True)
            return
        self.answer_download(query_id, tr(language, "download_sending"))
        params = {"chat_id": account["chat_id"], "document": file_id,
                  "caption": tr(language, "download_caption"),
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
        threading.Thread(target=self.connection_alerts.run, args=(self.stop,), name="connection-alerts", daemon=True).start()
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
    if method not in {"getMe", "getLocale"} and str(params.get("chat_id")) != str(account["chat_id"]):
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
        bucket = "worker-control:" if method in {"getControls", "reportStatus"} else "gateway:"
        if not store.take_limit(bucket + account["id"], 180, 60):
            return jsonify(ok=False, error_code=429, description="Gateway account rate limit reached"), 429
        if method not in METHODS:
            return jsonify(ok=False, error_code=403, description="Gateway method is not permitted"), 403
        attempted = False
        try:
            if method in {"getLocale", "getControls", "reportStatus"}:
                if request.content_length is None or request.content_length > 1024:
                    raise DashboardError("Locale requests must be empty JSON objects.")
                params = request.get_json(silent=True)
                if method == "reportStatus":
                    store.report_worker(account, params)
                    return jsonify(ok=True, result=True)
                if params != {}:
                    raise DashboardError("Locale requests must be empty JSON objects.")
                return jsonify(ok=True, result=store.controls(account) if method == "getControls" else {"language": store.telegram_language(account["chat_id"])})
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
                if store.controls(account)["quiet_until"] > store.clock():
                    return jsonify(ok=True, result={"_logger_suppressed": True})
                upload = request.files["document"]
                upload.stream.seek(0, 2)
                size = upload.stream.tell()
                upload.stream.seek(0)
                if size > MAX_FILE:
                    return jsonify(ok=False, error_code=413, description="Request Entity Too Large"), 413
                # The gateway, not a worker, owns the keyboard and callback.
                # Future uploads remain one document notification; only an
                # explicit, owner-checked button press requests another copy.
                params["reply_markup"] = json.dumps(download_markup(upload.filename or "attachment.bin", store.telegram_language(account["chat_id"])))
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
                attempted = True
                status, payload = api.post(method, content(), chat=account["chat_id"], content_type="multipart/form-data; boundary=" + boundary, length=len(prefix) + size + len(suffix))
            else:
                if request.content_length is None or request.content_length > 65536:
                    raise DashboardError("Request is too large.")
                params = request.get_json(silent=True)
                if not isinstance(params, dict):
                    raise DashboardError("Request must be JSON.")
                validate_destination(method, params, account, bot)
                if method == "sendMessage" and store.controls(account)["quiet_until"] > store.clock():
                    return jsonify(ok=True, result={"_logger_suppressed": True})
                attempted = method == "sendMessage"
                status, payload = api.post(method, json.dumps(params).encode(), chat=account["chat_id"])
            if attempted:
                outcome = "sent" if 200 <= status < 300 and payload.get("ok") is True else "rejected" if 400 <= status < 500 else "unconfirmed"
                store.record_delivery(account["id"], outcome)
            # The worker performs the existing safe diagnostic classification.
            return jsonify(payload), status if 100 <= status <= 599 else 502
        except DashboardError:
            return jsonify(ok=False, error_code=403, description="Gateway request is not permitted"), 403
        except HTTPException:
            # Parsing/size rejections happen before delivery. Let Flask retain
            # the real 4xx status rather than labelling them ambiguous sends.
            raise
        except Exception:
            if attempted:
                store.record_delivery(account["id"], "unconfirmed")
            # A network outcome is unconfirmed, never a definitive Telegram 4xx.
            return jsonify(ok=False, error_code=502, description="Gateway delivery outcome is unconfirmed"), 502

    @app.errorhandler(413)
    def too_large(error):
        return jsonify(ok=False, error_code=413, description="Request Entity Too Large"), 413

    return app
