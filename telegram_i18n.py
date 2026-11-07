"""Explicit Polish/English bot copy. Never translate captured content."""
DEFAULT_LANGUAGE = "pl"
LANGUAGES = frozenset({"pl", "en"})

TEXT = {
    "en": {
        "deleted_message": "🗑 Deleted message", "once_message": "👁 View-once message",
        "captured_message": "💬 Message captured", "sent": "Sent", "deleted": "Deleted",
        "not_captured": "Not captured",
        "missing_content": "Content was not captured before deletion; it cannot be recovered from the log.",
        "empty_capture": "No supported text or attachment was captured.",
        "edited_version": "✏️ Edited version", "empty_text": "[Empty text]",
        "reply_to": "↩ Reply to ", "unknown": "Unknown participant", "self": "You",
        "unnamed": "Unnamed conversation", "conversation": "Conversation", "unmatched": "Unmatched messages",
        "mentions": "Mentions", "attachment": "Attachment", "image": "Photo", "video": "Video",
        "gif": "GIF", "sticker": "Sticker", "voice": "Voice message", "audio": "Audio",
        "document": "File", "file": "File",
        "saved": "Saved", "pending": "Downloading…", "failed": "Unavailable",
        "interrupted": "Download interrupted", "metadata_only": "Metadata only",
        "once": "view once", "replay": "limited replay", "disappearing": "disappearing",
        "deleted_attachment": "📎 Deleted attachment", "once_attachment": "👁 View-once attachment",
        "attachment_title": "📎 Attachment", "edited_event": "✏️ Message edited",
        "reaction_event": "💟 Reaction", "chat_event": "💬 Chat update",
        "edit_reference": "Message {message_id} was edited:",
        "reaction_reference": "Reaction {reaction} to message {message_id}",
        "reaction_removed": "Reaction removed from message {message_id}",
        "message_details": "Full message details in message.txt.",
        "zip_details": "Download the ZIP for attachments and full message details.",
        "file_unavailable": "A saved attachment is unavailable; captured details remain local.",
        "file_limit": "An attachment is unavailable or exceeds Telegram's file-upload limit; files remain local.",
        "file_integrity": "A saved attachment failed its integrity check; files remain local.",
        "bundle_limit": "The combined download exceeds Telegram's file-upload limit; files remain local.",
        "download_file": "⬇ Download file", "download_zip": "⬇ Download ZIP",
        "download_unavailable": "This download is unavailable.",
        "download_denied": "This download is unavailable for this conversation.",
        "download_wait": "Wait a moment before requesting another copy.",
        "download_sending": "Sending a downloadable file copy.",
        "download_caption": "Requested download. Open the attachment and use Telegram’s Save/Download option.",
        "pair_requested": (
            "Pairing requested. Ask the administrator to approve your Telegram identity in the dashboard. "
            "Your linked Messenger messages and media can then be saved and relayed here. "
            "The server administrator has access to that account's archive. Local copies expire after three days; Telegram copies remain. "
            "Use /disconnect to cancel or stop logging. /en selects English; /pl selects Polish for your bot messages."),
        "pair_unavailable": "This invitation is expired, already used, or unavailable. Ask the administrator for a new invitation.",
        "disconnected": "Telegram pairing removed. Your linked logger is being stopped. Existing Telegram copies remain.",
        "privacy": (
            "This bot delivers copies from a Messenger account linked by the administrator. "
            "The administrator can access stored credentials and archives. Local message copies expire after three days; Telegram copies remain. "
            "Captured chat text keeps its original language. /en and /pl change only your bot notifications. Use /disconnect to stop your linked logger."),
        "help": (
            "Open the administrator's invitation link to request pairing. Your account remains separate from other users. "
            "Commands: /en — English, /pl — Polish, /privacy, /disconnect. Never send Messenger cookies or passwords to this bot."),
        "language_saved": "Language set to English for your future bot messages in all your conversation topics. Other users are unchanged. Use /pl to switch back to Polish.",
    },
    "pl": {
        "deleted_message": "🗑 Usunięta wiadomość", "once_message": "👁 Wiadomość jednorazowa",
        "captured_message": "💬 Zapisana wiadomość", "sent": "Wysłano", "deleted": "Usunięto",
        "not_captured": "Nie zapisano",
        "missing_content": "Treść nie została zapisana przed usunięciem i nie można jej odzyskać z dziennika.",
        "empty_capture": "Nie zapisano obsługiwanej treści ani załączników.",
        "edited_version": "✏️ Edytowana wersja", "empty_text": "[Pusta treść]",
        "reply_to": "↩ Odpowiedź na: ", "unknown": "Nieznany uczestnik", "self": "Ty",
        "unnamed": "Rozmowa bez nazwy", "conversation": "Rozmowa", "unmatched": "Nieprzypisane wiadomości",
        "mentions": "Wzmianki", "attachment": "Załącznik", "image": "Zdjęcie", "video": "Film",
        "gif": "GIF", "sticker": "Naklejka", "voice": "Wiadomość głosowa", "audio": "Nagranie audio",
        "document": "Plik", "file": "Plik",
        "saved": "Zapisano", "pending": "Pobieranie…", "failed": "Niedostępne",
        "interrupted": "Pobieranie przerwane", "metadata_only": "Tylko metadane",
        "once": "jednorazowe wyświetlenie", "replay": "ograniczone odtwarzanie", "disappearing": "znikający załącznik",
        "deleted_attachment": "📎 Usunięty załącznik", "once_attachment": "👁 Załącznik jednorazowy",
        "attachment_title": "📎 Załącznik", "edited_event": "✏️ Edytowana wiadomość",
        "reaction_event": "💟 Reakcja", "chat_event": "💬 Zmiana w rozmowie",
        "edit_reference": "Edytowano wiadomość {message_id}:",
        "reaction_reference": "Reakcja {reaction} na wiadomość {message_id}",
        "reaction_removed": "Usunięto reakcję na wiadomość {message_id}",
        "message_details": "Pełne szczegóły wiadomości znajdują się w pliku message.txt.",
        "zip_details": "Pobierz ZIP z załącznikami i pełnymi szczegółami wiadomości.",
        "file_unavailable": "Zapisany załącznik jest niedostępny. Szczegóły wiadomości pozostają na serwerze.",
        "file_limit": "Załącznik jest niedostępny lub przekracza limit wysyłania plików Telegrama. Pliki pozostają na serwerze.",
        "file_integrity": "Nie udało się potwierdzić integralności załącznika. Pliki pozostają na serwerze.",
        "bundle_limit": "Łączny rozmiar przekracza limit wysyłania plików Telegrama. Pliki pozostają na serwerze.",
        "download_file": "⬇ Pobierz plik", "download_zip": "⬇ Pobierz ZIP",
        "download_unavailable": "Pobieranie tego pliku jest niedostępne.",
        "download_denied": "Ten plik nie jest dostępny do pobrania w tej rozmowie.",
        "download_wait": "Poczekaj chwilę przed ponownym pobraniem kopii.",
        "download_sending": "Wysyłam kopię pliku do pobrania.",
        "download_caption": "Kopia na żądanie. Otwórz załącznik i użyj opcji zapisu lub pobierania w Telegramie.",
        "pair_requested": (
            "Wysłano prośbę o połączenie. Poproś administratora o zatwierdzenie Twojego konta Telegram w panelu. "
            "Wiadomości i multimedia z połączonego Messengera będą mogły być zapisywane i przesyłane tutaj. "
            "Administrator serwera ma dostęp do archiwum tego konta. Lokalne kopie wygasają po trzech dniach; kopie w Telegramie pozostają. "
            "Użyj /disconnect, aby anulować połączenie lub zatrzymać zapis. /en ustawia język angielski, a /pl polski dla Twoich powiadomień."),
        "pair_unavailable": "To zaproszenie wygasło, zostało już użyte lub jest niedostępne. Poproś administratora o nowe.",
        "disconnected": "Usunięto połączenie z Telegramem. Zapis dla Twojego konta jest zatrzymywany. Dotychczasowe kopie w Telegramie pozostają.",
        "privacy": (
            "Ten bot przesyła kopie z konta Messenger połączonego przez administratora. "
            "Administrator ma dostęp do zapisanej sesji i archiwum. Lokalne kopie wiadomości wygasają po trzech dniach; kopie w Telegramie pozostają. "
            "Treść rozmów zachowuje oryginalny język. /en i /pl zmieniają tylko Twoje powiadomienia bota. Użyj /disconnect, aby zatrzymać zapis."),
        "help": (
            "Otwórz zaproszenie od administratora, aby poprosić o połączenie. Twoje konto jest oddzielone od innych użytkowników. "
            "Polecenia: /en — English, /pl — polski, /privacy, /disconnect. Nigdy nie wysyłaj temu botowi ciasteczek Messengera ani haseł."),
        "language_saved": "Ustawiono język polski dla Twoich nowych powiadomień we wszystkich tematach rozmów. Ustawienia innych osób pozostają bez zmian. Użyj /en, aby przełączyć na angielski.",
    },
}


def language(value):
    return value if isinstance(value, str) and value in LANGUAGES else DEFAULT_LANGUAGE


def tr(locale, key, **values):
    return TEXT[language(locale)][key].format(**values)


def attachments(count, locale):
    if language(locale) == "en":
        return f"{count} " + ("attachment" if count == 1 else "attachments")
    word = "załącznik" if count == 1 else "załączniki" if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14) else "załączników"
    return f"{count} {word}"
