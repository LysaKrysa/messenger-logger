"""Private fixed-function Docker controller; no public browser/debugging ports."""
from __future__ import annotations

import atexit
import hmac
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time

from flask import Flask, jsonify, request

from .browser_proxy import Connect, Proxy

from .browser_runtime import LABEL, IDENT, ACTIONS, BrowserError, Controller


def cleanup_orphans():
    result = subprocess.run(["docker", "ps", "-aq", "--filter", "label=" + LABEL + "=true"], capture_output=True, timeout=15, check=True)
    for ident in result.stdout.decode().splitlines():
        if re.fullmatch(r"[a-f0-9]{12,64}", ident):
            subprocess.run(["docker", "rm", "--force", ident], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=True)


def create_app(controller=None, *, secret=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 16384
    if secret is None:
        import hashlib
        key = Path(os.environ.get("DASHBOARD_KEY_FILE", "/etc/messenger-dashboard/master.key")).read_bytes().strip()
        secret = hmac.new(key, b"remote-browser-api", hashlib.sha256).hexdigest()
    if controller is None:
        cleanup_orphans()
        runtime = Path("/run/messenger-onboarding")
        runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        proxy_path = runtime / "egress.sock"
        proxy_path.unlink(missing_ok=True)
        proxy = Proxy(str(proxy_path), Connect)
        os.chown(proxy_path, 10001, 10001)
        os.chmod(proxy_path, 0o600)
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        controller = Controller(proxy_path=proxy_path, policy_path=Path(__file__).resolve().parents[1] / "browser/seccomp.json")
        stop = threading.Event()
        def reap():
            while not stop.wait(2):
                controller.sweep()
        threading.Thread(target=reap, daemon=True).start()
        def cleanup():
            if stop.is_set():
                return
            stop.set()
            controller.close()
            proxy.shutdown()
            proxy.server_close()
        atexit.register(cleanup)
        app.extensions["browser_cleanup"] = cleanup
    app.extensions["browser_controller"] = controller

    @app.post("/browser/<ident>/<action>")
    def command(ident, action):
        authorization = request.headers.get("Authorization", "")
        if not hmac.compare_digest(authorization, "Bearer " + secret):
            return jsonify(error="Browser controller authorization failed."), 403
        if not IDENT.fullmatch(ident):
            return jsonify(error="Invalid setup session."), 400
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error="Invalid browser request."), 400
        try:
            if action == "start":
                result = controller.start(ident, data.get("expires"), data.get("mobile") is True,
                                          platform=data.get("platform", "facebook"))
            elif action == "stop":
                controller.stop(ident)
                result = {}
            elif action in ACTIONS - {"open"}:
                result = controller.call(ident, action, data)
            else:
                raise BrowserError("This browser action is unavailable.")
            return jsonify(result)
        except BrowserError as exc:
            return jsonify(error=str(exc)), 409
        except Exception:
            return jsonify(error="The remote browser could not start. Contact the administrator."), 503

    @app.errorhandler(Exception)
    def error(_):
        return jsonify(error="The browser request could not be completed."), 400

    return app
