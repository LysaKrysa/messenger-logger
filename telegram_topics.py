"""Persistent Telegram topic routing and local repair of topic mappings."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from telegram_i18n import tr
from identities import display_label

COLORS = (7322096, 16766590, 13338331, 9367192, 16749490, 16478047)


class TopicError(RuntimeError):
    """Static user-readable setup/state messages; never provider response text."""


class TelegramRejected(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__("Telegram rejected a request (code=" + str(code) + ").")


def topic_title(name, language="en"):
    name = display_label(name) or tr(language, "conversation")
    # Satisfy both the Bot API character cap and underlying UTF-8 limits.
    return name.encode("utf-8")[:128].decode("utf-8", errors="ignore").strip() or tr(language, "conversation")


class TopicRouter:
    def __init__(self, api, store, bot_id, chat_id, *, language="en"):
        self.language = language
        self.api, self.store = api, store
        self.bot_id, self.requested_chat = str(bot_id), str(chat_id)
        self.chat_id = None
        self.retry_at = 0.0

    def destination(self):
        if self.chat_id is not None:
            return self.chat_id
        if time.monotonic() < self.retry_at:
            raise TopicError("Topic setup is not ready; check Threaded mode or group topic permissions.")
        try:
            bot = self.api("getMe", {})
            if not isinstance(bot, dict) or str(bot.get("id")) != self.bot_id:
                raise TopicError("Could not verify the Telegram bot for topic routing.")
            chat = self.api("getChat", {"chat_id": self.requested_chat})
            if not isinstance(chat, dict) or not isinstance(chat.get("id"), int) or isinstance(chat["id"], bool):
                raise TopicError("Could not verify the Telegram destination for topics.")
            if chat.get("type") == "private":
                if bot.get("has_topics_enabled") is not True:
                    raise TopicError("Telegram reports Threaded mode disabled for the configured bot. Check that same bot in BotFather; retry a fresh event after 30 seconds or restart the logger.")
            elif chat.get("type") == "supergroup" and chat.get("is_forum") is True:
                member = self.api("getChatMember", {"chat_id": chat["id"], "user_id": int(self.bot_id)})
                if not isinstance(member, dict) or not (member.get("status") == "creator" or (member.get("status") == "administrator" and member.get("can_manage_topics") is True)):
                    raise TopicError("The bot needs administrator access with Manage Topics in this group.")
            else:
                raise TopicError("Use a threaded private bot chat or a group with Topics enabled.")
            self.chat_id = str(chat["id"])
            return self.chat_id
        except Exception as exc:
            self.retry_at = time.monotonic() + 30
            if isinstance(exc, TopicError):
                raise
            raise TopicError("Could not verify topic setup. Check the bot token, destination chat ID, and Telegram connectivity.") from None

    def unique_title(self, name):
        existing = self.store.telegram_topic_names(self.bot_id, self.chat_id)
        base = topic_title(name, self.language)
        title, index = base, 2
        while title in existing:
            suffix = " (" + str(index) + ")"
            title = base.encode("utf-8")[:128-len(suffix)].decode("utf-8", errors="ignore").rstrip() + suffix
            index += 1
        return title

    def topic(self, thread_id, name):
        self.destination()
        mapping = self.store.telegram_topic(self.bot_id, self.chat_id, thread_id)
        fallback = not display_label(name) or name in {"Unnamed conversation", "Unknown participant"}
        if mapping:
            if mapping["status"] == "missing":
                raise TopicError("Telegram rejected the replacement topic too. Automatic repair is paused; use manage-topics to bind a verified existing topic.")
            if mapping["status"] not in {"ready", "repair_pending"} or not mapping["topic_id"]:
                raise TopicError("A topic creation was unconfirmed. Use manage-topics to bind its existing Telegram topic before retrying.")
            # Improve an initially unknown conversation label once a name arrives.
            if mapping["is_fallback"] and not fallback and mapping["status"] == "ready":
                title = self.unique_title(name)
                try:
                    self.api("editForumTopic", {"chat_id": self.chat_id, "message_thread_id": mapping["topic_id"], "name": title})
                except Exception:
                    pass  # The topic ID remains valid even if its title cannot change.
                else:
                    self.store.save_telegram_topic(self.bot_id, self.chat_id, thread_id, mapping["topic_id"], title)
            return mapping["topic_id"]
        title = self.unique_title(tr(self.language, "unmatched") if thread_id == "unresolved" else name)
        if not self.store.reserve_telegram_topic(self.bot_id, self.chat_id, thread_id, title, fallback):
            raise TopicError("Another topic creation is in progress; local content is saved.")
        return self._create_reserved(thread_id, title, fallback)

    def replace_missing(self, thread_id, topic_id, name):
        """Replace only the mapping used by a definitively rejected request."""
        mapping = self.store.telegram_topic(self.bot_id, self.chat_id, thread_id)
        if mapping and mapping["topic_id"] == topic_id and mapping["status"] == "repair_pending":
            self.store.finish_telegram_topic_repair(self.bot_id, self.chat_id, thread_id, topic_id, missing=True)
            raise TopicError("Telegram rejected the replacement topic too. Automatic repair is paused; use manage-topics to bind a verified existing topic.")
        if mapping and self.store.begin_telegram_topic_repair(self.bot_id, self.chat_id, thread_id, topic_id):
            title = mapping["name"] or self.unique_title(name)
            return self._create_reserved(thread_id, title, mapping["is_fallback"], repair=True)
        # A concurrent local bind/repair must not be overwritten.
        return self.topic(thread_id, name)

    def confirm(self, thread_id, topic_id, *, missing=False):
        self.store.finish_telegram_topic_repair(self.bot_id, self.chat_id, thread_id, topic_id, missing=missing)

    def _create_reserved(self, thread_id, title, fallback, *, repair=False):
        try:
            color = COLORS[hashlib.sha256(thread_id.encode()).digest()[0] % len(COLORS)]
            result = self.api("createForumTopic", {"chat_id": self.chat_id, "name": title, "icon_color": color})
        except InterruptedError:
            self.store.cancel_telegram_topic(self.bot_id, self.chat_id, thread_id)
            raise
        except TelegramRejected:
            self.store.cancel_telegram_topic(self.bot_id, self.chat_id, thread_id)
            raise TopicError("Telegram rejected topic creation. Check topic settings, permissions, and API limits.") from None
        except Exception:
            self.store.unconfirmed_telegram_topic(self.bot_id, self.chat_id, thread_id)
            raise TopicError("Topic creation was unconfirmed. Use manage-topics to bind it; it will not be recreated automatically.") from None
        topic_id = result.get("message_thread_id") if isinstance(result, dict) else None
        if not isinstance(topic_id, int) or isinstance(topic_id, bool) or topic_id <= 0:
            self.store.unconfirmed_telegram_topic(self.bot_id, self.chat_id, thread_id)
            raise TopicError("Telegram did not return a usable topic ID. Use manage-topics to bind it.")
        self.store.save_telegram_topic(self.bot_id, self.chat_id, thread_id, topic_id, title, fallback,
                                       status="repair_pending" if repair else "ready")
        return topic_id


def main():
    parser = argparse.ArgumentParser(description="Inspect or repair local Telegram topic mappings. No Telegram API requests are made.")
    parser.add_argument("action", choices=("list", "bind"))
    parser.add_argument("--bot-id", required=True, help="Numeric bot ID (the part of its token before the colon), without the token")
    parser.add_argument("--chat-id", required=True)
    parser.add_argument("--conversation", help="Messenger conversation ID; available with show-messages --include-ids")
    parser.add_argument("--topic-id", type=int)
    parser.add_argument("--name", default="Conversation")
    args = parser.parse_args()
    if not args.bot_id.isdecimal() or not args.chat_id.lstrip("-").isdecimal():
        parser.error("Use numeric bot and destination chat IDs; never pass a bot token.")
    if args.action == "bind" and (not args.conversation or not args.topic_id or args.topic_id <= 0):
        parser.error("bind requires --conversation and a positive --topic-id")
    path = Path(os.environ.get("DATA_DIR", "/data")) / "messenger-modern.sqlite3"
    if not path.is_file():
        parser.error("The local logger database does not exist.")
    with sqlite3.connect(path.resolve().as_uri() + ("?mode=ro" if args.action == "list" else "?mode=rw"), uri=True) as db:
        if args.action == "bind":
            db.execute("INSERT INTO telegram_topics VALUES (?,?,?,?,?,'ready',0) ON CONFLICT(bot_id,chat_id,thread_id) DO UPDATE SET topic_id=excluded.topic_id,name=excluded.name,status='ready',is_fallback=0",
                       (args.bot_id, args.chat_id, args.conversation, args.topic_id, topic_title(args.name)))
        rows = db.execute("SELECT thread_id,topic_id,name,status FROM telegram_topics WHERE bot_id=? AND chat_id=? ORDER BY name", (args.bot_id, args.chat_id)).fetchall()
        print(json.dumps([dict(zip(("conversation_id", "topic_id", "name", "status"), row)) for row in rows], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (sqlite3.Error, OSError, ValueError):
        print("Could not access the local topic map; private values are hidden.")
        raise SystemExit(1)
