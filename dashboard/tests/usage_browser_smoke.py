"""Usage UI with synthetic local counters; no Oracle/Facebook/Telegram calls."""
import copy
from pathlib import Path
import time

from playwright.sync_api import expect

from dashboard.usage import DEFAULTS, GIB, UsageCollector, parse_config


def seed(store, data_root, status_root):
    data_root = Path(data_root)
    data_root.mkdir(exist_ok=True)
    for row in store.accounts():
        folder = data_root / row["id"] / "data"
        folder.mkdir(parents=True)
        (folder / "synthetic-size-only").write_bytes(b"OFFLINE_SIZE_FIXTURE" * 1024)
    now = time.time()
    fields = {key: "" if value is None else str(value) for key, value in DEFAULTS.items()}
    fields.update(ocpus="1", memory_gb="6", storage_gb="50", backups="1")
    store.set_setting("usage_config", parse_config(fields, now))
    values = {"cpus": 1, "cpu_ticks": {"total": 10000, "idle": 8000}, "uptime": 100000,
              "boot_id": "aaa-bbb", "net": {"eth0": {"rx": 1000000, "tx": 2000000}},
              "memory": {"total": 6 * GIB, "used": int(.8 * GIB), "available": int(5.2 * GIB)},
              "swap": {"total": 0, "used": 0}, "same_disk": True,
              "data_disk": {"total": 50 * GIB, "used": int(6.4 * GIB), "free": int(43.6 * GIB)},
              "root_disk": {"total": 50 * GIB, "used": int(6.4 * GIB), "free": int(43.6 * GIB)}}
    class Fixture:
        def sample(self):
            return copy.deepcopy(values)
    collector = UsageCollector(store, data_root, status_root, clock=lambda: now - 120, resources=Fixture())
    collector.update(store.accounts())
    collector.clock = lambda: now
    values["uptime"] += 120
    values["cpu_ticks"] = {"total": 20000, "idle": 17000}
    values["net"]["eth0"]["tx"] += int(.6 * GIB)
    values["net"]["eth0"]["rx"] += int(1.2 * GIB)
    collector.update(store.accounts())


def verify(page, origin, store, output):
    page.set_viewport_size({"width": 1440, "height": 1050})
    page.goto(origin + "/usage")
    expect(page.get_by_role("heading", name="Usage", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="Usage", exact=True)).to_have_attribute("aria-current", "page")
    expect(page.get_by_text("Monitoring active", exact=True)).to_be_visible()
    assert "not an Oracle bill" in page.content()
    assert "Not available" in page.locator(".usage-cloud-table").inner_text()
    page.screenshot(path=str(output / "usage-desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("#usage-settings summary").click()
    page.get_by_label("Allocated OCPUs", exact=False).fill("1")
    page.get_by_label("Allocated memory (GB)", exact=False).fill("6")
    page.get_by_label("Object API requests this month", exact=False).fill("123")
    page.get_by_role("button", name="Save usage settings", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Usage settings saved")
    assert store.setting("usage_config")["object_requests"] == 123
    expect(page.locator(".usage-cloud-table")).to_contain_text("123 requests")
    page.locator("#usage-settings summary").click()
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), "Expanded usage settings overflow"
    targets = page.locator("#usage-settings input:not([type=hidden]), #usage-settings button")
    for index in range(targets.count()):
        box = targets.nth(index).bounding_box()
        assert box["height"] >= 44 and box["width"] >= 44, "Usage configuration control too small"
    page.screenshot(path=str(output / "usage-mobile-settings.png"), full_page=True)
    page.locator("#usage-settings summary").click()
