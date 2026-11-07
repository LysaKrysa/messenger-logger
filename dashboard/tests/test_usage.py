"""Synthetic monitoring, month accounting and administrator-only usage settings."""
import contextlib
import copy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from cryptography.fernet import Fernet

from dashboard.store import Store, DashboardError
from dashboard.usage import (DEFAULTS, GIB, LinuxResources, UsageCollector,
                             archive_sizes, load_config, month_window, parse_config)
from dashboard.usage_view import amount, build_view
from dashboard.web import create_app

NOW = datetime(2026, 11, 7, tzinfo=timezone.utc).timestamp()
ORIGIN = "https://usage.example.test"
IDENT = "a" * 32


def form_values(**extra):
    result = {key: "" if value is None else str(value) for key, value in DEFAULTS.items()}
    result.update(extra)
    return result


class Resources:
    def __init__(self):
        self.value = {"cpus": 2, "cpu_ticks": {"total": 1000, "idle": 500},
                      "memory": {"total": 6 * GIB, "used": GIB, "available": 5 * GIB},
                      "swap": {"total": 0, "used": 0}, "uptime": 1000, "boot_id": "aaa",
                      "data_disk": {"total": 50 * GIB, "used": 8 * GIB, "free": 42 * GIB},
                      "root_disk": {"total": 50 * GIB, "used": 8 * GIB, "free": 42 * GIB},
                      "same_disk": True, "net": {"eth0": {"rx": 1000, "tx": 2000}}}

    def sample(self):
        return copy.deepcopy(self.value)

    def advance(self, seconds, *, received=10000, sent=30000):
        self.value["uptime"] += seconds
        self.value["cpu_ticks"]["total"] += 100
        self.value["cpu_ticks"]["idle"] += 70
        self.value["net"]["eth0"]["rx"] += received
        self.value["net"]["eth0"]["tx"] += sent


class UsageBase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.now = NOW
        self.store = Store(self.root / "state", Fernet.generate_key(), clock=lambda: self.now)
        self.data = self.root / "accounts"
        self.status = self.root / "status"
        self.data.mkdir()
        self.status.mkdir()
        self.resources = Resources()
        self.collector = UsageCollector(self.store, self.data, self.status,
                                        clock=lambda: self.now, resources=self.resources)

    def tearDown(self):
        self.temporary.cleanup()

    def allocate(self, ocpus="1", memory="6"):
        self.store.set_setting("usage_config", parse_config(form_values(ocpus=ocpus, memory_gb=memory), self.now))

    def advance(self, seconds=3600, **kwargs):
        self.now += seconds
        self.resources.advance(seconds, **kwargs)
        self.collector.update([])

    def snapshot(self):
        return json.loads((self.status / "usage.json").read_text())


class UsageTests(UsageBase):
    def test_linux_counts_host_resources_once_and_excludes_guest_ticks_and_virtual_links(self):
        proc, sys = self.root / "proc", self.root / "sys"
        (proc / "net").mkdir(parents=True)
        (proc / "sys/kernel/random").mkdir(parents=True)
        (sys / "class/net/eth0/device").mkdir(parents=True)
        (sys / "class/net/docker0/device").mkdir(parents=True)
        (proc / "stat").write_text("cpu 100 2 30 300 20 0 0 5 99 99\n")
        (proc / "meminfo").write_text("MemTotal: 8388608 kB\nMemAvailable: 3145728 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
        (proc / "uptime").write_text("12345.5 0.0")
        (proc / "sys/kernel/random/boot_id").write_text("aaaa-bbbb")
        (proc / "net/dev").write_text("eth0: 123 0 0 0 0 0 0 0 456 0 0 0 0 0 0 0\nlo: 999 0 0 0 0 0 0 0 999 0 0 0 0 0 0 0\ndocker0: 999 0 0 0 0 0 0 0 999 0 0 0 0 0 0 0")
        value = LinuxResources(self.data, proc=proc, sys=sys, root=self.root, cpus=lambda: 2).sample()
        self.assertEqual(value["cpu_ticks"], {"total": 457, "idle": 320})
        self.assertEqual(value["memory"]["used"], 5 * GIB)
        self.assertEqual(value["swap"]["total"], 0)
        self.assertEqual(value["net"], {"eth0": {"rx": 123, "tx": 456}})
        self.assertEqual(value["uptime"], 12345.5)

    def test_default_route_fallback_and_missing_memory_are_explicit(self):
        proc = self.root / "proc"
        (proc / "net").mkdir(parents=True)
        (proc / "net/route").write_text("Iface Destination Gateway Flags\nens3 00000000 00000000 0003\ndocker0 00000000 00000000 0003\n")
        (proc / "net/dev").write_text("ens3: 123 0 0 0 0 0 0 0 456\ndocker0: 999 0 0 0 0 0 0 0 999")
        (proc / "meminfo").write_text("MemTotal: 1024 kB\n")
        value = LinuxResources(self.data, proc=proc, sys=self.root / "missing").sample()
        self.assertEqual(value["net"], {"ens3": {"rx": 123, "tx": 456}})
        self.assertIsNone(value["memory"])
        self.assertIsNone(value["uptime"])

    def test_counter_delta_uses_allocated_resources_not_cpu_load(self):
        self.allocate()
        self.collector.update([])
        initial = self.snapshot()
        self.assertIsNone(initial["cpu_percent"])
        self.assertEqual(initial["month"]["cpu_hours"], 0)
        self.advance(received=10_000_000, sent=30_000_000)
        value = self.snapshot()
        self.assertAlmostEqual(value["cpu_percent"], 30)
        self.assertAlmostEqual(value["month"]["cpu_hours"], 1)
        self.assertAlmostEqual(value["month"]["memory_hours"], 6)
        self.assertEqual(value["month"]["outbound"], 30_000_000)
        self.assertAlmostEqual(value["tx_rate"], 30_000_000 / 3600)

    def test_budget_changes_apply_forward_without_repricing_observed_history(self):
        self.allocate()
        self.collector.update([])
        self.advance()
        self.allocate("2", "8")
        self.advance(1800)
        self.advance(1800)
        self.assertAlmostEqual(self.snapshot()["month"]["cpu_hours"], 2.5)
        self.assertAlmostEqual(self.snapshot()["month"]["memory_hours"], 13)

    def test_restart_keeps_monthly_totals_and_private_state_permissions(self):
        self.allocate()
        self.collector.update([])
        self.advance()
        self.collector = UsageCollector(self.store, self.data, self.status, clock=lambda: self.now, resources=self.resources)
        self.advance(60, sent=12345)
        self.assertAlmostEqual(self.snapshot()["month"]["cpu_hours"], 1 + 1 / 60)
        self.assertEqual(self.snapshot()["month"]["outbound"], 30000 + 12345)
        self.assertEqual((self.status / "usage-state.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.status / "usage.json").stat().st_mode & 0o777, 0o644)

    def test_boot_or_network_counter_reset_never_adds_negative_or_previous_boot_traffic(self):
        self.allocate()
        self.collector.update([])
        self.advance()
        before = self.snapshot()["month"]["outbound"]
        self.resources.value.update(boot_id="bbb", uptime=10)
        self.resources.value["net"]["eth0"] = {"rx": 1, "tx": 2}
        self.now += 600
        self.collector.update([])
        self.assertEqual(self.snapshot()["month"]["outbound"], before)
        self.assertTrue(self.snapshot()["month"]["gaps"])
        self.advance(60, sent=77)
        self.assertEqual(self.snapshot()["month"]["outbound"], before + 77)
        self.resources.value["net"]["eth0"]["tx"] = 0
        self.now += 60
        self.resources.value["uptime"] += 60
        self.collector.update([])
        self.assertEqual(self.snapshot()["month"]["outbound"], before + 77)
        self.assertIsNone(self.snapshot()["tx_rate"])

    def test_utc_month_rollover_splits_the_sample_interval_and_discards_old_totals(self):
        self.now = datetime(2026, 11, 30, 23, 59, tzinfo=timezone.utc).timestamp()
        self.allocate()
        self.collector.update([])
        self.advance(120, sent=1200)
        value = self.snapshot()["month"]
        self.assertEqual(value["key"], "2026-12")
        self.assertAlmostEqual(value["cpu_hours"], 1 / 60)
        self.assertAlmostEqual(value["memory_hours"], .1)
        self.assertEqual(value["outbound"], 600)
        self.assertEqual(value["since"], datetime(2026, 12, 1, tzinfo=timezone.utc).timestamp())
        self.assertEqual(month_window(datetime(2026, 12, 31, tzinfo=timezone.utc).timestamp())[2],
                         datetime(2027, 1, 1, tzinfo=timezone.utc).timestamp())

    def test_sampling_is_throttled_and_corrupt_history_starts_a_new_observation(self):
        self.collector.update([])
        first = (self.status / "usage.json").read_bytes()
        self.now += 5
        self.collector.update([])
        self.assertEqual((self.status / "usage.json").read_bytes(), first)
        (self.status / "usage-state.json").write_text("broken-json")
        self.collector = UsageCollector(self.store, self.data, self.status, clock=lambda: self.now, resources=self.resources)
        self.collector.update([])
        self.assertEqual(self.snapshot()["month"]["since"], self.now)
        self.assertEqual(self.snapshot()["month"]["outbound"], 0)

    def test_clock_rollback_does_not_reduce_or_fabricate_totals(self):
        self.allocate()
        self.collector.update([])
        self.advance()
        before = self.snapshot()["month"]
        self.now -= 100
        self.collector.update([])
        self.assertEqual(self.snapshot()["month"]["cpu_hours"], before["cpu_hours"])
        self.assertEqual(self.snapshot()["month"]["outbound"], before["outbound"])
        self.assertTrue(self.snapshot()["month"]["gaps"])

    def test_clock_rollback_across_month_does_not_destroy_the_newer_ledger(self):
        self.allocate()
        self.collector.update([])
        self.advance()
        before = (self.status / "usage-state.json").read_bytes()
        self.now = datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp()
        self.collector.update([])
        self.assertEqual((self.status / "usage-state.json").read_bytes(), before)

    def test_sampling_failure_isolated_and_private_exception_is_hidden(self):
        self.resources.sample = lambda: (_ for _ in ()).throw(RuntimeError("PRIVATE_SESSION_VALUE"))
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            self.collector.update([])
        self.assertIn("account management continues", captured.getvalue())
        self.assertNotIn("PRIVATE_SESSION_VALUE", captured.getvalue())
        self.assertFalse((self.status / "usage.json").exists())

    def test_archive_scan_reads_sizes_only_skips_links_and_reports_work_limits(self):
        root = self.data / IDENT / "data"
        root.mkdir(parents=True)
        (root / "one").write_bytes(b"SYNTHETIC_PRIVATE_MESSAGE")
        outside = self.root / "outside"
        outside.write_bytes(b"DO_NOT_COUNT" * 100)
        (root / "external").symlink_to(outside)
        sizes = archive_sizes(self.data, [IDENT, "../outside"])
        self.assertEqual(sizes[IDENT], {"bytes": len(b"SYNTHETIC_PRIVATE_MESSAGE"), "complete": True})
        self.assertEqual(len(sizes), 1)
        for index in range(10):
            (root / str(index)).write_bytes(b"x")
        limited = archive_sizes(self.data, [IDENT], maximum=3)
        self.assertFalse(limited[IDENT]["complete"])
        self.collector.update([{"id": IDENT, "cookies": "DO_NOT_PUBLISH_CREDENTIALS"}])
        self.assertNotIn("SYNTHETIC_PRIVATE_MESSAGE", (self.status / "usage.json").read_text())
        self.assertNotIn("DO_NOT_PUBLISH_CREDENTIALS", (self.status / "usage.json").read_text())
        self.assertNotIn(IDENT, (self.status / "usage-state.json").read_text())

    def test_config_validation_rejects_nonfinite_negative_partial_and_fractional_counts(self):
        for extra in ({"ocpus": "nan", "memory_gb": "6"}, {"cpu_hours_limit": "inf"},
                      {"storage_gb": "-1"}, {"backups": "1.5"}, {"ocpus": "1"},
                      {"memory_gb": "99999999", "ocpus": "1"}, {"requests_limit": "0"}):
            with self.subTest(extra=extra), self.assertRaises(DashboardError):
                parse_config(form_values(**extra), self.now)
        result = parse_config(form_values(ocpus=".5", memory_gb="3", backups="0"), self.now)
        self.assertEqual(result["backups"], 0)
        self.assertEqual(result["ocpus"], .5)

    def test_monthly_manual_requests_expire_while_current_inventory_is_preserved(self):
        config = parse_config(form_values(storage_gb="50", object_requests="123"), self.now)
        next_month = datetime(2026, 12, 1, tzinfo=timezone.utc).timestamp()
        loaded = load_config(config, next_month)
        self.assertIsNone(loaded["object_requests"])
        self.assertEqual(loaded["storage_gb"], 50)

    def test_small_observations_are_not_rounded_to_zero(self):
        self.assertEqual(amount(.01, "OCPU-hours"), "<0.1 OCPU-hours")
        self.allocate()
        self.collector.update([])
        self.advance(60, sent=1024)
        view = build_view(self.store, self.status, self.now)
        self.assertEqual(view["quotas"][2]["used"], "1 KiB")
        self.assertEqual(view["quotas"][2]["ratio"], "<0.1 %")

    def test_unknown_stale_and_allocation_forecasts_are_distinct(self):
        view = build_view(self.store, self.status, self.now)
        self.assertFalse(view["available"])
        self.assertEqual(view["quotas"][0]["used"], "Not available")
        self.assertEqual(view["manual"][0]["used"], "Not available")
        self.allocate("3", "18")
        self.collector.update([])
        self.advance()
        view = build_view(self.store, self.status, self.now)
        self.assertTrue(view["quotas"][0]["forecast_over"])
        self.assertTrue(view["quotas"][1]["forecast_over"])
        self.assertFalse(view["stale"])
        view = build_view(self.store, self.status, self.now + 181)
        self.assertTrue(view["stale"])
        value = self.snapshot()
        value["cpu_percent"] = "DO_NOT_RENDER_PRIVATE_TEXT"
        value["month"]["key"] = "2025-01"
        (self.status / "usage.json").write_text(json.dumps(value))
        view = build_view(self.store, self.status, self.now)
        self.assertEqual(view["quotas"][0]["used"], "Not available")
        self.assertNotIn("DO_NOT_RENDER_PRIVATE_TEXT", str(view))


class UsageWebTests(UsageBase):
    def setUp(self):
        super().setUp()
        self.store.set_password("synthetic-usage-admin-password")
        self.app = create_app(self.store, origin=ORIGIN, status_root=self.status, testing=True)
        self.client = self.app.test_client()

    def login(self):
        self.client.get("/login", base_url=ORIGIN)
        with self.client.session_transaction(base_url=ORIGIN) as session:
            self.csrf = session["csrf"]
        self.client.post("/login", base_url=ORIGIN, data={"csrf": self.csrf, "password": "synthetic-usage-admin-password"})
        with self.client.session_transaction(base_url=ORIGIN) as session:
            self.csrf = session["csrf"]

    def test_usage_and_configuration_require_administrator_auth_and_csrf(self):
        self.assertEqual(self.client.get("/usage", base_url=ORIGIN).status_code, 302)
        self.login()
        response = self.client.get("/usage", base_url=ORIGIN)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Waiting for the first usage sample", response.text)
        self.assertEqual(self.client.post("/usage", base_url=ORIGIN, data=form_values()).status_code, 403)
        self.assertIsNone(self.store.setting("usage_config"))
        response = self.client.post("/usage", base_url=ORIGIN, data={**form_values(), "csrf": self.csrf},
                                    headers={"Origin": "https://another.example"})
        self.assertEqual(response.status_code, 403)

    def test_save_is_atomic_and_usage_never_displays_raw_snapshot_fields(self):
        self.login()
        valid = {**form_values(ocpus="1", memory_gb="6", storage_gb="50"), "csrf": self.csrf}
        self.assertEqual(self.client.post("/usage", base_url=ORIGIN, data=valid).status_code, 302)
        before = self.store.setting("usage_config")
        self.assertEqual(before["storage_gb"], 50)
        self.assertEqual(self.client.post("/usage", base_url=ORIGIN, data={**valid, "cpu_hours_limit": "NaN"}).status_code, 400)
        self.assertEqual(self.store.setting("usage_config"), before)
        self.collector.update([])
        self.advance()
        self.login()  # The one-hour synthetic jump expires the idle admin session.
        snapshot = self.snapshot()
        snapshot["secret"] = "PRIVATE_RAW_SNAPSHOT_TEXT"
        (self.status / "usage.json").write_text(json.dumps(snapshot))
        response = self.client.get("/usage", base_url=ORIGIN)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("PRIVATE_RAW_SNAPSHOT_TEXT", response.text)
        self.assertIn("1 OCPU-hours", response.text)
        self.assertIn("not an Oracle bill", response.text)
        self.assertIn("aria-current=\"page\"", response.text)
