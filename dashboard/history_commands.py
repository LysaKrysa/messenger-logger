"""Private owner menus for conversation policy and explicitly confirmed resends."""
import re

from telegram_i18n import tr
from .bot_commands import date
from .store import DashboardError

PREFIXES = ("ch.", "cf.", "cc.", "fc.", "cd.", "dh.", "dl.", "dc.")


def button(text, data):
    return {"text": text, "callback_data": data}


class HistoryCommands:
    def __init__(self, store, api, deliveries):
        self.store, self.api, self.deliveries = store, api, deliveries
        self.conversations = deliveries.conversations

    def menu(self, owner, command, language):
        row = self.store.user_account(owner)
        if row is None:
            return tr(language, "not_paired"), None
        return self.chats(row, language) if command == "chats" else self.history(row, language)

    def chats(self, row, lang, offset=0):
        values = self.conversations.rows(row, offset, 10)
        policy = self.store.filters(row["id"])
        text = tr(lang, "chats_title") + "\n\n" + tr(lang, "chats_note") + "\n\n" + tr(lang, "capture_default") + ": " + tr(lang, "default_all" if policy["capture_default"] else "default_include")
        keys = [[button(tr(lang, "default_all"), "cd.all"), button(tr(lang, "default_include"), "cd.include")]]
        for item in values:
            label = item["name"] or tr(lang, "chat_fallback", value=item["thread_id"])
            state = tr(lang, "capture_on" if item["capture_enabled"] else "capture_off")
            keys.append([button(label[:50] + " · " + state, "cf." + item["id"])])
        if not values:
            text += "\n\n" + tr(lang, "chats_empty")
        keys.append(self.pages("ch", offset, len(values), lang))
        return text, {"inline_keyboard": [key for key in keys if key]}

    @staticmethod
    def pages(prefix, offset, count, lang):
        keys = []
        if offset:
            keys.append(button(tr(lang, "page_previous"), prefix + "." + str(max(0, offset - 10))))
        keys.append(button(tr(lang, "refresh"), prefix + "." + str(offset)))
        if count == 10 and offset < 4990:
            keys.append(button(tr(lang, "page_next"), prefix + "." + str(offset + 10)))
        return keys

    def chat(self, row, ident, lang):
        item = self.conversations.get(row, ident)
        if not item:
            raise DashboardError("Conversation unavailable.")
        policy = self.store.filters(row["id"])
        rule = policy["rules"].get(item["thread_id"], {"capture": None, "forward": "inherit"})
        name = item["name"] or tr(lang, "chat_fallback", value=item["thread_id"])
        text = name + "\n\n" + tr(lang, "chat_controls") + "\n\n" + tr(lang, "capture_setting") + ": " + tr(lang, {None: "inherit", True: "include", False: "exclude"}[rule["capture"]]) + "\n" + tr(lang, "forward_setting") + ": " + tr(lang, "forward_" + rule["forward"])
        keys = []
        for prefix, choices, current in (("cc", [("inherit", "inherit"), ("on", "include"), ("off", "exclude")], {None: "inherit", True: "on", False: "off"}[rule["capture"]]),
                                          ("fc", [(key, "forward_" + key) for key in ("inherit", "all", "deleted", "off")], rule["forward"])):
            for value, label in choices:
                keys.append([button(("✓ " if value == current else "") + tr(lang, label), prefix + "." + ident + "." + value)])
        keys.append([button(tr(lang, "back_chats"), "ch.0")])
        return text, {"inline_keyboard": keys}

    def history(self, row, lang, offset=0):
        values = self.deliveries.rows(row, offset, 10)
        text = tr(lang, "deliveries_title") + "\n\n" + tr(lang, "deliveries_note")
        keys = []
        for index, item in enumerate(values, 1):
            name = item["name"] or tr(lang, "chat_fallback", value=item["thread_id"]) if item["thread_id"] else tr(lang, "unknown_chat")
            text += "\n\n" + str(offset + index) + ". " + tr(lang, "delivery_" + item["outcome"]) + " · " + name[:60] + "\n" + date(item["created"], lang) + " · " + tr(lang, "kind_" + item["kind"])
            keys.append([button(str(offset + index) + ". " + tr(lang, "delivery_details"), "dl." + item["id"])])
        if not values:
            text += "\n\n" + tr(lang, "deliveries_empty")
        keys.append(self.pages("dh", offset, len(values), lang))
        return text, {"inline_keyboard": [key for key in keys if key]}

    def detail(self, row, ident, lang):
        value = self.deliveries.get(row, ident)
        if not value:
            raise DashboardError("Delivery unavailable.")
        text = tr(lang, "delivery_" + value["outcome"]) + "\n" + date(value["created"], lang) + "\n\n" + tr(lang, "resend_warning")
        keys = []
        if value["available"]:
            token = self.deliveries.intent(row, ident)
            text += "\n\n" + tr(lang, "resend_expiry", value=date(value["expires"], lang))
            keys.append([button(tr(lang, "resend_confirm"), "dc." + token)])
        else:
            text += "\n\n" + tr(lang, "resend_unavailable")
        keys.append([button(tr(lang, "back_deliveries"), "dh.0")])
        return text, {"inline_keyboard": keys}

    def answer(self, query_id, text, alert=False):
        try:
            self.api.json("answerCallbackQuery", {"callback_query_id": query_id, "text": text, "show_alert": alert, "cache_time": 0})
        except (RuntimeError, OSError):
            pass

    def callback(self, query):
        query_id = query.get("id")
        if not isinstance(query_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", query_id):
            return
        sender, message = query.get("from"), query.get("message")
        sender = sender if isinstance(sender, dict) else {}
        message = message if isinstance(message, dict) else {}
        owner = sender.get("id")
        lang = self.store.telegram_language(owner) if type(owner) is int else "pl"
        chat, author, bot = message.get("chat", {}), message.get("from", {}), self.store.bot()
        mid, topic = message.get("message_id"), message.get("message_thread_id")
        valid = (type(owner) is int and owner > 0 and sender.get("is_bot") is False
                 and isinstance(chat, dict) and chat.get("type") == "private" and type(chat.get("id")) is int and chat["id"] == owner
                 and isinstance(author, dict) and bot and author.get("id") == bot["id"] and author.get("is_bot") is True
                 and type(mid) is int and 0 < mid < 2**53 and (topic is None or type(topic) is int and 0 < topic < 2**53))
        row = self.store.user_account(owner) if valid else None
        if row is None:
            self.answer(query_id, tr(lang, "menu_denied"), True)
            return
        if not self.store.take_limit("history-menu:" + str(owner), 30, 60):
            self.answer(query_id, tr(lang, "download_wait"), True)
            return
        data = query.get("data", "")
        try:
            if not isinstance(data, str) or len(data) > 64:
                raise DashboardError("Invalid action.")
            if re.fullmatch(r"(?:ch|dh)\.[0-9]{1,4}", data):
                action, raw = data.split(".")
                offset = int(raw)
                if offset > 4990:
                    raise DashboardError("Invalid page.")
                text, markup = self.chats(row, lang, offset) if action == "ch" else self.history(row, lang, offset)
            elif data in {"cd.all", "cd.include"}:
                self.conversations.update(row, default=data == "cd.all")
                text, markup = self.chats(row, lang)
            elif re.fullmatch(r"(?:cc|fc)\.[a-f0-9]{32}\.(?:inherit|on|off|all|deleted)", data):
                action, ident, value = data.split(".")
                item = self.conversations.get(row, ident)
                if not item or action == "cc" and value not in {"inherit", "on", "off"} or action == "fc" and value not in {"inherit", "all", "deleted", "off"}:
                    raise DashboardError("Conversation unavailable.")
                rule = self.store.filters(row["id"])["rules"].get(item["thread_id"], {"capture": None, "forward": "inherit"})
                self.conversations.update(row, ident, include={"inherit": None, "on": True, "off": False}[value] if action == "cc" else rule["capture"],
                                          mode=value if action == "fc" else rule["forward"])
                text, markup = self.chat(row, ident, lang)
            elif re.fullmatch(r"(?:cf|dl|dc)\.[a-f0-9]{32}", data):
                action, ident = data.split(".")
                if action == "cf":
                    text, markup = self.chat(row, ident, lang)
                elif action == "dl":
                    text, markup = self.detail(row, ident, lang)
                else:
                    self.answer(query_id, tr(lang, "resend_sending"))
                    value = self.deliveries.resend(row, ident, acknowledged=True)
                    text = tr(lang, "resend_result", value=tr(lang, "delivery_" + value["outcome"])) if value else tr(lang, "resend_unavailable")
                    markup = {"inline_keyboard": [[button(tr(lang, "back_deliveries"), "dh.0")]]}
            else:
                raise DashboardError("Invalid action.")
        except DashboardError:
            self.answer(query_id, tr(lang, "menu_unavailable"), True)
            return
        self.answer(query_id, tr(lang, "menu_updated"))
        try:
            self.api.json("editMessageText", {"chat_id": owner, "message_id": mid, "text": text, "reply_markup": markup})
        except (RuntimeError, OSError):
            pass  # A lost UI response must never repeat the confirmed action.
