"""Small, receive-only client for the upstream standalone Messenger bridge."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import queue
import re
import subprocess
import threading
import time
from typing import Callable

from install_bridge import CAPABILITIES, DIGESTS, UPSTREAM_DIGESTS, VERSION, smoke_test
from identities import display_label, username_label

SAFE_REASONS = {
    "network_disconnected", "auth_failed", "checkpoint_required", "config_invalid",
    "protocol_error", "device_state_invalid", "connection_failed", "cancelled",
}
# No message sending, mark-read, typing, deletion, or reaction methods are exposed.
MEDIA_METHODS = {"downloadMedia", "downloadE2EEMedia"}
BACKGROUND_METHODS = MEDIA_METHODS | {"getUserInfo"}
ALLOWED_METHODS = {"hello", "newClient", "connect", "connectE2EE", "isConnected", "disconnect", "getUserInfo"} | MEDIA_METHODS
CONTENT_EVENTS = {
    "message", "e2eeMessage", "messageEdit", "messageUnsend", "reaction",
    "e2eeReaction",
}
CONTROL_EVENTS = {"ready", "reconnected", "disconnected", "error", "e2eeConnected"}
# The pinned bridge caps downloads at 100 MiB; JSON carries them as base64.
MAX_LINE = 144 * 1024 * 1024

# libsignal v0.2.2's DefaultLogger prints this exact human-readable prefix to
# stdout independently of the bridge's configured log level.
SIGNAL_DIAGNOSTIC = re.compile(
    rb"^\[(INFO|WARNING|ERROR)\] "
    rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2}) "
    rb"[A-Za-z0-9_.-]+\.go:\d+ \xe2\x96\xb6  "
)
MAX_NATIVE_LOG_LINE = 16 * 1024


class NativeDiagnostics:
    """Count recognized pinned-library log categories; never retain log text."""
    def __init__(self):
        self.lock = threading.Lock()
        self.counts = dict.fromkeys(("stderr_lines", "oversized_logs", "unavailable", "view_once_unavailable", "missing_session",
                                    "missing_sender_key", "missing_prekey", "identity_changed", "bad_mac", "other_decrypt"), 0)

    def record(self, raw, *, signal=False):
        value = raw.lower()
        category = None
        if b"unavailable message " in value:
            category = "view_once_unavailable" if b'(type: "view_once")' in value else "unavailable"
        elif b"error decrypting message " in value or signal:
            for name, markers in (
                ("missing_sender_key", (b"no sender key",)),
                ("missing_prekey", (b"no signed prekey", b"prekey store didn't return one-time key")),
                ("identity_changed", (b"untrusted identity", b"invalid signature on device key")),
                ("bad_mac", (b"mismatching mac in signal message",)),
                ("missing_session", (b"no session found for user", b"no valid sessions", b"uninitialized session")),
            ):
                if any(marker in value for marker in markers):
                    category = name
                    break
            if category is None and b"error decrypting message " in value:
                category = "other_decrypt"
        with self.lock:
            if not signal:
                self.counts["stderr_lines"] += 1
            if category:
                self.counts[category] += 1

    def oversized(self):
        with self.lock:
            self.counts["oversized_logs"] += 1

    def snapshot(self):
        with self.lock:
            return dict(self.counts)


class BridgeFailure(RuntimeError):
    """Only static messages and structured reason codes may reach the console."""
    def __init__(self, message, *, terminal=False):
        super().__init__(message)
        self.terminal = terminal


class BridgeBusy(BridgeFailure):
    """A download occupies the native bridge's sequential RPC handler."""


class ReaderShapeError(ValueError):
    """Static shape labels, never payload values."""
    def __init__(self, category):
        self.category = category
        super().__init__(category)


def safe_reason(value) -> str:
    return value if isinstance(value, str) and value in SAFE_REASONS else "connection_failed"


def verify_binary(binary: Path) -> None:
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower())
    if arch not in DIGESTS or not binary.is_file():
        raise BridgeFailure("Pinned Messenger bridge binary is unavailable.", terminal=True)
    digest = hashlib.sha256()
    with binary.open("rb") as handle:
        while chunk := handle.read(65536):
            digest.update(chunk)
    if digest.hexdigest() != DIGESTS[arch]:
        if digest.hexdigest() in UPSTREAM_DIGESTS.values():
            raise BridgeFailure("This update requires the rebuilt native bridge. Rebuild the Docker image before starting.", terminal=True)
        raise BridgeFailure("Messenger bridge binary failed its SHA-256 check.", terminal=True)


def prepare_event(event: dict) -> dict | None:
    """Discard key-state events and raw bodies before handing events to storage."""
    kind = event.get("type")
    data = event.get("data")
    if not isinstance(kind, str):
        raise ReaderShapeError("event_type_not_string")
    if kind == "metadata":
        if not isinstance(data, dict):
            raise ReaderShapeError("metadata_data_not_object")
        safe = {"contacts": [], "threads": []}
        for group in safe:
            rows = data.get(group, [])
            if not isinstance(rows, list):
                raise ReaderShapeError("metadata_rows_not_array")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                uid = row.get("id")
                if not isinstance(uid, (str, int)) or isinstance(uid, bool):
                    continue
                item = {"id": uid, "name": display_label(row.get("name")) or display_label(row.get("firstName"))}
                if group == "contacts":
                    item["username"] = username_label(row.get("username"))
                    item["priority"] = 2 if display_label(row.get("name")) else 1
                else:
                    item["type"] = row.get("type") if isinstance(row.get("type"), int) else 0
                safe[group].append(item)
        return {"type": "metadata", "data": safe}
    if kind in CONTENT_EVENTS:
        if not isinstance(data, dict):
            raise ReaderShapeError("content_data_not_object")
        return {"type": kind, "data": data, "timestamp": event.get("timestamp")}
    elif kind in CONTROL_EVENTS:
        filtered = {}
        if data is not None and not isinstance(data, dict):
            raise ReaderShapeError("control_data_not_object")
        if isinstance(data, dict):
            for key in ("failureCode", "retryable", "permanent", "transport", "code"):
                if key in data:
                    filtered[key] = data[key]
        return {"type": kind, "data": filtered, "timestamp": event.get("timestamp")}
    elif kind == "raw" and isinstance(data, dict):
        raw_kind = data.get("type")
        if data.get("from") == "whatsmeow" and isinstance(raw_kind, str) and raw_kind in {"FBMessage", "Message"}:
            message = data.get("data")
            info = message.get("Info") if isinstance(message, dict) else None
            if isinstance(info, dict):
                sender = info.get("Sender")
                if isinstance(sender, dict):
                    sender = sender.get("User")
                name = display_label(info.get("PushName"))
                if isinstance(sender, (str, int)) and isinstance(name, str) and name.strip():
                    return {"type": "metadata", "data": {"contacts": [{"id": sender, "name": name.strip(), "priority": 1}], "threads": []}}
            return None
        if data.get("from") != "lightspeed" or data.get("type") != "LSTable":
            return None
        table = data.get("data")
        if not isinstance(table, dict):
            return None
        def rows(name):
            value = table.get(name)
            if value is None:
                return []
            if not isinstance(value, list):
                raise ReaderShapeError("metadata_rows_not_array")
            if any(row is not None and not isinstance(row, dict) for row in value):
                raise ReaderShapeError("metadata_row_not_object")
            return value
        contacts = []
        for key in ("LSDeleteThenInsertContact", "LSVerifyContactRowExists", "LSVerifyContactParticipantExist", "LSInsertCommunityMember"):
            for row in rows(key):
                if not isinstance(row, dict):
                    continue
                contact_id = row.get("Id") or row.get("ContactId") or row.get("ContactID")
                name = display_label(row.get("Name")) or display_label(row.get("FirstName"))
                username = username_label(row.get("Username")) or username_label(row.get("SecondaryName"))
                if contact_id and (isinstance(name, str) or isinstance(username, str)):
                    contacts.append({"id": contact_id, "name": name if isinstance(name, str) else "", "username": username if isinstance(username, str) else "", "priority": 2 if display_label(row.get("Name")) else 1})
        threads = []
        for row in rows("LSDeleteThenInsertThread"):
            if isinstance(row, dict) and row.get("ThreadKey"):
                threads.append({"id": row["ThreadKey"], "name": row.get("ThreadName", ""), "type": row.get("ThreadType", 0)})
        if contacts or threads:
            return {"type": "metadata", "data": {"contacts": contacts, "threads": threads}}
    return None


class Bridge:
    def __init__(self, binary: Path, *, verify=True, queue_size=1024, stop_event=None):
        if verify:
            verify_binary(binary)
        self.events: queue.Queue[dict] = queue.Queue(maxsize=queue_size)
        self._pending = {}
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._rpc_lock = threading.Lock()
        self._health_requested = threading.Event()
        self._closed = threading.Event()
        self._next_id = 1
        self.stop_event = stop_event
        self.failure: BridgeFailure | None = None
        self.native_diagnostics = 0
        self._diagnostic_levels = set()
        self._native_issues = NativeDiagnostics()
        child_env = {key: value for key, value in os.environ.items() if not key.startswith(("FB_", "TELEGRAM_"))}
        self.process = subprocess.Popen(
            [str(binary.resolve())], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=child_env,
        )
        self._stderr_reader = threading.Thread(target=self._read_stderr, name="bridge-diagnostics", daemon=True)
        self._stderr_reader.start()
        self._reader = threading.Thread(target=self._read, name="bridge-reader", daemon=True)
        self._reader.start()

    def native_snapshot(self):
        return self._native_issues.snapshot()

    def _read_stderr(self):
        # Drain independently of JSON RPC. Only fixed counters leave this method;
        # discard oversized lines in full so no fragment becomes another record.
        dropping = False
        try:
            while True:
                raw = self.process.stderr.readline(MAX_NATIVE_LOG_LINE + 1)
                if not raw:
                    return
                if dropping:
                    if raw.endswith(b"\n"):
                        dropping = False
                    raw = b""
                    continue
                if len(raw) > MAX_NATIVE_LOG_LINE or not raw.endswith(b"\n"):
                    self._native_issues.oversized()
                    dropping = not raw.endswith(b"\n")
                    raw = b""
                    continue
                self._native_issues.record(raw)
                raw = b""
        except (OSError, ValueError):
            if not self._closed.is_set():
                self._fail("Messenger bridge diagnostic pipe failed; private details hidden.")

    def _fail(self, message: str, *, terminal=False) -> None:
        if self.failure is None and not self._closed.is_set():
            self.failure = BridgeFailure(message, terminal=terminal)
        with self._pending_lock:
            for waiter in self._pending.values():
                waiter["ready"].set()

    def _read(self) -> None:
        stage = "read"
        raw = b""
        event_kind = "none"
        record_number = 0
        record_format = "unknown"
        try:
            while not self._closed.is_set():
                stage = "read"
                event_kind = "none"
                record_format = "unknown"
                raw = self.process.stdout.readline(MAX_LINE + 1)
                if not raw:
                    self._fail("Messenger bridge exited; the listener is stopped.")
                    return
                if len(raw) > MAX_LINE or not raw.endswith(b"\n"):
                    self._fail("Messenger bridge output exceeded the size limit.", terminal=True)
                    return
                record_number += 1
                stripped = raw.strip()
                if not stripped:
                    # Blank separators contain no RPC response or event to lose.
                    continue
                diagnostic = SIGNAL_DIAGNOSTIC.match(raw)
                if diagnostic is not None:
                    level = diagnostic.group(1).decode("ascii")
                    self.native_diagnostics += 1
                    self._native_issues.record(raw[:MAX_NATIVE_LOG_LINE], signal=True)
                    if level not in self._diagnostic_levels:
                        self._diagnostic_levels.add(level)
                        print("Native Signal diagnostic suppressed (level=" + level + "); provider text hidden. Listener continuing.", flush=True)
                    raw = b""
                    continue
                record_format = "json_object" if stripped.startswith(b"{") else ("json_array" if stripped.startswith(b"[") else "non_json_prefix")
                stage = "json_decode"
                payload = json.loads(raw)
                stage = "envelope"
                if not isinstance(payload, dict):
                    raise ReaderShapeError("envelope_not_object")
                if "id" in payload:
                    stage = "response"
                    request_id = payload["id"]
                    if not isinstance(request_id, int) or isinstance(request_id, bool):
                        raise ReaderShapeError("response_id_not_integer")
                    with self._pending_lock:
                        pending = self._pending.get(request_id)
                        if pending is not None:
                            pending["response"] = payload
                            pending["ready"].set()
                elif "event" in payload:
                    stage = "event_envelope"
                    incoming = payload["event"]
                    if not isinstance(incoming, dict):
                        raise ReaderShapeError("event_not_object")
                    kind = incoming.get("type")
                    known = CONTENT_EVENTS | CONTROL_EVENTS | {"raw", "metadata", "deviceDataChanged"}
                    event_kind = kind if isinstance(kind, str) and kind in known else "other"
                    stage = "event_filter"
                    event = prepare_event(incoming)
                    if event is not None:
                        stage = "queue"
                        try:
                            self.events.put(event, timeout=2)
                        except queue.Full:
                            self._fail("Event queue overflowed; stopping because a logging gap is possible.", terminal=True)
                            return
                else:
                    raise ReaderShapeError("envelope_missing_response_or_event")
        except Exception as exc:
            if isinstance(exc, ReaderShapeError):
                category = exc.category
            elif isinstance(exc, json.JSONDecodeError):
                category = "json_syntax"
            elif isinstance(exc, UnicodeDecodeError):
                category = "invalid_encoding"
            elif isinstance(exc, RecursionError):
                category = "nesting_limit"
            elif isinstance(exc, OSError):
                category = "pipe_io"
            else:
                category = "unexpected_exception"
            exception_type = type(exc).__name__
            json_location = f"; json_line={exc.lineno}; json_column={exc.colno}" if isinstance(exc, json.JSONDecodeError) else ""
            self._fail(
                "Messenger bridge output could not be processed "
                f"(stage={stage}; category={category}; exception={exception_type}; "
                f"event={event_kind}; bytes={len(raw)}; record={record_number}; "
                f"format={record_format}{json_location}). Payload and exception text hidden.",
                terminal=not isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError, OSError))
            )

    def call(self, method: str, params=None, *, timeout=120) -> dict:
        if method not in ALLOWED_METHODS:
            raise BridgeFailure("This logger does not expose message-changing RPC methods.")
        # Keep the reader independent: events continue while media is fetched.
        # Health checks must not queue behind a long download and time out.
        if method == "isConnected":
            self._health_requested.set()
            if not self._rpc_lock.acquire(blocking=False):
                raise BridgeBusy("Messenger bridge is processing another request.")
            self._health_requested.clear()
        else:
            deadline = time.monotonic() + timeout
            while True:
                # Give the next scheduled health check priority between files.
                if method in BACKGROUND_METHODS and self._health_requested.is_set():
                    self._closed.wait(0.1)
                elif self._rpc_lock.acquire(timeout=0.1):
                    break
                if self._closed.is_set() or (method != "disconnect" and self.stop_event is not None and self.stop_event.is_set()):
                    raise BridgeFailure("Listener shutdown requested.")
                if time.monotonic() >= deadline:
                    raise BridgeFailure("Messenger bridge request queue timed out.")
        try:
            return self._call(method, params, timeout=timeout)
        finally:
            self._rpc_lock.release()

    def _call(self, method: str, params=None, *, timeout=120) -> dict:
        if method != "disconnect" and self.stop_event is not None and self.stop_event.is_set():
            raise BridgeFailure("Listener shutdown requested.")
        if self.failure is not None:
            raise self.failure
        if self._closed.is_set():
            raise BridgeFailure("Messenger bridge is closed.")
        with self._pending_lock:
            request_id = self._next_id
            self._next_id += 1
            pending = {"ready": threading.Event(), "response": None}
            self._pending[request_id] = pending
        try:
            raw = json.dumps({"id": request_id, "method": method, "params": params or {}}, separators=(",", ":")).encode() + b"\n"
            with self._write_lock:
                self.process.stdin.write(raw)
                self.process.stdin.flush()
            deadline = time.monotonic() + timeout
            while not pending["ready"].wait(min(0.25, max(0, deadline - time.monotonic()))):
                if method != "disconnect" and self.stop_event is not None and self.stop_event.is_set():
                    raise BridgeFailure("Listener shutdown requested.")
                if time.monotonic() >= deadline:
                    if method in BACKGROUND_METHODS:
                        self._fail("Messenger bridge did not finish a background request within its deadline.")
                    raise BridgeFailure(f"Messenger bridge timed out during {method}.")
            response = pending["response"]
            if response is None:
                raise self.failure or BridgeFailure("Messenger bridge stopped during a request.")
            if response.get("ok") is not True:
                reason = safe_reason(response.get("errorCode"))
                terminal = reason in {"auth_failed", "checkpoint_required", "config_invalid", "protocol_error", "device_state_invalid"}
                raise BridgeFailure(f"Messenger bridge rejected {method}: {reason}. Provider text hidden.", terminal=terminal)
            data = response.get("data", {})
            if not isinstance(data, dict):
                raise BridgeFailure("Messenger bridge returned an unexpected response shape.", terminal=True)
            return data
        except (BrokenPipeError, OSError):
            raise BridgeFailure("Messenger bridge pipe closed during a request.") from None
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def check_contract(self) -> None:
        info = self.call("hello", timeout=15)
        if info.get("bridgeVersion") != VERSION or info.get("protocolVersion") != 1 or not CAPABILITIES.issubset(set(info.get("capabilities", []))):
            raise BridgeFailure("Messenger bridge version or RPC capabilities do not match this logger.", terminal=True)

    def wait_connected(self, *, encrypted=False, timeout=120, stop_event=None, service="Messenger") -> dict:
        deadline = time.monotonic() + timeout
        next_report = 0.0
        last = {}
        phase = "encrypted" if encrypted else "regular"
        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                raise BridgeFailure("Listener shutdown requested.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            last = self.call("isConnected", timeout=min(15, remaining))
            code = last.get("failureCode")
            if code:
                # The native bridge uses this state while connecting as well as
                # during a recoverable disconnect. Do not respawn or re-login.
                recovering = (
                    code == "network_disconnected"
                    and last.get("retryable") is True
                    and last.get("permanent") is False
                    and last.get("nativeReconnecting") is True
                )
                if not recovering:
                    flags = (
                        f"retryable={last.get('retryable') is True}, "
                        f"permanent={last.get('permanent') is True}, "
                        f"native_reconnecting={last.get('nativeReconnecting') is True}"
                    )
                    terminal = last.get("permanent") is True or code in {"auth_failed", "checkpoint_required", "config_invalid", "protocol_error", "device_state_invalid"}
                    raise BridgeFailure(service + " connection failed: " + safe_reason(code) + " (" + flags + ").", terminal=terminal)
            elif last.get("connected") is True and (not encrypted or last.get("e2eeConnected") is True):
                return last
            now = time.monotonic()
            remaining = max(0.0, deadline - now)
            if now >= next_report:
                print(
                    f"Waiting for {phase} {service} connection: "
                    f"native_reconnecting={last.get('nativeReconnecting') is True}, "
                    f"retryable={last.get('retryable') is True}; "
                    f"startup deadline in {int(remaining)} seconds.",
                    flush=True,
                )
                next_report = now + 30
            if remaining <= 0:
                break
            if stop_event is not None:
                stop_event.wait(min(0.5, remaining))
            else:
                time.sleep(min(0.5, remaining))
        reason = safe_reason(last["failureCode"]) if last.get("failureCode") else "not_ready"
        raise BridgeFailure(
            f"{service} {phase} connection did not become ready within the startup deadline "
            f"(last status: {reason}; native_reconnecting={last.get('nativeReconnecting') is True})."
        )

    def close(self) -> None:
        if self._closed.is_set():
            return
        if self.process.poll() is None and self.failure is None:
            try:
                self.call("disconnect", timeout=5)
            except BridgeFailure:
                pass
        self._closed.set()
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self._reader.join(timeout=3)
        self._stderr_reader.join(timeout=3)
        self.process.stdout.close()
        self.process.stderr.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", type=Path, required=True)
    args = parser.parse_args()
    verify_binary(args.self_test)
    smoke_test(args.self_test)
