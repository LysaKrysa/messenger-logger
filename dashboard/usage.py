"""Bounded local resource sampling. No cloud credentials or message bodies."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import time

from .store import DashboardError

GIB = 1024 ** 3
GB = 1000 ** 3
TB = 1000 ** 4
SOURCE = "https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm"
VERIFIED = "2026-11-07"
# Current A1 reference; saved dashboard budgets can be changed for a tenancy.
DEFAULTS = {
    "ocpus": None, "memory_gb": None,
    "cpu_hours_limit": 1500.0, "memory_hours_limit": 9000.0,
    "transfer_tb_limit": 10.0, "storage_gb_limit": 200.0,
    "object_gb_limit": 20.0, "requests_limit": 50000, "backups_limit": 5,
    "storage_gb": None, "object_gb": None, "object_requests": None, "backups": None,
}
INTEGER_FIELDS = {"requests_limit", "backups_limit", "object_requests", "backups"}
OPTIONAL_FIELDS = {"ocpus", "memory_gb", "storage_gb", "object_gb", "object_requests", "backups"}
MAXIMUM = {"ocpus": 1024, "memory_gb": 65536, "cpu_hours_limit": 1e8,
           "memory_hours_limit": 1e10, "transfer_tb_limit": 1e6,
           "storage_gb_limit": 1e9, "object_gb_limit": 1e9,
           "requests_limit": 1e12, "backups_limit": 1000000,
           "storage_gb": 1e9, "object_gb": 1e9, "object_requests": 1e12, "backups": 1000000}


def number(value, *, minimum=0, maximum=1e30):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and minimum <= value <= maximum else None


def month_window(now):
    dt = datetime.fromtimestamp(now, timezone.utc)
    start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start.strftime("%Y-%m"), start.timestamp(), end.timestamp()


def load_config(value, now):
    result = dict(DEFAULTS)
    if not isinstance(value, dict):
        return result
    for key in DEFAULTS:
        candidate = number(value.get(key), minimum=0 if key in OPTIONAL_FIELDS else .000001,
                           maximum=MAXIMUM[key])
        if candidate is not None and (key not in INTEGER_FIELDS or candidate == int(candidate)):
            result[key] = int(candidate) if key in INTEGER_FIELDS else candidate
    if result["ocpus"] == 0 or result["memory_gb"] == 0:
        result["ocpus"] = result["memory_gb"] = None
    result["saved_at"] = number(value.get("saved_at"))
    result["manual_month"] = value.get("manual_month") if re.fullmatch(r"\d{4}-\d{2}", str(value.get("manual_month", ""))) else None
    if result["manual_month"] != month_window(now)[0]:
        result["object_requests"] = None
    return result


def parse_config(form, now):
    result = {}
    for key in DEFAULTS:
        raw = form.get(key, "")
        if not isinstance(raw, str) or len(raw) > 30:
            raise DashboardError("Use finite, non-negative usage values within the allowed range.")
        if not raw.strip() and key in OPTIONAL_FIELDS:
            result[key] = None
            continue
        try:
            value = float(raw)
        except ValueError:
            raise DashboardError("Enter a number for each usage limit, or leave optional values blank.") from None
        minimum = .000001 if key not in OPTIONAL_FIELDS or key in {"ocpus", "memory_gb"} else 0
        if number(value, minimum=minimum, maximum=MAXIMUM[key]) is None or (key in INTEGER_FIELDS and value != int(value)):
            raise DashboardError("Use finite, non-negative usage values within the allowed range.")
        result[key] = int(value) if key in INTEGER_FIELDS else value
    if (result["ocpus"] is None) != (result["memory_gb"] is None):
        raise DashboardError("Enter both the allocated OCPUs and memory, or leave both blank.")
    result.update(saved_at=now, manual_month=month_window(now)[0])
    return result


def read_json(path, maximum=262144):
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(maximum + 1)
        value = json.loads(raw) if len(raw) <= maximum else None
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path, value, mode):
    temporary = path.with_name(path.name + ".new")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(value, handle, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, mode)
    temporary.replace(path)


class LinuxResources:
    def __init__(self, data_root, *, proc="/proc", sys="/sys", root="/", cpus=os.cpu_count):
        self.data_root, self.proc, self.sys, self.root = map(Path, (data_root, proc, sys, root))
        self.cpus = cpus

    def text(self, name):
        try:
            with (self.proc / name).open() as handle:
                return handle.read(65536)
        except OSError:
            return ""

    def disk(self, path):
        try:
            value = shutil.disk_usage(path)
            return {"total": value.total, "used": value.used, "free": value.free}
        except OSError:
            return None

    def sample(self):
        cpu = None
        for line in self.text("stat").splitlines():
            values = line.split()
            if values[:1] == ["cpu"]:
                try:
                    ticks = [int(v) for v in values[1:9]]
                    if len(ticks) >= 4 and min(ticks) >= 0:
                        # guest/guest_nice are already included in user/nice.
                        cpu = {"total": sum(ticks), "idle": ticks[3] + (ticks[4] if len(ticks) > 4 else 0)}
                except ValueError:
                    pass
                break
        memory = {}
        for line in self.text("meminfo").splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[1].isdigit() and parts[2] == "kB":
                memory[parts[0].rstrip(":")] = int(parts[1]) * 1024
        total, available = memory.get("MemTotal"), memory.get("MemAvailable")
        ram = {"total": total, "used": max(0, total - available), "available": available} if total and available is not None and available <= total else None
        swap_total, swap_free = memory.get("SwapTotal"), memory.get("SwapFree")
        swap = {"total": swap_total, "used": max(0, swap_total - swap_free)} if swap_total is not None and swap_free is not None and swap_free <= swap_total else None
        interfaces = set()
        try:
            interfaces = {entry.name for entry in (self.sys / "class/net").iterdir()
                          if (entry / "device").exists() and not entry.name.startswith(("lo", "veth", "docker", "br-", "virbr"))}
        except OSError:
            pass
        if not interfaces:
            for line in self.text("net/route").splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 4 and parts[1] == "00000000" and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", parts[0]):
                    if not parts[0].startswith(("lo", "veth", "docker", "br-", "virbr")):
                        interfaces.add(parts[0])
        net = {}
        for line in self.text("net/dev").splitlines():
            if ":" not in line:
                continue
            name, raw = line.split(":", 1)
            if name.strip() not in interfaces:
                continue
            parts = raw.split()
            try:
                rx, tx = int(parts[0]), int(parts[8])
                if 0 <= rx < 2**64 and 0 <= tx < 2**64:
                    net[name.strip()] = {"rx": rx, "tx": tx}
            except (IndexError, ValueError):
                pass
        try:
            uptime = number(float(self.text("uptime").split()[0]))
        except (ValueError, IndexError):
            uptime = None
        boot_id = self.text("sys/kernel/random/boot_id").strip()
        if not re.fullmatch(r"[a-fA-F0-9-]{1,64}", boot_id):
            boot_id = None
        try:
            same_disk = self.data_root.stat().st_dev == self.root.stat().st_dev
        except OSError:
            same_disk = None
        return {"same_disk": same_disk, "cpu_ticks": cpu, "cpus": self.cpus(), "memory": ram, "swap": swap,
                "data_disk": self.disk(self.data_root), "root_disk": self.disk(self.root),
                "net": net, "boot_id": boot_id, "uptime": uptime}


def archive_sizes(data_root, identifiers, *, maximum=50000, budget=.5, monotonic=time.monotonic):
    """Count metadata only, skip links and bound work independently of archive size."""
    result, visited = {}, 0
    deadline = monotonic() + budget
    for ident in identifiers:
        if not isinstance(ident, str) or not re.fullmatch(r"[a-f0-9]{32}", ident):
            continue
        root = Path(data_root) / ident
        total, complete, stack = 0, True, [root]
        while stack:
            if visited >= maximum or monotonic() >= deadline:
                complete = False
                break
            directory = stack.pop()
            try:
                if directory.is_symlink():
                    continue
                with os.scandir(directory) as entries:
                    for entry in entries:
                        visited += 1
                        if visited > maximum or monotonic() >= deadline:
                            complete = False
                            break
                        metadata = entry.stat(follow_symlinks=False)
                        if stat.S_ISDIR(metadata.st_mode):
                            stack.append(Path(entry.path))
                        elif stat.S_ISREG(metadata.st_mode):
                            total += metadata.st_size
                if not complete:
                    break
            except FileNotFoundError:
                continue
            except OSError:
                complete = False
        result[ident] = {"bytes": total, "complete": complete}
    return result


class UsageCollector:
    INTERVAL = 60

    def __init__(self, store, data_root, status_root, *, clock=time.time, resources=None):
        self.store, self.data_root, self.status_root, self.clock = store, Path(data_root), Path(status_root), clock
        self.resources = resources or LinuxResources(self.data_root)
        self.path = self.status_root / "usage-state.json"
        self.state = read_json(self.path)
        self.last_attempt = None

    def update(self, rows):
        now = self.clock()
        if self.last_attempt is not None and 0 <= now - self.last_attempt < self.INTERVAL:
            return
        self.last_attempt = now
        try:
            self._sample(now, rows)
        except Exception:
            # Monitoring must not interrupt account reconciliation or expose an
            # exception containing a filesystem path or private configuration.
            print("Usage sampling unavailable; account management continues.", flush=True)

    def _sample(self, now, rows):
        current = self.resources.sample()
        config = load_config(self.store.setting("usage_config"), now)
        key, month_start, month_end = month_window(now)
        state = self.state if self.state.get("version") == 1 else {}
        previous = state.get("last") if isinstance(state.get("last"), dict) else {}
        old_month = state.get("month") if isinstance(state.get("month"), dict) else {}
        saved_month = old_month.get("key", "")
        if isinstance(saved_month, str) and re.fullmatch(r"\d{4}-\d{2}", saved_month) and saved_month > key:
            return  # A backward clock crossing a month must not destroy the ledger.
        same_month = saved_month == key
        month = {"key": key, "since": max(month_start, number(old_month.get("since")) or now) if same_month else max(month_start, number(previous.get("at")) or now),
                 "outbound": number(old_month.get("outbound")) or 0 if same_month else 0,
                 "cpu_hours": number(old_month.get("cpu_hours")) or 0 if same_month else 0,
                 "memory_hours": number(old_month.get("memory_hours")) or 0 if same_month else 0,
                 "allocation_seconds": number(old_month.get("allocation_seconds")) or 0 if same_month else 0,
                 "gaps": bool(old_month.get("gaps")) if same_month else False}
        at = number(previous.get("at"))
        wall = now - at if at is not None else None
        previous_uptime = number(previous.get("uptime"))
        uptime = number(current.get("uptime"))
        same_boot = bool(current.get("boot_id") and current["boot_id"] == previous.get("boot_id"))
        elapsed = uptime - previous_uptime if same_boot and uptime is not None and previous_uptime is not None else None
        valid_interval = wall is not None and wall > 0 and elapsed is not None and elapsed > 0 and abs(wall - elapsed) < 300
        cpu_percent, rx_rate, tx_rate = None, None, None
        if valid_interval:
            portion = min(1, max(0, (now - max(at, month_start)) / wall))
            seconds = elapsed * portion
            prior_allocation = previous.get("allocation", {})
            ocpus = number(prior_allocation.get("ocpus"), minimum=.000001, maximum=1024) if isinstance(prior_allocation, dict) else None
            memory_gb = number(prior_allocation.get("memory_gb"), minimum=.000001, maximum=65536) if isinstance(prior_allocation, dict) else None
            if ocpus is not None and memory_gb is not None:
                month["cpu_hours"] += ocpus * seconds / 3600
                month["memory_hours"] += memory_gb * seconds / 3600
                month["allocation_seconds"] += seconds
            old_net = previous.get("net") if isinstance(previous.get("net"), dict) else {}
            network = current.get("net") or {}
            received, sent = 0, 0
            continuous = bool(network) and network.keys() == old_net.keys()
            for interface, counters in network.items():
                prior = old_net.get(interface)
                if not isinstance(prior, dict):
                    continuous = False
                    continue
                rx, tx = number(prior.get("rx")), number(prior.get("tx"))
                if rx is None or tx is None or counters["rx"] < rx or counters["tx"] < tx:
                    continuous = False
                    continue
                received += counters["rx"] - rx
                sent += counters["tx"] - tx
            month["outbound"] += sent * portion
            if continuous:
                rx_rate, tx_rate = received / elapsed, sent / elapsed
            else:
                month["gaps"] = True
            old_cpu, cpu = previous.get("cpu_ticks"), current.get("cpu_ticks")
            if isinstance(old_cpu, dict) and isinstance(cpu, dict):
                total = cpu["total"] - (number(old_cpu.get("total")) or 0)
                idle = cpu["idle"] - (number(old_cpu.get("idle")) or 0)
                if total > 0 and 0 <= idle <= total:
                    cpu_percent = 100 * (1 - idle / total)
        elif previous:
            month["gaps"] = True
        sizes = archive_sizes(self.data_root, [row["id"] for row in rows])
        snapshot = {"version": 1, "updated": now, "cpu_percent": cpu_percent,
                    "cpus": current.get("cpus"), "uptime": uptime, "memory": current.get("memory"),
                    "swap": current.get("swap"), "data_disk": current.get("data_disk"),
                    "root_disk": current.get("root_disk"), "same_disk": current.get("same_disk"), "rx_rate": rx_rate, "tx_rate": tx_rate,
                    "network_available": bool(current.get("net")), "archives": sizes, "month": month}
        new_state = {"version": 1, "month": month, "last": {
            "at": now, "uptime": uptime, "boot_id": current.get("boot_id"), "net": current.get("net"),
            "cpu_ticks": current.get("cpu_ticks"), "allocation": {"ocpus": config["ocpus"], "memory_gb": config["memory_gb"]}}}
        write_json(self.path, new_state, 0o600)
        self.state = new_state
        write_json(self.status_root / "usage.json", snapshot, 0o644)
