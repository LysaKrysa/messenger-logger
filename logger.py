"""Incoming Messenger and Instagram logger using the pinned native transport."""
from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import queue
import re
import signal
import stat
import sys
import threading
import time
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bridge_rpc import Bridge, BridgeBusy, BridgeFailure, safe_reason, verify_binary
from event_store import EventStore, StoreFailure
from install_bridge import smoke_test
from media_saver import MediaSaver
from telegram_relay import TelegramRelay, TelegramConfigurationError
from contact_names import ContactNames
from retention import RETENTION_SECONDS, CLEANUP_INTERVAL, cleanup_data, cleanup_status

SCRIPT_STARTED_AT = time.time()
PLATFORM_NAMES = {"facebook": "Messenger", "instagram": "Instagram"}
SESSION_ENV = {"facebook": "FB_SESSION_JSON", "instagram": "IG_SESSION_JSON"}
SESSION_ID = {"facebook": "c_user", "instagram": "ds_user_id"}
COOKIE_NAMES = {
    "facebook": (("c_user", "xs", "datr"), {"fr", "sb", "wd", "presence", "ps_l", "ps_n"}),
    "instagram": (("ds_user_id", "sessionid", "csrftoken"), {"rur", "mid", "ig_did", "shbid", "shbts"}),
}

class ConfigurationError(ValueError):
    pass


def load_runtime_credentials(platform="facebook"):
    """Optional dashboard secrets mounted from host /run, outside Docker metadata."""
    path = os.environ.get("LOGGER_CREDENTIALS_FILE")
    if not path:
        return
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError
            raw = handle.read(150001)
        if len(raw) > 150000:
            raise ValueError
        values = json.loads(raw)
        if not isinstance(values, dict) or set(values) != {SESSION_ENV[platform], "TELEGRAM_ACCOUNT_KEY"} or not all(isinstance(v, str) and "\n" not in v and "\r" not in v for v in values.values()):
            raise ValueError
        for name, value in values.items():
            os.environ[name] = value
    except (OSError, ValueError, TypeError):
        raise ConfigurationError("The mounted account credentials could not be read.") from None


def boolean(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    if value.lower() in {"true", "1", "yes"}:
        return True
    if value.lower() in {"false", "0", "no"}:
        return False
    raise ConfigurationError(name + " must be true or false.")


def parse_cookies(raw, *, platform="facebook"):
    if platform not in COOKIE_NAMES:
        raise ConfigurationError("Choose Messenger or Instagram.")
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        raise ConfigurationError(SESSION_ENV[platform] + " must contain valid JSON.") from None
    if isinstance(value, list):
        cookies = {}
        host = platform + ".com"
        for entry in value:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not isinstance(entry.get("value"), str):
                raise ConfigurationError("Cookie array entries require string name/value fields.")
            domain = entry.get("domain", host)
            if isinstance(domain, str) and (domain.lstrip(".") == host or domain.endswith("." + host)):
                cookies[entry["name"]] = entry["value"]
        value = cookies
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str)
        and "\r" not in key + item and "\n" not in key + item
        for key, item in value.items()
    ):
        raise ConfigurationError("Cookies must be a name/value object or browser-export array.")
    required, optional = COOKIE_NAMES[platform]
    missing = [name for name in required if not value.get(name)]
    if missing:
        raise ConfigurationError("Required cookie names missing: " + ", ".join(missing) + ".")
    identity = SESSION_ID[platform]
    if not re.fullmatch(r"[0-9]{1,20}", value[identity]):
        raise ConfigurationError("The " + identity + " cookie must contain a numeric account ID.")
    allowed = set(required) | optional
    parsed = {key: item for key, item in value.items() if key in allowed}
    if platform == "instagram" and (
            not 0 < int(parsed[identity]) < 2**64
            or any(not re.fullmatch(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*", item) for item in parsed.values())):
        raise ConfigurationError("Instagram cookies contain an invalid account ID or cookie value.")
    return parsed


class LoggerRuntime:
    def __init__(self, root: Path, zone: str, stop: threading.Event, *, started_at=None, platform="facebook"):
        self.root, self.zone, self.stop = root, ZoneInfo(zone), stop
        self.started_at = time.time() if started_at is None else started_at
        self.platform = platform
        self.service = PLATFORM_NAMES[platform]
        self.run_id = datetime.fromtimestamp(self.started_at, self.zone).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        self._report_lock = threading.Lock()
        self.consumer_failure = None
        self.bridge = None
        self.store = None
        self.relay = None
        self.media = None
        self.names = None
        self.consumer = None
        self.finish_consumer = threading.Event()
        self.logged_this_run = 0
        self.messages_this_run = 0
        self.deleted_this_run = 0
        self.last_event = None

    def report(self, message):
        # All callers use fixed text plus numeric counters / allowlisted reason codes.
        with self._report_lock:
            stamp = datetime.now(self.zone).isoformat(timespec="seconds")
            print(stamp + " | " + message, flush=True)
            with (self.root / "messenger-modern-status.log").open("a", encoding="utf-8") as handle:
                handle.write(stamp + " | " + message + "\n")

    def _consume(self):
        try:
            while not self.finish_consumer.is_set() or not self.bridge.events.empty():
                try:
                    event = self.bridge.events.get(timeout=0.2)
                except queue.Empty:
                    continue
                kind = event.get("type")
                if kind == "error":
                    code = event["data"].get("code")
                    if code == 2:
                        self.report("An encrypted message was unavailable or could not be decrypted. Its plaintext was not logged.")
                    else:
                        self.report("Transport reported an error event; provider payload hidden.")
                elif kind == "disconnected":
                    self.report("Transport disconnected; monitoring connection health. A message gap may be possible.")
                elif kind == "reconnected":
                    self.report("Transport reconnected. Messages missed while disconnected are not guaranteed to replay.")
                else:
                    summary = self.store.accept(event)
                    accepted = []
                    if summary is not None:
                        following = summary.pop("following", None)
                        accepted.append((event, summary))
                        if following is not None:
                            accepted.append(({"type": "messageUnsend", "data": {}}, following))
                    for source, summary in accepted:
                        self.last_event = time.time()
                        self.logged_this_run += 1
                        if summary["type"] == "message":
                            self.messages_this_run += 1
                            if self.messages_this_run == 1:
                                self.report("First message saved by this run. Check the daily log to verify its content.")
                        elif summary["type"] == "unsend":
                            self.deleted_this_run += 1
                        self.relay.submit(summary)
                        self.media.submit(source, summary)
                        if self.names:
                            self.names.submit(summary.get("sender_id"))
                self.bridge.events.task_done()
        except Exception:
            # Never stringify database parameters, user message content, or a provider exception.
            self.consumer_failure = StoreFailure("Event storage failed. Logging has stopped; private details hidden.")
            self.stop.set()

    def connect(self, cookies, binary, encrypted):
        self.bridge = Bridge(binary, stop_event=self.stop)
        self.bridge.check_contract()
        self.report("Native bridge supports view-once media and contact lookup.")
        expected_id = cookies[SESSION_ID[self.platform]]
        config = {"cookies": cookies, "platform": self.platform, "logLevel": "none"}
        if self.platform == "facebook":
            device_dir = self.root / "messenger-modern-state" / expected_id
            device_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            config.update(e2eeMemoryOnly=False, devicePath=str(device_dir / "device.json"))
        self.bridge.call("newClient", config, timeout=30)
        self.report("Cookie configuration loaded. Connecting the current " + self.service + " transport...")
        result = self.bridge.call("connect", timeout=120)
        user = result.get("user")
        if not isinstance(user, dict) or str(user.get("id")) != expected_id:
            raise BridgeFailure("Authenticated account ID is missing or mismatched. Logging did not start.", terminal=True)
        sender_id = expected_id
        if self.platform == "instagram":
            sender_id = user.get("senderId")
            if not isinstance(sender_id, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", sender_id):
                raise BridgeFailure("Instagram sender identity is missing. Logging did not start.", terminal=True)
        self.bridge.wait_connected(timeout=120, stop_event=self.stop, service=self.service)
        self.report("Authenticated account matches the supplied session.")
        self.store = EventStore(self.root, timezone=self.zone.key, own_id=sender_id, started_at=self.started_at, run_id=self.run_id, retention_seconds=RETENTION_SECONDS)
        with self._report_lock:
            cleanup_status(self.root, time.time() - RETENTION_SECONDS, self.zone)
        self.report("Local message retention is 72 hours; cleanup runs every five minutes. Telegram copies are kept.")
        self.report("Incoming chat-content capture starts at process startup; messages sent by this account are excluded. Counters are zero for this run.")
        if isinstance(user.get("name"), str):
            self.store.accept({"type": "metadata", "data": {"contacts": [{"id": sender_id, "name": user["name"]}]}})
        if self.platform == "facebook":
            self.names = ContactNames(self.bridge, self.store, self.stop)
        self.relay = TelegramRelay(self.report, self.store, timezone=self.zone.key, names=self.names,
                                   topic_prefix="Instagram" if self.platform == "instagram" else "")
        self.store.capture_allowed = self.relay.capture_allowed
        self.media = MediaSaver(self.bridge, self.store, self.report, self.stop, on_update=self.relay.media_updated,
                                allow_download=self.relay.download_media, allow_conversation=self.relay.capture_allowed)
        if self.relay.enabled:
            self.report("Telegram forwarding enabled (mode=" + self.relay.mode + "; topics=" + str(self.relay.use_topics).lower() + ").")
        self.consumer = threading.Thread(target=self._consume, name="event-storage", daemon=True)
        self.consumer.start()
        if self.platform == "instagram":
            self.report("Instagram listener ready. Ask another person to send a fresh test DM.")
        elif encrypted:
            self.report("Connecting encrypted Messenger transport...")
            self.bridge.call("connectE2EE", timeout=180)
            self.bridge.wait_connected(encrypted=True, timeout=120, stop_event=self.stop)
            self.report("Messenger listener ready (regular and encrypted). Ask another person to send a fresh test message.")
        else:
            self.report("Messenger listener ready for unencrypted chats only; encrypted transport is disabled in configuration.")
        self.media.start()
        if self.names:
            self.names.start()
        self.relay.report_status(True, self.last_event)
        self.report("Media downloads enabled for newly captured attachments (up to " + str(self.media.max_bytes // (1024 * 1024)) + " MiB each).")

    def run(self, encrypted):
        disconnected_since = None
        last_status = time.monotonic()
        next_cleanup = time.monotonic() + CLEANUP_INTERVAL
        while not self.stop.wait(5):
            if self.consumer_failure:
                raise self.consumer_failure
            if self.bridge.failure:
                raise self.bridge.failure
            if self.media and self.media.failure:
                raise self.media.failure
            if time.monotonic() >= next_cleanup:
                try:
                    counts = self.store.cleanup_expired()
                    with self._report_lock:
                        cleanup_status(self.root, time.time() - RETENTION_SECONDS, self.zone)
                except Exception:
                    raise StoreFailure("Local retention cleanup failed. Check data-directory access and disk space; private details hidden.") from None
                self.report("Local retention cleanup completed: " + str(counts["messages"]) + " expired messages and " + str(counts["files"]) + " files removed.")
                next_cleanup = time.monotonic() + CLEANUP_INTERVAL
            try:
                health = self.bridge.call("isConnected", timeout=15)
            except BridgeBusy:
                # Native RPC handles one operation at a time; its independent
                # event reader and our storage worker still run during downloads.
                continue
            if health.get("failureCode") and health.get("permanent") is True:
                raise BridgeFailure(self.service + " transport stopped: " + safe_reason(health["failureCode"]) + ".", terminal=True)
            ready = health.get("connected") is True and (not encrypted or health.get("e2eeConnected") is True)
            self.relay.report_status(ready, self.last_event)
            self.relay.report_conversations()
            self.relay.report_stats(health, self.started_at, self.messages_this_run, self.deleted_this_run, self.logged_this_run, encrypted)
            if ready:
                if disconnected_since is not None:
                    self.report(self.service + " listener ready after reconnecting.")
                disconnected_since = None
            elif disconnected_since is None:
                disconnected_since = time.monotonic()
                self.report("Connection is not ready. Waiting up to 120 seconds for the native transport to recover.")
            elif time.monotonic() - disconnected_since > 120:
                raise BridgeFailure("Transport did not recover in 120 seconds. Refresh or diagnose the session before restarting.")
            if time.monotonic() - last_status >= 60:
                self.report(f"This run: {self.messages_this_run} messages, {self.deleted_this_run} deletions, {self.logged_this_run} chat events saved.")
                last_status = time.monotonic()
        if self.consumer_failure:
            raise self.consumer_failure
        if self.media and self.media.failure:
            raise self.media.failure

    def close(self):
        self.stop.set()
        if self.relay:
            self.relay.report_status(False, self.last_event)
        if self.media:
            self.media.stopping.set()
        if self.names:
            self.names.stopping.set()
        if self.bridge:
            self.bridge.close()
        self.finish_consumer.set()
        if self.consumer:
            self.consumer.join(timeout=15)
            if self.consumer.is_alive():
                self.report("Storage worker did not finish before shutdown; inspect the local event journal before restarting.")
                # Do not close a database still in use by the writer.
                self.store = None
        if self.media and not self.media.close():
            self.report("Media worker did not finish before shutdown; pending attachments may be incomplete.")
            self.store = None
        if self.names and not self.names.close():
            self.report("Contact worker did not finish before shutdown; local logs remain saved.")
            self.store = None
        if self.relay and not self.relay.close():
            # An in-flight request may still need to record its result.
            self.store = None
        if self.store:
            self.store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="Check binary and local RPC only; no login")
    parser.add_argument("--check-config", action="store_true", help="Validate cookie shape only; no login")
    parser.add_argument("--cleanup", action="store_true", help="Expire local captured content without Facebook or Telegram access")
    args = parser.parse_args()
    if args.cleanup:
        os.umask(0o077)
        cleanup_data(Path(os.environ.get("DATA_DIR", "/data")).resolve(), timezone=os.environ.get("LOGGER_TIMEZONE", "Europe/Warsaw"))
        return
    binary = Path(os.environ.get("BRIDGE_BIN", "/usr/local/bin/messenger-bridge"))
    if args.self_test:
        verify_binary(binary)
        smoke_test(binary)
        return
    platform = os.environ.get("LOGGER_PLATFORM", "facebook")
    if platform not in PLATFORM_NAMES:
        raise ConfigurationError("LOGGER_PLATFORM must be facebook or instagram.")
    load_runtime_credentials(platform)
    cookies = parse_cookies(os.environ.get(SESSION_ENV[platform], ""), platform=platform)
    encrypted = platform == "facebook" and boolean("LOGGER_E2EE", True)
    zone = os.environ.get("LOGGER_TIMEZONE", "Europe/Warsaw")
    try:
        ZoneInfo(zone)
    except ZoneInfoNotFoundError:
        raise ConfigurationError("LOGGER_TIMEZONE is unavailable; the image needs timezone data.") from None
    if args.check_config:
        print("Cookie configuration shape is valid. No " + ("Facebook" if platform == "facebook" else "Instagram") + " authentication was attempted.")
        return
    os.umask(0o077)
    root = Path(os.environ.get("DATA_DIR", "/data")).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = (root / ".messenger-modern.lock").open("a")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise ConfigurationError("Another modern logger already uses this data directory.") from None
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    runtime = LoggerRuntime(root, zone, stop, started_at=SCRIPT_STARTED_AT, platform=platform)
    try:
        runtime.connect(cookies, binary, encrypted)
        runtime.run(encrypted)
        runtime.report("Listener stopped by request.")
    finally:
        runtime.close()
        lock.close()


if __name__ == "__main__":
    try:
        main()
    except (ConfigurationError, TelegramConfigurationError, StoreFailure) as exc:
        print("Logger stopped: " + str(exc), flush=True)
        raise SystemExit(78)
    except BridgeFailure as exc:
        print("Logger stopped: " + str(exc), flush=True)
        raise SystemExit(78 if exc.terminal else 1)
    except Exception as exc:
        print("Logger stopped (" + type(exc).__name__ + "). Private values are hidden.", flush=True)
        raise SystemExit(1)
