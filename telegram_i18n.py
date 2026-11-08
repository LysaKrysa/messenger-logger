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
            "Użyj /odlacz, aby anulować połączenie lub zatrzymać zapis. /en ustawia język angielski, a /pl polski dla Twoich powiadomień."),
        "pair_unavailable": "To zaproszenie wygasło, zostało już użyte lub jest niedostępne. Poproś administratora o nowe.",
        "disconnected": "Usunięto połączenie z Telegramem. Zapis dla Twojego konta jest zatrzymywany. Dotychczasowe kopie w Telegramie pozostają.",
        "privacy": (
            "Ten bot przesyła kopie z konta Messenger połączonego przez administratora. "
            "Administrator ma dostęp do zapisanej sesji i archiwum. Lokalne kopie wiadomości wygasają po trzech dniach; kopie w Telegramie pozostają. "
            "Treść rozmów zachowuje oryginalny język. /en i /pl zmieniają tylko Twoje powiadomienia bota. Użyj /odlacz, aby zatrzymać zapis."),
        "help": (
            "Otwórz zaproszenie od administratora, aby poprosić o połączenie. Twoje konto jest oddzielone od innych użytkowników. "
            "Polecenia: /en — English, /pl — polski, /privacy, /disconnect. Nigdy nie wysyłaj temu botowi ciasteczek Messengera ani haseł."),
        "language_saved": "Ustawiono język polski dla Twoich nowych powiadomień we wszystkich tematach rozmów. Ustawienia innych osób pozostają bez zmian. Użyj /en, aby przełączyć na angielski.",
    },
}


TEXT["en"].update({
    "offline_alert": "⚠️ Your Messenger logger is offline or is no longer reporting its connection. New messages may not be captured. Check /status; a temporary connection problem may recover automatically.",
    "reconnect_link": "Refresh your existing Messenger session:\n{url}\n\nThis single-use link expires in 15 minutes. After opening it, confirm the request in this Telegram chat before signing in. Only your originally approved Messenger account can be reconnected; your Telegram pairing stays unchanged. Use /reconnect if you need a fresh link.",
    "reconnect_unavailable": "A reconnect link is available only for your existing account after a sustained outage. Online, paused or unlinked accounts cannot request one. At most three links can be issued per hour. Check /status or contact the administrator.",
    "reconnect_confirmation": "A one-time reconnect link was opened. If that was you, approve below to open the temporary Facebook browser. Only your original Messenger account can be refreshed. Reject this request if you did not open the link.",
    "reconnect_approve": "I opened this link", "reconnect_reject": "Reject",
    "reconnect_approved": "Approved. Return to the browser and sign in to your original Messenger account.",
    "reconnect_rejected": "Request rejected. The link has been disabled.",
    "reconnect_expired": "This reconnect request is unavailable, expired or already used.",
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
        "Commands:\n\n"
        "/status — connection, capture, last event and delivery health\n"
        "/pause — pause capture for your account\n"
        "/resume — resume new events after connection\n"
        "/reconnect — one-time session refresh for your offline account\n"
        "/mode deleted — forward deleted messages\n"
        "/mode all — forward all supported chat events\n"
        "/media on|off — attachment downloads\n"
        "/quiet 1h — skip Telegram notifications for an hour\n"
        "/quiet off — end quiet mode\n"
        "/settings — your current settings\n"
        "/chats — conversation capture and forwarding filters\n"
        "/deliveries — delivery history and confirmed resends\n"
        "/en — English · /pl — Polish\n"
        "/privacy — privacy information\n"
        "/disconnect — unlink Telegram and stop capture\n"
        "/start — welcome and help\n"
        "/help — this list\n\n"
        "Commands affect only your linked account across its topics. "
        "Quiet durations: 5m, 1h, 24h (maximum one day); quiet mode keeps local capture running and skips notifications without a backlog. "
        "Resume does not import missed history. View-once alerts follow the administrator's separate setting. "
        "Original chat content keeps its language. Never send passwords or cookies here."),
})
TEXT["pl"].update({
    "offline_alert": "⚠️ Twój rejestrator Messengera utracił połączenie lub przestał raportować jego stan. Nowe wiadomości mogą nie być zapisywane. Sprawdź /stan. Przejściowy problem z połączeniem może ustąpić samoczynnie.",
    "reconnect_link": "Odnów sesję swojego istniejącego konta Messenger:\n{url}\n\nTen jednorazowy link wygasa po 15 minutach. Po otwarciu potwierdź żądanie w tym czacie Telegrama przed logowaniem. Możesz ponownie połączyć tylko pierwotnie zatwierdzone konto Messenger. Połączenie z Telegramem pozostaje bez zmian. /polacz pozwala poprosić o nowy link.",
    "reconnect_unavailable": "Link ponownego połączenia jest dostępny tylko dla Twojego istniejącego konta po dłuższej utracie połączenia. Nie można go uzyskać dla konta działającego, wstrzymanego ani odłączonego. Limit wynosi trzy linki na godzinę. Sprawdź /stan lub skontaktuj się z administratorem.",
    "reconnect_confirmation": "Otwarto jednorazowy link ponownego połączenia. Jeśli to Ty, potwierdź poniżej, aby otworzyć tymczasową przeglądarkę Facebooka. Można odnowić tylko pierwotne konto Messenger. Odrzuć żądanie, jeśli link został otwarty przez kogoś innego.",
    "reconnect_approve": "To ja — kontynuuj", "reconnect_reject": "Odrzuć",
    "reconnect_approved": "Potwierdzono. Wróć do przeglądarki i zaloguj się na swoje pierwotne konto Messenger.",
    "reconnect_rejected": "Odrzucono żądanie. Link został wyłączony.",
    "reconnect_expired": "To żądanie ponownego połączenia jest niedostępne, wygasło lub zostało już wykorzystane.",
    "disabled": "Pobieranie wyłączone",
    "welcome": "Witaj w Messenger Logger. Wyślij /pomoc, aby zobaczyć wszystkie polecenia i zarządzać połączonym kontem. Ustawienia dotyczą tylko Ciebie.",
    "not_paired": "Do Twojego Telegrama nie jest przypisane zatwierdzone konto Messenger. Otwórz zaproszenie administratora i poczekaj na zatwierdzenie. /pomoc pokazuje polecenia.",
    "command_usage": "Nieprawidłowe argumenty. Wyślij /pomoc, aby zobaczyć polecenia i przykłady.",
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
        "Polecenia:\n\n"
        "/stan — połączenie, zapis, ostatnie zdarzenie i stan wysyłki\n"
        "/pauza — wstrzymaj zapis dla swojego konta\n"
        "/wznow — wznów zapis nowych zdarzeń po połączeniu\n"
        "/polacz — jednorazowe odnowienie sesji konta po utracie połączenia\n"
        "/tryb usuniete — przesyłaj usunięte wiadomości\n"
        "/tryb wszystkie — przesyłaj wszystkie obsługiwane zdarzenia\n"
        "/media wlacz|wylacz — pobieranie załączników\n"
        "/cisza 1g — pomijaj powiadomienia Telegrama przez godzinę\n"
        "/cisza wylacz — zakończ wyciszenie\n"
        "/ustawienia — Twoje aktualne ustawienia\n"
        "/rozmowy — filtry zapisu i przesyłania rozmów\n"
        "/wysylki — historia wysyłek i potwierdzone ponowienie\n"
        "/en — język angielski · /pl — język polski\n"
        "/prywatnosc — informacje o prywatności\n"
        "/odlacz — odłącz Telegram i zatrzymaj zapis\n"
        "/start — powitanie i pomoc\n"
        "/pomoc — ta lista\n\n"
        "Polecenia dotyczą tylko Twojego konta we wszystkich jego tematach. "
        "Czas wyciszenia: 5m, 1g, 24g (maksymalnie doba). Wyciszenie zachowuje zapis lokalny i pomija powiadomienia bez późniejszej kolejki. "
        "Wznowienie nie pobiera pominiętej historii. Powiadomienia o jednorazowych multimediach zależą od osobnego ustawienia administratora. "
        "Treść rozmów zachowuje oryginalny język. Nie wysyłaj tutaj haseł ani ciasteczek."),
})


TEXT["en"].update({
    "chats_title": "Your conversations", "chats_note": "Capture and Telegram forwarding are separate. Changes apply to new events. Exclusion stops capture and downloads; forwarding off keeps local capture. Existing local content expires normally.",
    "capture_default": "Default capture", "default_all": "All except excluded", "default_include": "Included only",
    "chats_empty": "No conversations reported yet. Keep Messenger connected and wait for a fresh chat event; past messages are not imported.",
    "chat_fallback": "Conversation {value}", "unknown_chat": "Conversation unavailable",
    "capture_on": "Capture on", "capture_off": "Excluded", "capture_setting": "Capture setting", "forward_setting": "Telegram forwarding",
    "chat_controls": "The first three buttons control capture. The next four control Telegram forwarding. Exclusion always prevents forwarding. Default forwarding follows your account mode; an explicit deleted-only rule waits for deletion, including view-once content.",
    "inherit": "Default capture", "include": "Include capture", "exclude": "Exclude capture",
    "forward_inherit": "Default forwarding", "forward_all": "Forward all", "forward_deleted": "Deleted only", "forward_off": "Forwarding off",
    "page_previous": "Previous", "page_next": "Next", "refresh": "Refresh", "back_chats": "Back to conversations", "back_deliveries": "Back to history",
    "deliveries_title": "Your Telegram deliveries", "deliveries_note": "Sent means Telegram confirmed acceptance. Failed means rejected or not sent. Uncertain means delivery could not be confirmed; it may already have arrived. No automatic resend. Records and encrypted resend content expire within 72 hours of capture; cache limits may make content unavailable sooner. Quiet or filtered events are not attempted and do not appear here.",
    "deliveries_empty": "No retained delivery attempts.", "delivery_details": "Details / resend",
    "delivery_sent": "Sent", "delivery_failed": "Failed", "delivery_uncertain": "Uncertain", "delivery_sending": "Sending",
    "kind_message": "Message", "kind_deleted": "Deleted message", "kind_view_once": "View-once content", "kind_edit": "Edit", "kind_reaction": "Reaction", "kind_notification": "Notification",
    "resend_warning": "Resending may create a duplicate, especially if this notification is already sent or has an uncertain result. This sends the retained original only to your paired private Telegram chat. Current conversation filters still apply; quiet mode is bypassed for this explicit request.",
    "resend_expiry": "Content expires: {value}. This confirmation is single use and valid for up to 10 minutes.",
    "resend_confirm": "Confirm resend · may duplicate", "resend_unavailable": "Resend unavailable: content expired, cache limit reached, a send is in progress, or current conversation filters block it.",
    "resend_sending": "Resending once…", "resend_result": "Resend result: {value}. No automatic retry.",
    "menu_denied": "These controls belong to the paired account owner in their private bot chat.",
    "menu_unavailable": "Unavailable, expired, already used, or rate limited. Open the command again and check your current settings.", "menu_updated": "Updated",
})
TEXT["pl"].update({
    "chats_title": "Twoje rozmowy", "chats_note": "Zapis i przesyłanie do Telegrama są niezależne. Zmiany dotyczą nowych zdarzeń. Wykluczenie zatrzymuje zapis i pobieranie; wyłączenie przesyłania zachowuje zapis lokalny. Wcześniejsze dane wygasają jak dotychczas.",
    "capture_default": "Domyślny zapis", "default_all": "Wszystkie poza wykluczonymi", "default_include": "Tylko wybrane",
    "chats_empty": "Brak zgłoszonych rozmów. Pozostaw Messengera połączonego i poczekaj na nowe zdarzenie. Dawne wiadomości nie są pobierane.",
    "chat_fallback": "Rozmowa {value}", "unknown_chat": "Rozmowa niedostępna",
    "capture_on": "Zapis włączony", "capture_off": "Wykluczona", "capture_setting": "Ustawienie zapisu", "forward_setting": "Przesyłanie do Telegrama",
    "chat_controls": "Pierwsze trzy przyciski sterują zapisem. Kolejne cztery sterują przesyłaniem do Telegrama. Wykluczenie zawsze blokuje przesyłanie. Domyślne przesyłanie używa trybu konta. Osobna reguła tylko usuniętych czeka na usunięcie, także dla treści jednorazowych.",
    "inherit": "Domyślny zapis", "include": "Uwzględnij w zapisie", "exclude": "Wyklucz z zapisu",
    "forward_inherit": "Domyślne przesyłanie", "forward_all": "Przesyłaj wszystko", "forward_deleted": "Tylko usunięte", "forward_off": "Przesyłanie wyłączone",
    "page_previous": "Poprzednie", "page_next": "Następne", "refresh": "Odśwież", "back_chats": "Wróć do rozmów", "back_deliveries": "Wróć do historii",
    "deliveries_title": "Twoje wysyłki do Telegrama", "deliveries_note": "Wysłano oznacza potwierdzenie przyjęcia przez Telegram. Błąd oznacza odrzucenie lub brak wysyłki. Niepewny wynik oznacza brak potwierdzenia; wiadomość mogła już dotrzeć. Bez automatycznego ponawiania. Historia i zaszyfrowana treść do ponowienia wygasają do 72 godzin od zapisu. Limity pamięci mogą wcześniej uniemożliwić ponowienie. Zdarzenia wyciszone i odfiltrowane nie są wysyłane i nie pojawiają się tutaj.",
    "deliveries_empty": "Brak zachowanych prób wysyłki.", "delivery_details": "Szczegóły / ponów",
    "delivery_sent": "Wysłano", "delivery_failed": "Błąd", "delivery_uncertain": "Niepewny wynik", "delivery_sending": "Wysyłanie",
    "kind_message": "Wiadomość", "kind_deleted": "Usunięta wiadomość", "kind_view_once": "Treść jednorazowa", "kind_edit": "Edycja", "kind_reaction": "Reakcja", "kind_notification": "Powiadomienie",
    "resend_warning": "Ponowienie może utworzyć duplikat, zwłaszcza gdy wysyłka była udana lub ma niepewny wynik. Zachowana oryginalna treść trafi tylko do Twojego połączonego prywatnego czatu Telegrama. Obecne filtry rozmów nadal obowiązują. To świadome żądanie pomija wyciszenie.",
    "resend_expiry": "Treść wygasa: {value}. Potwierdzenie jest jednorazowe i ważne maksymalnie 10 minut.",
    "resend_confirm": "Potwierdź ponowienie · możliwy duplikat", "resend_unavailable": "Ponowienie niedostępne: treść wygasła, przekroczono limit pamięci, trwa wysyłka lub blokują ją obecne filtry rozmów.",
    "resend_sending": "Jednorazowe ponawianie…", "resend_result": "Wynik ponowienia: {value}. Bez automatycznego powtarzania.",
    "menu_denied": "Te ustawienia są dostępne tylko właścicielowi połączonego konta w jego prywatnym czacie z botem.",
    "menu_unavailable": "Niedostępne, wygasłe, wykorzystane lub przekroczono limit. Otwórz polecenie ponownie i sprawdź ustawienia.", "menu_updated": "Zaktualizowano",
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
