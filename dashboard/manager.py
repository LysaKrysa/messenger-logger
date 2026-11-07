"""Root-only reconciler. The web and bot services have no Docker access."""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import threading
import time

from .store import Store, DashboardError, account_id, from_environment
from .usage import UsageCollector

LABEL = "messenger.dashboard"


class EngineError(RuntimeError):
    pass


class Docker:
    def __init__(self, binary="/usr/bin/docker"):
        self.binary = binary

    def call(self, *args, missing=False, timeout=60):
        result = subprocess.run([self.binary, *args], capture_output=True, text=True, timeout=timeout,
                                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "DOCKER_CONFIG": "/etc/messenger-dashboard/docker-client"})
        if result.returncode:
            if missing:
                return None
            raise EngineError("Container operation failed; inspect the manager journal on the server.")
        return result.stdout.strip()

    def inspect(self, name):
        raw = self.call("inspect", name, missing=True)
        return json.loads(raw)[0] if raw else None


def private_json(path, payload, *, mode=0o600):
    path = Path(path)
    temporary = path.with_name(path.name + ".new")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(payload, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, mode)
    temporary.replace(path)


class Manager:
    def __init__(self, store, *, data_root, status_root, runtime_root, code_root, image="messenger-logger:modern", docker=None, clock=time.time):
        self.store, self.clock = store, clock
        self.data_root, self.status_root, self.runtime_root = map(Path, (data_root, status_root, runtime_root))
        self.code_root, self.image = Path(code_root), image
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]{0,199}", image):
            raise DashboardError("Invalid configured logger image.")
        self.docker = docker or Docker()
        for root, mode in ((self.data_root, 0o700), (self.runtime_root, 0o700), (self.status_root, 0o755)):
            root.mkdir(mode=mode, parents=True, exist_ok=True)
            if root.is_symlink():
                raise DashboardError("Manager directories must not be symbolic links.")
        self.last_cleanup = 0
        self.usage = UsageCollector(self.store, self.data_root, self.status_root, clock=self.clock)

    def status(self, ident):
        try:
            return json.loads((self.status_root / (ident + ".json")).read_text())
        except (OSError, ValueError):
            return {}

    def save_status(self, ident, value):
        value["updated"] = self.clock()
        private_json(self.status_root / (ident + ".json"), value, mode=0o644)

    def path(self, ident):
        account_id(ident)
        root = self.data_root / ident
        if root.is_symlink():
            raise DashboardError("Account directories must not be symbolic links.")
        root.mkdir(mode=0o700, exist_ok=True)
        data = root / "data"
        if data.is_symlink():
            raise DashboardError("Account data must not be a symbolic link.")
        data.mkdir(mode=0o700, exist_ok=True)
        return data

    def launch(self, row, bot):
        ident = account_id(row["id"])
        if type(row["chat_id"]) is not int or row["chat_id"] <= 0 or type(row["revision"]) is not int or row["revision"] <= 0 or row["mode"] not in {"all", "deleted"}:
            raise DashboardError("Invalid worker configuration.")
        cookies = self.store.unseal("cookies:" + ident, row["cookies"])
        parsed = Store.validate_cookies(json.dumps(cookies))
        if parsed["c_user"] != row["facebook_id"] or type(bot["id"]) is not int or bot["id"] <= 0:
            raise DashboardError("Worker identity validation failed.")
        data = self.path(ident)
        credentials = self.runtime_root / (ident + ".json")
        private_json(credentials, {"FB_SESSION_JSON": json.dumps(parsed, separators=(",", ":")), "TELEGRAM_ACCOUNT_KEY": self.store.worker_key(row)})
        args = ["run", "-d", "--name", "messenger-account-" + ident,
                "--label", LABEL + "=true", "--label", "messenger.account=" + ident,
                "--label", "messenger.revision=" + str(row["revision"]),
                "--restart=no", "--stop-timeout", "30", "--network", "host",
                "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--pids-limit", "128", "--memory", "512m", "--memory-swap", "512m",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                "--log-driver", "local", "--log-opt", "max-size=1m", "--log-opt", "max-file=2",
                "--mount", "type=bind,source=" + str(self.code_root) + ",target=/app,readonly",
                "--mount", "type=bind,source=" + str(data) + ",target=/data",
                "--mount", "type=bind,source=" + str(credentials) + ",target=/run/logger-credentials.json,readonly"]
        env = {"LOGGER_CREDENTIALS_FILE": "/run/logger-credentials.json", "LOGGER_TIMEZONE": "Europe/Warsaw", "LOGGER_E2EE": "true",
               "DATA_DIR": "/data", "TELEGRAM_ENABLED": "true", "TELEGRAM_RELAY_URL": "http://127.0.0.1:8788",
               "TELEGRAM_BOT_ID": str(bot["id"]), "TELEGRAM_CHAT_ID": str(row["chat_id"]), "TELEGRAM_TOPICS": "true",
               "TELEGRAM_MODE": row["mode"], "TELEGRAM_SEND_MEDIA": "true", "TELEGRAM_VIEW_ONCE": "true" if row["view_once"] else "false",
               "LOGGER_MEDIA_MAX_MB": "25", "GOMEMLIMIT": "256MiB"}
        for key, value in env.items():
            args += ["--env", key + "=" + value]
        self.docker.call(*args, self.image)

    def stop(self, name, info):
        if info:
            if info.get("Config", {}).get("Labels", {}).get(LABEL) != "true":
                raise DashboardError("A container name is already used outside this dashboard.")
            if info.get("State", {}).get("Running"):
                self.docker.call("stop", "--time", "30", name, timeout=45)
            self.docker.call("rm", name)

    def observe(self, ident, previous):
        result = {"ready": False, "messages": 0, "deletions": 0}
        path = self.path(ident) / "messenger-modern-status.log"
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    return result
                handle.seek(max(0, os.fstat(handle.fileno()).st_size - 16384))
                lines = handle.read(16384).decode("utf-8", errors="replace").splitlines()
            result.update({key: previous.get(key, result[key]) for key in result})
            for line in lines:
                if "Cookie configuration loaded." in line or "Connection is not ready." in line:
                    result["ready"] = False
                    if "Cookie configuration loaded." in line:
                        result.update(messages=0, deletions=0)
                if "Messenger listener ready" in line:
                    result["ready"] = True
                match = re.search(r"This run: (\d{1,10}) messages, (\d{1,10}) deletions,", line)
                if match:
                    result.update(messages=int(match[1]), deletions=int(match[2]))
        except OSError:
            pass
        return result

    def reconcile(self, row, bot):
        ident = account_id(row["id"])
        name, now = "messenger-account-" + ident, self.clock()
        previous = self.status(ident)
        info = self.docker.inspect(name)
        if info and info.get("Config", {}).get("Labels", {}).get(LABEL) != "true":
            raise DashboardError("An unmanaged container conflicts with this account.")
        labels = info.get("Config", {}).get("Labels", {}) if info else {}
        state = info.get("State", {}) if info else {}
        if not row["desired"] or not row["cookies"] or not row["chat_id"] or not bot:
            self.stop(name, info)
            (self.runtime_root / (ident + ".json")).unlink(missing_ok=True)
            self.save_status(ident, {"state": "stopped", "revision": row["revision"]})
            return
        same = labels.get("messenger.revision") == str(row["revision"])
        if info and same and state.get("Running"):
            result = {"state": "running", "revision": row["revision"], "attempts": previous.get("attempts", [])}
            result.update(self.observe(ident, previous if previous.get("revision") == row["revision"] else {}))
            self.save_status(ident, result)
            return
        if info and same and state.get("ExitCode") == 78:
            self.save_status(ident, {"state": "attention", "revision": row["revision"], "reason": "The logger needs valid cookies or configuration. Replace cookies or inspect its local status log, then press Restart."})
            return
        attempts = [x for x in previous.get("attempts", []) if isinstance(x, (int, float)) and now - 900 < x <= now] if previous.get("revision") == row["revision"] else []
        if len(attempts) >= 5 or (attempts and now - attempts[-1] < 60):
            self.save_status(ident, {"state": "retry_wait", "revision": row["revision"], "attempts": attempts,
                                     "reason": "Waiting before a retry. Five starts per 15 minutes are allowed; Restart resets the account revision."})
            return
        self.stop(name, info)
        self.launch(row, bot)
        self.save_status(ident, {"state": "starting", "revision": row["revision"], "attempts": attempts + [now]})

    def cleanup(self):
        # Includes stopped accounts, and contains no Facebook/Telegram credentials.
        for root in self.data_root.iterdir():
            if not root.is_symlink() and re.fullmatch(r"[a-f0-9]{32}", root.name) and root.is_dir():
                data = self.path(root.name)
                self.docker.call("run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                                 "--mount", "type=bind,source=" + str(self.code_root) + ",target=/app,readonly",
                                 "--mount", "type=bind,source=" + str(data) + ",target=/data", "--entrypoint", "python",
                                 self.image, "/app/logger.py", "--cleanup", timeout=120)

    def run_once(self):
        rows = self.store.accounts(private=True)
        if len(rows) > self.store.max_accounts:
            raise DashboardError("Configured accounts exceed the manager limit.")
        bot = self.store.bot()
        for row in rows:
            try:
                self.reconcile(row, bot)
            except Exception:
                self.save_status(account_id(row["id"]), {"state": "attention", "revision": row.get("revision"), "reason": "Account operation failed. Inspect the manager journal or configuration on the server; private details are hidden."})
        # Removing an account revokes its gateway key immediately. Reconcile only
        # our labelled containers; never take over the standalone logger.
        known = {account_id(row["id"]) for row in rows}
        names = self.docker.call("ps", "-a", "--filter", "label=" + LABEL + "=true", "--format", "{{.Names}}")
        for name in names.splitlines():
            if re.fullmatch(r"messenger-account-[a-f0-9]{32}", name) and name.removeprefix("messenger-account-") not in known and self.store.account(name.removeprefix("messenger-account-"), private=True) is None:
                self.stop(name, self.docker.inspect(name))
        for directory in self.data_root.iterdir():
            if re.fullmatch(r"[a-f0-9]{32}", directory.name) and directory.name not in known and self.store.account(directory.name, private=True) is None:
                if directory.is_symlink():
                    directory.unlink()
                else:
                    shutil.rmtree(directory)
                (self.runtime_root / (directory.name + ".json")).unlink(missing_ok=True)
                (self.status_root / (directory.name + ".json")).unlink(missing_ok=True)
        if self.clock() - self.last_cleanup >= 3600:
            self.last_cleanup = self.clock()
            self.cleanup()
        private_json(self.status_root / "host.json", {"updated": self.clock(), "disk_free": shutil.disk_usage(self.data_root).free}, mode=0o644)
        self.usage.update(rows)


def main():
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    manager = Manager(from_environment(readonly=True), data_root="/var/lib/messenger-accounts", status_root="/var/lib/messenger-dashboard-status",
                      runtime_root="/run/messenger-dashboard-workers", code_root=Path(__file__).resolve().parents[1],
                      image=os.environ.get("DASHBOARD_LOGGER_IMAGE", "messenger-logger:modern"))
    while not stop.is_set():
        try:
            manager.run_once()
        except Exception:
            print("Dashboard manager could not reconcile. Existing workers are preserved; private details hidden.", flush=True)
        stop.wait(5)


if __name__ == "__main__":
    main()
