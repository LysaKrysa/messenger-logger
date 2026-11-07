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


TEXT["en"].update({
    "disabled": "Downloads off",
    "welcome": "Welcome to Messenger Logger. Send /help to see all commands and control your linked account. Your settings apply only to you.",
    "not_paired": "No approved Messenger account is linked to your Telegram identity. Open an administrator invitation and wait for approval first. /help lists the commands.",
    "command_usage": "Invalid command arguments. Send /help for commands and examples.",
    "pause_saved": "Capture pause requested for your account. The worker will stop shortly. Messages received while paused are not imported later.",
    "resume_saved": "Capture resume requested for your account. Once connected, only new events will be captured. No missed history is imported.",
    "mode_saved": "Forwarding mode: {value}. Local capture continues; the change applies to future events without reconnecting Messenger.",
    "media_saved": "Attachment downloads: {value}. The change applies to new and queued downloads; an in-progress download may finish. Existing saved files are preserved.",
    "quiet_saved": "Telegram notifications are quiet until {value}. Local capture and media downloads continue. Skipped notifications will not be replayed. Use /quiet off to end quiet mode early.",
    "quiet_cleared": "Quiet mode ended. Future Telegram notifications resume; skipped notifications are not replayed.",
    "settings_title": "Your settings", "status_title": "Your logger status",
    "label_capture": "Capture", "label_connection": "Messenger connection", "label_last_event": "Last captured event",
    "label_delivery": "Telegram delivery", "label_last_sent": "Last confirmed delivery", "label_checked": "Connection checked",
    "label_mode": "Forwarding", "label_media": "Attachment downloads", "label_language": "Language", "label_quiet": "Quiet until",
    "value_active": "Requested on", "value_paused": "Paused", "value_connected": "Connected",
    "value_reconnecting": "Connecting / reconnecting", "value_unknown": "Unknown / no fresh worker report",
    "value_sent": "Last notification confirmed sent", "value_rejected": "Last notification rejected",
    "value_unconfirmed": "Last delivery unconfirmed; no automatic resend", "value_never": "No recorded result",
    "value_on": "On", "value_off": "Off", "value_deleted": "Deleted messages only", "value_all": "All supported chat events",
    "help": (
        "Commands (English / Polish aliases):\n\n"
        "/status · /stan — connection, capture, last event and delivery health\n"
        "/pause · /pauza — pause capture for your account\n"
        "/resume · /wznow — resume new events after connection\n"
        "/mode deleted · /tryb usuniete — forward deleted messages\n"
        "/mode all · /tryb wszystkie — forward all supported chat events\n"
        "/media on|off · /media wlacz|wylacz — attachment downloads\n"
        "/quiet 1h · /cisza 1g — skip Telegram notifications for an hour\n"
        "/quiet off · /cisza wylacz — end quiet mode\n"
        "/settings · /ustawienia — your current settings\n"
        "/en — English · /pl — Polish\n"
        "/privacy · /prywatnosc — privacy information\n"
        "/disconnect · /odlacz (/stop) — unlink Telegram and stop capture\n"
        "/start — welcome and help\n"
        "/help · /pomoc — this list\n\n"
        "Commands affect only your linked account across its topics. Polish aliases work in either language. "
        "Quiet durations: 5m, 1h, 24h (maximum one day); quiet mode keeps local capture running and skips notifications without a backlog. "
        "Resume does not import missed history. View-once alerts follow the administrator's separate setting. "
        "Original chat content keeps its language. Never send passwords or cookies here."),
})
TEXT["pl"].update({
    "disabled": "Pobieranie wyłączone",
    "welcome": "Witaj w Messenger Logger. Wyślij /help lub /pomoc, aby zobaczyć wszystkie polecenia i zarządzać połączonym kontem. Ustawienia dotyczą tylko Ciebie.",
    "not_paired": "Do Twojego Telegrama nie jest przypisane zatwierdzone konto Messenger. Otwórz zaproszenie administratora i poczekaj na zatwierdzenie. /help lub /pomoc pokazuje polecenia.",
    "command_usage": "Nieprawidłowe argumenty. Wyślij /help lub /pomoc, aby zobaczyć polecenia i przykłady.",
    "pause_saved": "Zlecono wstrzymanie zapisu dla Twojego konta. Proces wkrótce się zatrzyma. Wiadomości z okresu pauzy nie zostaną później pobrane.",
    "resume_saved": "Zlecono wznowienie zapisu dla Twojego konta. Po połączeniu będą zapisywane nowe zdarzenia. Pominięta historia nie zostanie pobrana.",
    "mode_saved": "Tryb przesyłania: {value}. Zapis lokalny trwa dalej. Zmiana dotyczy kolejnych zdarzeń i nie wymaga ponownego łączenia z Messengerem.",
    "media_saved": "Pobieranie załączników: {value}. Zmiana dotyczy nowych i oczekujących plików. Rozpoczęte pobieranie może się zakończyć. Już zapisane pliki pozostają.",
    "quiet_saved": "Powiadomienia Telegrama wyciszono do {value}. Zapis lokalny i pobieranie multimediów trwają dalej. Pominięte powiadomienia nie zostaną wysłane później. /cisza wylacz kończy wyciszenie wcześniej.",
    "quiet_cleared": "Zakończono wyciszenie. Nowe powiadomienia będą przesyłane. Pominięte powiadomienia nie zostaną wysłane ponownie.",
    "settings_title": "Twoje ustawienia", "status_title": "Stan Twojego rejestratora",
    "label_capture": "Zapis", "label_connection": "Połączenie z Messengerem", "label_last_event": "Ostatnie zapisane zdarzenie",
    "label_delivery": "Dostarczanie do Telegrama", "label_last_sent": "Ostatnia potwierdzona wysyłka", "label_checked": "Sprawdzono połączenie",
    "label_mode": "Przesyłanie", "label_media": "Pobieranie załączników", "label_language": "Język", "label_quiet": "Wyciszenie do",
    "value_active": "Zlecono włączenie", "value_paused": "Wstrzymany", "value_connected": "Połączono",
    "value_reconnecting": "Łączenie / ponowne łączenie", "value_unknown": "Nieznane / brak aktualnego raportu procesu",
    "value_sent": "Ostatnia wysyłka potwierdzona", "value_rejected": "Ostatnia wysyłka odrzucona",
    "value_unconfirmed": "Ostatnia wysyłka niepotwierdzona; bez automatycznego ponawiania", "value_never": "Brak zapisanego wyniku",
    "value_on": "Włączone", "value_off": "Wyłączone", "value_deleted": "Tylko usunięte wiadomości", "value_all": "Wszystkie obsługiwane zdarzenia rozmów",
    "help": (
        "Polecenia (angielskie / polskie odpowiedniki):\n\n"
        "/status · /stan — połączenie, zapis, ostatnie zdarzenie i stan wysyłki\n"
        "/pause · /pauza — wstrzymaj zapis dla swojego konta\n"
        "/resume · /wznow — wznów zapis nowych zdarzeń po połączeniu\n"
        "/mode deleted · /tryb usuniete — przesyłaj usunięte wiadomości\n"
        "/mode all · /tryb wszystkie — przesyłaj wszystkie obsługiwane zdarzenia\n"
        "/media on|off · /media wlacz|wylacz — pobieranie załączników\n"
        "/quiet 1h · /cisza 1g — pomijaj powiadomienia Telegrama przez godzinę\n"
        "/quiet off · /cisza wylacz — zakończ wyciszenie\n"
        "/settings · /ustawienia — Twoje aktualne ustawienia\n"
        "/en — English · /pl — polski\n"
        "/privacy · /prywatnosc — informacje o prywatności\n"
        "/disconnect · /odlacz (/stop) — odłącz Telegram i zatrzymaj zapis\n"
        "/start — powitanie i pomoc\n"
        "/help · /pomoc — ta lista\n\n"
        "Polecenia dotyczą tylko Twojego konta we wszystkich jego tematach. Obie wersje działają niezależnie od wybranego języka. "
        "Czas wyciszenia: 5m, 1h lub 1g, 24h (maksymalnie doba). Wyciszenie zachowuje zapis lokalny i pomija powiadomienia bez późniejszej kolejki. "
        "Wznowienie nie pobiera pominiętej historii. Powiadomienia o jednorazowych multimediach zależą od osobnego ustawienia administratora. "
        "Treść rozmów zachowuje oryginalny język. Nie wysyłaj tutaj haseł ani ciasteczek."),
})


def language(value):
    return value if isinstance(value, str) and value in LANGUAGES else DEFAULT_LANGUAGE


def tr(locale, key, **values):
    return TEXT[language(locale)][key].format(**values)


def attachments(count, locale):
    if language(locale) == "en":
        return f"{count} " + ("attachment" if count == 1 else "attachments")
    word = "załącznik" if count == 1 else "załączniki" if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14) else "załączników"
    return f"{count} {word}"
