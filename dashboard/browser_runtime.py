"""Private fixed-function browser process and deadline control; standard library only."""
from __future__ import annotations
import json
from pathlib import Path
import queue
import re
import subprocess
import threading
import time

LABEL = "messenger.setup.managed"
IMAGE = "messenger-onboarding:browser"
IDENT = re.compile(r"[a-f0-9]{32}\Z")
ACTIONS = {"open", "frame", "input", "click", "text", "key", "scroll", "reload", "capture"}

class BrowserError(ValueError):
    pass


class DockerBrowser:
    def __init__(self, ident, expires, *, proxy_path, policy_path, image=IMAGE, clock=time.time, offline_check=False, platform="facebook"):
        if not IDENT.fullmatch(ident) or platform not in ("facebook", "instagram"):
            raise BrowserError("Invalid setup session.")
        self.ident, self.expires, self.clock = ident, expires, clock
        self.name = "messenger-setup-" + ident
        self.lock, self.answers = threading.Lock(), queue.Queue(maxsize=4)
        self.sequence = 0
        self.failed = False
        command = self.command(ident, proxy_path, policy_path, image)
        command += ["--expires", str(expires)]
        if platform == "instagram":
            command += ["--platform", platform]
        if offline_check:
            command.append("--offline-check")
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None if offline_check else subprocess.DEVNULL)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    @staticmethod
    def command(ident, proxy_path, policy_path, image=IMAGE):
        if not IDENT.fullmatch(ident) or image != IMAGE:
            raise BrowserError("Invalid browser launch settings.")
        return ["docker", "run", "--rm", "-i", "--init", "--name", "messenger-setup-" + ident,
                "--label", LABEL + "=true", "--network", "none", "--read-only", "--user", "10001:10001",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--security-opt", "seccomp=" + str(policy_path), "--pids-limit", "256", "--memory", "1024m", "--memory-swap", "1024m",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m,uid=10001,gid=10001,mode=700", "--shm-size", "128m",
                "--log-driver", "none", "--mount", "type=bind,source=" + str(proxy_path) + ",target=/run/egress.sock,readonly", image]

    def _read(self):
        try:
            while raw := self.process.stdout.readline(1500001):
                if len(raw) > 1500000 or not raw.endswith(b"\n"):
                    break
                self.answers.put(json.loads(raw), timeout=1)
        except Exception:
            pass
        try:
            self.answers.put_nowait({"ok": False})
        except queue.Full:
            pass

    def call(self, action, data=None):
        if action not in ACTIONS | {"smoke", "smoke-check", "smoke-network"}:
            raise BrowserError("This browser action is unavailable.")
        with self.lock:
            try:
                if self.process.poll() is not None:
                    raise ValueError
                self.sequence += 1
                raw = json.dumps({**(data or {}), "action": action, "id": self.sequence}, ensure_ascii=False).encode() + b"\n"
                if len(raw) > 16000:
                    raise ValueError
                self.process.stdin.write(raw)
                self.process.stdin.flush()
                answer = self.answers.get(timeout=40)
                if answer.get("id") != self.sequence:
                    self.failed = True
                    raise ValueError
                if answer.get("ok") is not True or not isinstance(answer.get("result"), dict):
                    raise ValueError
                return answer["result"]
            except queue.Empty:
                self.failed = True
                raise BrowserError("The remote browser stopped responding. Start it again to continue.") from None
            except Exception:
                raise BrowserError("The remote browser could not complete that action. Finish the sign-in checks or restart the browser.") from None

    def close(self):
        # The exact container was created by this object. Never remove arbitrary
        # containers merely because their names have a similar prefix.
        try:
            inspected = subprocess.run(["docker", "container", "inspect", self.name], capture_output=True, timeout=10)
            if inspected.returncode == 0:
                item = json.loads(inspected.stdout)[0]
                if item.get("Config", {}).get("Labels", {}).get(LABEL) == "true":
                    subprocess.run(["docker", "rm", "--force", item["Id"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        except Exception:
            pass
        try:
            self.process.stdin.close()
            self.process.wait(timeout=5)
        except Exception:
            self.process.kill()
            self.process.wait(timeout=5)
        self.reader.join(timeout=1)


class Controller:
    def __init__(self, *, proxy_path, policy_path, factory=DockerBrowser, clock=time.time):
        self.proxy_path, self.policy_path = Path(proxy_path), Path(policy_path)
        self.factory, self.clock = factory, clock
        self.lock, self.sessions = threading.RLock(), {}

    def start(self, ident, expires, mobile=False, *, platform="facebook"):
        if not isinstance(ident, str) or not IDENT.fullmatch(ident) or type(expires) not in {int, float} or not self.clock() < expires <= self.clock() + 1201 or platform not in ("facebook", "instagram"):
            raise BrowserError("This setup session is unavailable.")
        self.sweep()
        with self.lock:
            if ident in self.sessions:
                return {"started": True}
            if self.sessions:
                raise BrowserError("Someone else is connecting. Please try again shortly; one browser runs at a time.")
            browser = self.factory(ident, expires, proxy_path=self.proxy_path, policy_path=self.policy_path, clock=self.clock, platform=platform)
            self.sessions[ident] = browser
        try:
            return browser.call("open", {"mobile": mobile is True})
        except Exception:
            self.stop(ident)
            raise

    def call(self, ident, action, data):
        self.sweep()
        with self.lock:
            browser = self.sessions.get(ident)
        if browser is None:
            raise BrowserError("The temporary browser is closed. Start it again to continue.")
        return browser.call(action, data)

    def stop(self, ident):
        with self.lock:
            browser = self.sessions.pop(ident, None)
        if browser:
            browser.close()

    def sweep(self):
        with self.lock:
            expired = [key for key, value in self.sessions.items() if value.expires <= self.clock() or getattr(value, "failed", False) or value.process.poll() is not None]
        for ident in expired:
            self.stop(ident)

    def close(self):
        with self.lock:
            keys = list(self.sessions)
        for ident in keys:
            self.stop(ident)


