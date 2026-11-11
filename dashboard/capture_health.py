"""Account-bound health reports and deduplicated, localized capture alerts."""
import json
import math

from .message_archive import bound
from .store import DashboardError
from telegram_i18n import tr

KINDS = ("decryption", "media", "queue")


class CaptureHealth:
    def __init__(self, store):
        self.store = store

    def report(self, row, value):
        now = self.store.clock()
        if (not isinstance(value, dict) or set(value) != {"revision", "started_at", "last_capture", "counts", "recent", "last"}
                or type(value["revision"]) is not int or value["revision"] != row["revision"]):
            raise DashboardError("Invalid capture health report.")
        for field in ("counts", "recent", "last"):
            if not isinstance(value[field], dict) or set(value[field]) != set(KINDS):
                raise DashboardError("Invalid health counters.")
        for kind in KINDS:
            if any(type(value[field][kind]) is not int or not 0 <= value[field][kind] < 2**53 for field in ("counts", "recent")) or value["recent"][kind] > value["counts"][kind]:
                raise DashboardError("Invalid health count.")
        for timestamp in [value["started_at"], value["last_capture"], *value["last"].values()]:
            if timestamp is not None and (type(timestamp) not in {int, float} or not math.isfinite(timestamp) or not 0 <= timestamp <= now + 5):
                raise DashboardError("Invalid health time.")
        if not value["started_at"]:
            raise DashboardError("Missing health start time.")
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            bound(db, row, worker=True)
            data = dict(value, pair_epoch=row["pair_epoch"], updated=now)
            db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("capture_health:" + row["id"], json.dumps(data)))

    def enabled(self, row):
        return self.store.setting("capture_alerts:" + row["id"] + ":" + str(row["pair_epoch"]), True) is True

    def preference(self, row, enabled):
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            bound(db, row)
            db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("capture_alerts:" + row["id"] + ":" + str(row["pair_epoch"]), json.dumps(bool(enabled))))

    def view(self, row):
        now = self.store.clock()
        report = self.store.setting("capture_health:" + row["id"], {})
        if report.get("pair_epoch") != row["pair_epoch"] or now - report.get("updated", 0) > 259200:
            report = {}
        fresh = report.get("revision") == row["revision"] and 0 <= now - report.get("updated", 0) <= 90
        worker = self.store.setting("worker_status:" + row["id"], {})
        connected = worker.get("revision") == row["revision"] and 0 <= now - worker.get("updated", 0) <= 90 and worker.get("connected") is True
        issues = [kind for kind in KINDS if report.get("recent", {}).get(kind, 0) >= (1 if kind == "queue" else 3)] if fresh else []
        state = "paused" if not row["desired"] else "unknown" if not fresh else "capture_attention" if issues else "capture_reporting"
        stats = self.store.setting("worker_stats:" + row["id"], {})
        if stats.get("pair_epoch") != row["pair_epoch"] or stats.get("revision") != row["revision"] or not 0 <= now - stats.get("updated", 0) <= 90:
            stats = {}
        with self.store.db() as db:
            deliveries = dict(db.execute("SELECT outcome,COUNT(*) FROM delivery_history WHERE account_id=? AND pair_epoch=? AND expires>? GROUP BY outcome", (row["id"], row["pair_epoch"], now)).fetchall())
        return {"report": report, "fresh": fresh, "connected": connected, "state": state, "issues": issues,
                "alerts": self.enabled(row), "stats": stats, "deliveries": deliveries}

    def alerts(self, row):
        """Claim before sending. Retried reports cannot repeat an ambiguous alert."""
        now, key = self.store.clock(), "capture_monitor:" + row["id"]
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                bound(db, row, worker=True)
            except DashboardError:
                return []
            raw = db.execute("SELECT value FROM settings WHERE key=?", ("capture_health:" + row["id"],)).fetchone()
            report = json.loads(raw[0]) if raw else {}
            if report.get("pair_epoch") != row["pair_epoch"] or report.get("revision") != row["revision"] or not 0 <= now - report.get("updated", 0) <= 90:
                return []
            raw = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            state = json.loads(raw[0]) if raw else {}
            if state.get("pair_epoch") != row["pair_epoch"]:
                state = {"pair_epoch": row["pair_epoch"], "active": {}, "last_sent": {}}
            enabled = self.enabled(row)
            notifications = []
            for kind in KINDS:
                recent, last = report["recent"][kind], report["last"][kind] or 0
                if recent >= (1 if kind == "queue" else 3):
                    if enabled and now - state["last_sent"].get(kind, -21600) >= 21600:
                        notifications.append("capture_alert_" + kind)
                        state["last_sent"][kind] = now
                    state["active"][kind] = last
            if state["active"] and not any(report["recent"].values()) and (report["last_capture"] or 0) > max(state["active"].values()):
                if enabled and state["last_sent"]:
                    notifications.append("capture_alert_recovery")
                state["active"] = {}
            db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(state)))
        return notifications

    def tick(self, api):
        for row in self.store.accounts(private=True):
            for key in self.alerts(row):
                # Check the destination again after claiming, before network I/O.
                current = self.store.account(row["id"], private=True)
                if not current or any(current[field] != row[field] for field in ("chat_id", "pair_epoch", "auth_epoch", "revision")):
                    continue
                language = self.store.telegram_language(row["chat_id"])
                service = "Instagram" if row["platform"] == "instagram" else "Messenger"
                message = tr(language, key)
                if row["platform"] == "instagram":
                    message = message.replace("/panel", "/panel instagram")
                try:
                    api.json("sendMessage", {"chat_id": row["chat_id"], "text": service + " · " + message, "link_preview_options": {"is_disabled": True}})
                except Exception:
                    pass  # Claimed alerts retain their cooldown after an uncertain send.
