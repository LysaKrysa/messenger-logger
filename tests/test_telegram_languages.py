"""Localized bot scaffolding preserves captured text, entities and media bytes."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from email.parser import BytesParser
from email.policy import default
from unittest.mock import patch
from zoneinfo import ZoneInfo

from event_store import EventStore
from telegram_format import attachment_card, event_card, file_size, message_card, stamp, units
from telegram_i18n import TEXT, attachments, tr
from telegram_relay import TelegramRelay, TelegramConfigurationError, TelegramRequestError


ORIGINAL = "Deleted message Sent Saved <b>literal😀</b> — Zażółć gęślą jaźń"
ENV = {"TELEGRAM_ENABLED": "true", "TELEGRAM_MODE": "deleted", "TELEGRAM_SEND_MEDIA": "true",
       "TELEGRAM_TOPICS": "false", "TELEGRAM_CHAT_ID": "123456",
       "TELEGRAM_BOT_TOKEN": "123456:" + "SYNTHETIC_NOT_A_REAL_BOT_TOKEN"}


def sample():
    return {"sender": "Saved", "conversation": "Unnamed conversation", "deleted": True,
            "created_ms": 1001000, "deleted_at_ms": 1002000, "original_text": ORIGINAL,
            "text": ORIGINAL, "last_edit_ms": None, "attachments": [], "metadata": {}, "reply": {}}


def message(attachments=None):
    return {"type": "message", "timestamp": 1001000, "data": {"id": "m1", "threadId": 42,
            "senderId": 222, "timestampMs": 1001000, "text": ORIGINAL, "attachments": attachments or []}}


class FormattingTests(unittest.TestCase):
    def test_catalogs_have_the_same_nonempty_entries(self):
        self.assertEqual(TEXT["pl"].keys(), TEXT["en"].keys())
        for language in ("pl", "en"):
            self.assertTrue(all(isinstance(value, str) and value for value in TEXT[language].values()))

    def test_polish_default_and_english_keep_captured_text_and_utf16_entities(self):
        for language, heading, sent in (("pl", "Usunięta wiadomość", "Wysłano"), ("en", "Deleted message", "Sent")):
            with self.subTest(language=language):
                card = message_card(sample(), ZoneInfo("UTC"), language)
                self.assertIn(heading, card.text)
                self.assertIn(sent + "  ", card.text)
                self.assertIn("Saved\n💬 Unnamed conversation", card.text)
                quotes = []
                encoded = card.text.encode("utf-16-le")
                for entity in card.entities:
                    self.assertLessEqual(entity["offset"] + entity["length"], units(card.text))
                    value = encoded[entity["offset"] * 2:(entity["offset"] + entity["length"]) * 2].decode("utf-16-le")
                    if entity["type"] == "blockquote":
                        quotes.append(value)
                self.assertEqual(quotes, [ORIGINAL])
        self.assertIn("Usunięta wiadomość", message_card(sample(), ZoneInfo("UTC")).text)

    def test_only_typed_fallbacks_change_and_file_names_are_preserved(self):
        item = sample()
        item.update(sender_kind="unknown", conversation_kind="unnamed")
        item["reply"] = {"sender_name": "You", "sender_name_kind": "self", "text": ORIGINAL}
        item["attachments"] = [{"type": "voice", "fileName": "Saved głos 😀.ogg", "savedBytes": 1500000,
                                 "downloadStatus": "saved", "viewOnce": True, "viewMode": "once"}]
        card = message_card(item, ZoneInfo("UTC"))
        for text in ("Nieznany uczestnik", "Rozmowa bez nazwy", "Odpowiedź na: Ty", "1 załącznik",
                     "Wiadomość głosowa", "jednorazowe wyświetlenie", "Saved głos 😀.ogg", "1,4 MB", "Zapisano", ORIGINAL):
            self.assertIn(text, card.text)
        self.assertIn("Saved głos 😀.ogg", attachment_card(item, item["attachments"][0], 0, ZoneInfo("UTC")).text)

    def test_polish_plurals_and_dates_do_not_depend_on_process_locale(self):
        expected = {0: "0 załączników", 1: "1 załącznik", 2: "2 załączniki", 5: "5 załączników",
                    12: "12 załączników", 22: "22 załączniki", 112: "112 załączników"}
        for count, text in expected.items():
            self.assertEqual(attachments(count, "pl"), text)
        self.assertEqual(attachments(1, "en"), "1 attachment")
        self.assertEqual(attachments(2, "en"), "2 attachments")
        self.assertEqual(file_size(1), "1 bajt")
        self.assertEqual(file_size(22), "22 bajty")
        self.assertEqual(file_size(12), "12 bajtów")
        self.assertIn("lis", stamp(1794000000000, ZoneInfo("UTC")))
        self.assertIn("Nov", stamp(1794000000000, ZoneInfo("UTC"), "en"))
        self.assertEqual(stamp(None, ZoneInfo("UTC")), "Nie zapisano")


class RelayLanguageTests(unittest.TestCase):
    def test_zip_and_caption_are_localized_without_changing_original_files_or_splitting(self):
        for language in ("pl", "en"):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, dict(ENV, TELEGRAM_LANGUAGE=language), clear=True):
                root = Path(temporary)
                store = EventStore(root, own_id="12345", started_at=1000, run_id="language-run", clock=lambda: 1003)
                self.addCleanup(store.close)
                store.accept(message([{"type": "voice", "fileName": "voice.ogg"}, {"type": "image", "fileName": "photo.png"}]))
                originals = {"voice.ogg": b"OggS\x00\xfforiginal voice", "photo.png": b"\x89PNG\r\n\x1a\noriginal photo"}
                for index, (filename, data) in enumerate(originals.items()):
                    target = root / "media" / filename
                    target.parent.mkdir(exist_ok=True)
                    target.write_bytes(data)
                    store.update_media("42", "m1", index, {"downloadStatus": "saved", "localPath": "media/" + filename,
                                       "savedBytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
                store.accept({"type": "messageUnsend", "timestamp": 1002000, "data": {"threadId": 42, "messageId": "m1"}})
                calls = []

                class Response:
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                    def read(self, limit): return b'{"ok":true,"result":{}}'

                def opened(request, **options):
                    calls.append((request, b"".join(request.data)))
                    return Response()

                relay = TelegramRelay(lambda value: None, store, timezone="UTC", start=False)
                self.addCleanup(relay.close)
                with patch("telegram_relay.urlopen", side_effect=opened):
                    relay._deleted("42", "m1")
                self.assertEqual(len(calls), 1)
                request, body = calls[0]
                self.assertTrue(request.full_url.endswith("/sendDocument"))
                multipart = BytesParser(policy=default).parsebytes(("MIME-Version: 1.0\r\nContent-Type: " + request.get_header("Content-type") + "\r\n\r\n").encode() + body)
                fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True) for part in multipart.iter_parts()}
                self.assertIn(tr(language, "zip_details"), fields["caption"].decode())
                with zipfile.ZipFile(io.BytesIO(fields["document"])) as archive:
                    details = archive.read("message.txt").decode()
                    self.assertIn(tr(language, "deleted_message"), details)
                    self.assertIn(ORIGINAL, details)
                    for index, (filename, data) in enumerate(originals.items(), 1):
                        self.assertEqual(archive.read(f"{index:02d}_" + filename), data)

    def test_edits_and_reactions_use_structured_copy_and_exclude_prior_run_quotes(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = EventStore(Path(temporary), own_id="12345", started_at=1000, run_id="language-run", clock=lambda: 1003)
            self.addCleanup(store.close)
            store.accept(message())
            edited = "<b>Edited Saved 😀</b>"
            summary = store.accept({"type": "messageEdit", "timestamp": 1001500, "data": {"threadId": 42, "messageId": "m1", "senderId": 222, "newText": edited}})
            card = event_card(summary["thread"], summary["body"], "edit", event=summary["telegram"])
            self.assertIn("Edytowano wiadomość m1:", card.text)
            self.assertIn(edited, card.text)
            summary = store.accept({"type": "reaction", "timestamp": 1001600, "data": {"threadId": 42, "messageId": "m1", "actorId": 333, "reaction": "😀"}})
            for language in ("pl", "en"):
                card = event_card(summary["thread"], summary["body"], "reaction", language, event=summary["telegram"])
                self.assertIn(tr(language, "reaction_reference", reaction="😀", message_id="m1"), card.text)
                self.assertIn(edited, card.text)
            with store.db:
                store.db.execute("UPDATE messages SET run_id='previous-run'")
            summary = store.accept({"type": "reaction", "timestamp": 1001700, "data": {"threadId": 42, "messageId": "m1", "actorId": 333, "reaction": "👍"}})
            self.assertNotIn("original", summary["telegram"])

    def test_lookup_failure_retains_this_relays_last_language(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, ENV, clear=True):
            store = EventStore(Path(temporary))
            self.addCleanup(store.close)
            relay = TelegramRelay(lambda value: None, store, start=False)
            self.addCleanup(relay.close)
            self.assertEqual(relay.language, "pl")
            relay.gateway = "http://127.0.0.1:8788"
            with patch.object(relay, "_api", return_value={"language": "en"}):
                self.assertEqual(relay._language(), "en")
            for response in ({"language": []}, {"language": "de"}, None):
                with patch.object(relay, "_api", return_value=response):
                    self.assertEqual(relay._language(), "en")
            with patch.object(relay, "_api", side_effect=TelegramRequestError("getLocale", "timeout")):
                self.assertEqual(relay._language(), "en")
            with patch.dict(os.environ, {"TELEGRAM_LANGUAGE": "de"}), self.assertRaises(TelegramConfigurationError):
                TelegramRelay(lambda value: None, store, start=False)
