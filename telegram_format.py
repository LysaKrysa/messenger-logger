"""Telegram layouts using explicit entities, so chat text cannot inject markup."""
from __future__ import annotations

from datetime import datetime
from telegram_i18n import tr, language as normalize_language, attachments as attachment_count


def units(text):
    return sum(2 if ord(char) > 0xFFFF else 1 for char in text)


def chunks(text, limit=3500):
    part, length = [], 0
    for char in text:
        width = 2 if ord(char) > 0xFFFF else 1
        if length + width > limit:
            yield "".join(part)
            part, length = [], 0
        part.append(char)
        length += width
    if part:
        yield "".join(part)


class RichText:
    def __init__(self):
        self.text = ""
        self.entities = []
        self.length = 0

    def add(self, text, style=None):
        text = str(text)
        count = units(text)
        if style and count:
            self.entities.append({"type": style, "offset": self.length, "length": count})
        self.text += text
        self.length += count
        return self

    def quote(self, text):
        return self.add(text, "expandable_blockquote" if len(text) > 500 or text.count("\n") > 5 else "blockquote")

    def pages(self, limit=3500):
        offset = 0
        for text in chunks(self.text, limit):
            length = units(text)
            entities = []
            for entity in self.entities:
                start = max(offset, entity["offset"])
                end = min(offset + length, entity["offset"] + entity["length"])
                if start < end:
                    entities.append({"type": entity["type"], "offset": start - offset, "length": end - start})
            yield text, entities
            offset += length


def stamp(value, zone, language="pl"):
    if not value:
        return tr(language, "not_captured")
    date = datetime.fromtimestamp(value / 1000, zone)
    months = ("sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru") if normalize_language(language) == "pl" else ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{date.day:02d} {months[date.month - 1]} · " + date.strftime("%H:%M:%S %Z")


def file_size(value, language="pl"):
    value = value if isinstance(value, (int, float)) and value >= 0 else 0
    if value >= 1024 * 1024:
        amount = f"{value / (1024 * 1024):.1f}"
        return (amount.replace(".", ",") if normalize_language(language) == "pl" else amount) + " MB"
    if value >= 1024:
        return f"{value / 1024:.0f} KB"
    count = round(value)
    if normalize_language(language) == "en":
        return f"{count} bytes"
    word = "bajt" if count == 1 else "bajty" if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14) else "bajtów"
    return f"{count} {word}"


def media_label(media, language="pl"):
    kind = media.get("type")
    return tr(language, kind if kind in {"image", "video", "gif", "sticker", "voice", "audio", "document", "file"} else "attachment")


def display(item, key, language, fallback="unknown"):
    kind = item.get(key + "_kind")
    if kind in {"unknown", "self", "unnamed"}:
        return tr(language, kind)
    return item.get(key) or tr(language, fallback)


def message_card(item, zone, language="pl"):
    result = RichText()
    once = any(media.get("viewOnce") for media in item["attachments"])
    title = tr(language, "deleted_message" if item["deleted"] else "once_message" if once else "captured_message")
    result.add(title, "bold").add("\n\n")
    result.add("👤 ").add(display(item, "sender", language), "bold").add("\n💬 ").add(display(item, "conversation", language, "unnamed"), "bold").add("\n")
    result.add(tr(language, "sent") + "  " + stamp(item["created_ms"], zone, language), "italic")
    if item["deleted"]:
        result.add("\n").add(tr(language, "deleted") + "  " + stamp(item["deleted_at_ms"], zone, language), "italic")
    result.add("\n\n")
    original = item["original_text"]
    if original is None:
        result.add(tr(language, "missing_content"), "italic")
    elif original:
        result.quote(original)
    elif not item["attachments"]:
        result.add(tr(language, "empty_capture"), "italic")
    if item.get("last_edit_ms") and item["text"] != original:
        result.add("\n\n").add(tr(language, "edited_version"), "bold").add("\n").quote(item["text"] or tr(language, "empty_text"))
    reply = item.get("reply") or {}
    if reply:
        result.add("\n\n").add(tr(language, "reply_to") + display(reply, "sender_name", language), "bold")
        if reply.get("text"):
            result.add("\n").quote(reply["text"])
    mentions = item.get("metadata", {}).get("mentions", [])
    if mentions:
        result.add("\n\n" + tr(language, "mentions") + "  " + ", ".join(display(person, "name", language) for person in mentions))
    attachments = item["attachments"]
    if attachments:
        result.add("\n\n").add("📎 " + attachment_count(len(attachments), language), "bold")
        for media in attachments:
            status = media.get("downloadStatus")
            status = tr(language, status if status in {"saved", "pending", "failed", "interrupted", "disabled"} else "metadata_only")
            parts = [media_label(media, language)]
            if media.get("viewOnce"):
                mode = media.get("viewMode")
                parts.append(tr(language, mode if mode in {"once", "replay"} else "disappearing"))
            if media.get("fileName"):
                parts.append(str(media["fileName"]))
            if media.get("savedBytes"):
                parts.append(file_size(media["savedBytes"], language))
            parts.append(status)
            result.add("\n• " + " · ".join(parts))
    return result


def attachment_card(item, media, index, zone, *, status=None, language="pl"):
    result = RichText()
    heading = tr(language, "deleted_attachment" if item["deleted"] else "once_attachment" if media.get("viewOnce") else "attachment_title")
    result.add(heading, "bold").add("\n")
    result.add(display(item, "sender", language)[:150], "bold").add("\n").add(display(item, "conversation", language, "unnamed")[:150]).add("\n")
    label = str(media.get("fileName") or media_label(media, language))
    result.add(str(index + 1) + "/" + str(len(item["attachments"])) + " · " + label[:150])
    if media.get("savedBytes"):
        result.add(" · " + file_size(media["savedBytes"], language))
    result.add("\n").add(stamp(item["created_ms"], zone, language), "italic")
    if status:
        result.add("\n\n").add(status, "italic")
    return result


def download_caption(item, detail, language="pl"):
    title = tr(language, "deleted_message" if item["deleted"] else "captured_message")
    return (RichText().add(title, "bold").add("\n\n👤 ").add(display(item, "sender", language)[:100], "bold")
            .add("\n💬 ").add(display(item, "conversation", language, "unnamed")[:100], "bold").add("\n\n" + detail))


def event_card(conversation, body, kind, language="pl", *, event=None):
    title = tr(language, {"edit": "edited_event", "reaction": "reaction_event"}.get(kind, "chat_event"))
    result = RichText().add(title, "bold").add("\n\n").add(
        tr(language, event["conversation_kind"]) if event and event.get("conversation_kind") in {"unknown", "unnamed", "self"} else conversation, "bold").add("\n\n")
    if not isinstance(event, dict):
        return result.quote(body)
    if kind == "edit":
        return result.add(tr(language, "edit_reference", message_id=event["message_id"])).add("\n").quote(event.get("text") or tr(language, "empty_text"))
    if kind == "reaction":
        result.add(display(event, "sender", language), "bold").add("\n")
        result.add(tr(language, "reaction_reference" if event.get("reaction") else "reaction_removed",
                      reaction=event.get("reaction", ""), message_id=event["message_id"]))
        if event.get("original") is not None:
            result.add("\n\n").add(display(event, "original_sender", language), "bold").add("\n").quote(event["original"] or tr(language, "attachment"))
        return result
    return result.quote(body)
