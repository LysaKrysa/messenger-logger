"""Owner-scoped bot controls; no credentials, archive reads or Docker access."""
import re
from zoneinfo import ZoneInfo

from telegram_format import stamp
from telegram_i18n import tr

ALIASES = {
    "stan": "status", "pauza": "pause", "wznow": "resume", "wznów": "resume",
    "tryb": "mode", "cisza": "quiet", "ustawienia": "settings", "pomoc": "help",
    "prywatnosc": "privacy", "prywatność": "privacy", "odlacz": "disconnect", "odłącz": "disconnect",
    "polacz": "reconnect", "połącz": "reconnect",
    "rozmowy": "chats", "wysylki": "deliveries", "wysyłki": "deliveries",
    "panel": "dashboard",
}
CONTROLS = {"status", "pause", "resume", "mode", "media", "quiet", "settings"}


def argument(command, raw):
    value = raw.strip().casefold()
    if command == "mode":
        return {"deleted": "deleted", "usuniete": "deleted", "usunięte": "deleted", "all": "all", "wszystkie": "all"}.get(value)
    if command == "media":
        return {"on": True, "wlacz": True, "włącz": True, "off": False, "wylacz": False, "wyłącz": False}.get(value)
    if command == "quiet":
        if value in {"off", "wylacz", "wyłącz", "0"}:
            return 0
        match = re.fullmatch(r"([1-9][0-9]{0,3})([mhg])", value)
        if match:
            seconds = int(match[1]) * (60 if match[2] == "m" else 3600)
            return seconds if seconds <= 86400 else None
        return None
    return True if not value else None


def date(value, language):
    return stamp(value * 1000, ZoneInfo("Europe/Warsaw"), language) if value else tr(language, "value_never")


def overview(store, row, language, *, status=False):
    controls = store.controls(row)
    now = store.clock()
    fields = [("capture", tr(language, "value_active" if row["desired"] else "value_paused"))]
    if status:
        worker = store.setting("worker_status:" + row["id"], {})
        fresh = worker.get("revision") == row["revision"] and 0 <= now - worker.get("updated", 0) <= 90
        connection = "paused" if not row["desired"] else "connected" if fresh and worker.get("connected") else "reconnecting" if fresh else "unknown"
        delivery = store.setting("delivery_status:" + row["id"], {})
        fields += [("connection", tr(language, "value_" + connection)),
                   ("checked", date(worker.get("updated") if fresh else None, language)),
                   ("last_event", date(worker.get("last_event") if worker.get("last_event", 0) and worker["last_event"] >= now - 259200 else None, language)),
                   ("delivery", tr(language, "value_" + delivery.get("outcome", "never"))),
                   ("last_sent", date(delivery.get("last_sent"), language))]
    fields += [("mode", tr(language, "value_" + controls["mode"])),
               ("media", tr(language, "value_on" if controls["media"] else "value_off")),
               ("quiet", date(controls["quiet_until"], language) if controls["quiet_until"] > now else tr(language, "value_off")),
               ("language", "English" if language == "en" else "Polski")]
    return tr(language, "status_title" if status else "settings_title") + "\n\n" + "\n".join(tr(language, "label_" + key) + ": " + str(value) for key, value in fields)


def handle_control(store, user_id, command, raw, language):
    value = argument(command, raw)
    if value is None:
        return tr(language, "command_usage")
    row = store.user_account(user_id)
    if row is None:
        return tr(language, "not_paired")
    if command in {"status", "settings"}:
        return overview(store, row, language, status=command == "status")
    if not store.user_control(user_id, command, value):
        return tr(language, "not_paired")
    if command in {"pause", "resume"}:
        return tr(language, command + "_saved")
    if command == "mode":
        return tr(language, "mode_saved", value=tr(language, "value_" + value))
    if command == "media":
        return tr(language, "media_saved", value=tr(language, "value_on" if value else "value_off"))
    return tr(language, "quiet_saved", value=date(store.clock() + value, language)) if value else tr(language, "quiet_cleared")
