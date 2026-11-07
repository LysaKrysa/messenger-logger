"""Safe presentation of local estimates and explicitly entered cloud totals."""
from datetime import datetime, timezone

from .usage import DEFAULTS, GB, GIB, TB, SOURCE, VERIFIED, load_config, month_window, number, read_json


def amount(value, unit="", digits=1):
    value = number(value)
    if value is None:
        return "Not available"
    if 0 < value < 10 ** -digits:
        return "<" + f"{10 ** -digits:.{digits}f}" + (" " + unit if unit else "")
    text = f"{value:,.{digits}f}".rstrip("0").rstrip(".") if digits else f"{value:,.0f}"
    return text + (" " + unit if unit else "")


def binary(value):
    value = number(value)
    if value is None:
        return "Not available"
    for scale, unit in ((1024**4, "TiB"), (GIB, "GiB"), (1024**2, "MiB"), (1024, "KiB")):
        if value >= scale:
            return amount(value / scale, unit)
    return amount(value, "B", 0)


def timestamp(value):
    value = number(value, maximum=253402300799)
    if value is None:
        return "Not available"
    return datetime.fromtimestamp(value, timezone.utc).strftime("%d %b %Y, %H:%M UTC")


def percent(used, total):
    used, total = number(used), number(total, minimum=.000001)
    return used / total * 100 if used is not None and total is not None else None


def level(value):
    return "danger" if value is not None and value >= 100 else "warning" if value is not None and value >= 85 else ""


def quota(label, used, limit, unit, note, *, forecast=None):
    ratio = percent(used, limit)
    return {"label": label, "used": amount(used, unit), "limit": amount(limit, unit),
            "value": min(100, ratio) if ratio is not None else None,
            "ratio": amount(ratio, "%"), "level": level(ratio), "note": note,
            "forecast": amount(forecast, unit) if forecast is not None else None,
            "forecast_over": forecast is not None and forecast > limit}


def resource(label, value, note, used=None, total=None):
    ratio = percent(used, total)
    return {"label": label, "value": value, "note": note,
            "meter": min(100, ratio) if ratio is not None else None, "level": level(ratio)}


def build_view(store, status_root, now):
    config = load_config(store.setting("usage_config"), now)
    snapshot = read_json(status_root / "usage.json")
    updated = number(snapshot.get("updated"), maximum=now + 60)
    available = snapshot.get("version") == 1 and updated is not None
    stale = not available or now - updated > 180
    # Treat an absent/malformed sampler as unknown, rather than an empty bill.
    snapshot = snapshot if available else {}
    month_key, month_start, month_end = month_window(now)
    month = snapshot.get("month") if isinstance(snapshot.get("month"), dict) else {}
    if month.get("key") != month_key:
        month = {}
    cpu = number(snapshot.get("cpu_percent"), maximum=100)
    cpus = number(snapshot.get("cpus"), minimum=1, maximum=65536)
    memory = snapshot.get("memory") if isinstance(snapshot.get("memory"), dict) else {}
    swap = snapshot.get("swap") if isinstance(snapshot.get("swap"), dict) else {}
    disk = snapshot.get("data_disk") if isinstance(snapshot.get("data_disk"), dict) else {}
    memory_used, memory_total = number(memory.get("used")), number(memory.get("total"), minimum=1)
    disk_used, disk_total = number(disk.get("used")), number(disk.get("total"), minimum=1)
    swap_used, swap_total = number(swap.get("used")), number(swap.get("total"))
    resources = [
        resource("CPU utilization", amount(cpu, "%") if cpu is not None else "Sampling" if available else "Not available",
                 (amount(cpus, "logical CPUs", 0) + " · last sample average") if cpus is not None else "Waiting for the manager", cpu, 100),
        resource("Memory", binary(memory_used), "of " + binary(memory_total) + " · includes all server processes",
                 memory_used, memory_total),
        resource("Disk space used", binary(disk_used), "of " + binary(disk_total) + " on the data filesystem",
                 disk_used, disk_total),
        resource("Swap", "Not configured" if swap_total == 0 else binary(swap_used),
                 "Additional disk-backed memory" if swap_total == 0 else "of " + binary(swap_total),
                 swap_used, swap_total),
    ]
    root_disk = snapshot.get("root_disk") if isinstance(snapshot.get("root_disk"), dict) else {}
    if snapshot.get("same_disk") is False:
        root_used, root_total = number(root_disk.get("used")), number(root_disk.get("total"), minimum=1)
        resources.append(resource("Root disk space used", binary(root_used), "of " + binary(root_total) + " on the root filesystem", root_used, root_total))
    allocated = config["ocpus"] is not None and config["memory_gb"] is not None
    month_hours = (month_end - month_start) / 3600
    observed = number(month.get("allocation_seconds"))
    has_compute = observed is not None and observed > 0
    since = number(month.get("since"), minimum=month_start, maximum=now)
    quota_rows = [
        quota("A1 compute", number(month.get("cpu_hours")) if has_compute else None, config["cpu_hours_limit"], "OCPU-hours",
              "Allocated OCPUs × observed server uptime, independent of CPU load.",
              forecast=config["ocpus"] * month_hours if allocated else None),
        quota("A1 allocated memory", number(month.get("memory_hours")) if has_compute else None, config["memory_hours_limit"], "GB-hours",
              "Allocated memory × observed server uptime; not RAM currently in use.",
              forecast=config["memory_gb"] * month_hours if allocated else None),
        quota("Observed outbound traffic", number(month.get("outbound")) / TB if month and snapshot.get("network_available") is True and number(month.get("outbound")) is not None else None,
              config["transfer_tb_limit"], "TB",
              "This server’s external-interface traffic. Includes traffic OCI may not bill; excludes other resources."),
    ]
    if month and snapshot.get("network_available") is True:
        quota_rows[2]["used"] = binary(month.get("outbound"))
    manual_rows = [
        quota("Provisioned boot + block storage", config["storage_gb"], config["storage_gb_limit"], "GB",
              "Enter the allocated capacity across your tenancy, not files currently stored."),
        quota("Volume backups", config["backups"], config["backups_limit"], "backups", "Combined boot-volume and block-volume backups."),
        quota("Object + archive storage", config["object_gb"], config["object_gb_limit"], "GB",
              "20 GB combined is the Always Free-only reference. Trial/paid account tier allowances differ."),
        quota("Object Storage API requests", config["object_requests"], config["requests_limit"], "requests",
              "Current UTC month only; an entered request total is cleared from view next month."),
    ]
    sizes = snapshot.get("archives") if isinstance(snapshot.get("archives"), dict) else {}
    accounts = []
    for row in store.accounts():
        data = sizes.get(row["id"]) if isinstance(sizes.get(row["id"]), dict) else {}
        size = number(data.get("bytes"))
        accounts.append({"name": row["name"], "id": row["id"], "bytes": binary(size),
                         "partial": data.get("complete") is not True})
    uptime = number(snapshot.get("uptime"))
    return {"config": config, "available": available, "stale": stale, "updated": timestamp(updated),
            "resources": resources, "quotas": quota_rows, "manual": manual_rows,
            "allocated": allocated, "since": timestamp(since),
            "month": datetime.fromtimestamp(month_start, timezone.utc).strftime("%B %Y"),
            "gaps": month.get("gaps") is True, "accounts": accounts,
            "rx": binary(snapshot.get("rx_rate")) + "/s" if number(snapshot.get("rx_rate")) is not None else "Sampling" if available and snapshot.get("network_available") else "Not available",
            "tx": binary(snapshot.get("tx_rate")) + "/s" if number(snapshot.get("tx_rate")) is not None else "Sampling" if available and snapshot.get("network_available") else "Not available",
            "uptime": amount(uptime / 86400, "days") if uptime is not None else "Not available",
            "manual_at": timestamp(config.get("saved_at")), "source": SOURCE, "verified": VERIFIED,
            "disk_free": binary(disk.get("free")), "defaults": DEFAULTS}
