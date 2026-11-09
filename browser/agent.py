"""Ephemeral, sandboxed Chromium. Only typed user actions cross the private pipe."""
from __future__ import annotations

import base64
import argparse
import json
import os
import select
import signal
import socket
import socketserver
import sys
import threading
import time
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright
from events import normalize
from capture import FrameCapture
from policy import LOGIN_URLS, allowed_url, session_url

REQUIRED_COOKIES = {"facebook": {"c_user", "xs", "datr"}, "instagram": {"ds_user_id", "sessionid", "csrftoken"}}
OPTIONAL_COOKIES = {"facebook": {"fr", "sb", "wd", "presence", "ps_l", "ps_n"},
                    "instagram": {"rur", "mid", "ig_did", "shbid", "shbts"}}


class Tunnel(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                peer.connect("/run/egress.sock")
                while True:
                    ready, _, _ = select.select([self.request, peer], [], [], 60)
                    if not ready:
                        return
                    for source in ready:
                        block = source.recv(65536)
                        if not block:
                            return
                        (peer if source is self.request else self.request).sendall(block)
        except OSError:
            pass


class TunnelServer(socketserver.ThreadingTCPServer):
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline-check", action="store_true")
    parser.add_argument("--platform", choices=tuple(LOGIN_URLS), default="facebook")
    parser.add_argument("--expires", type=float, default=time.time() + 1200)
    args = parser.parse_args()
    platform = args.platform
    required = REQUIRED_COOKIES[platform]
    cookie_names = required | OPTIONAL_COOKIES[platform]
    cookie_origin = "https://www." + platform + ".com/"
    # The container expires even if the host controller is killed mid-session.
    signal.signal(signal.SIGALRM, lambda *_: os._exit(0))
    signal.setitimer(signal.ITIMER_REAL, max(0.1, min(1200, args.expires - time.time())))
    tunnel = TunnelServer(("127.0.0.1", 0), Tunnel)
    threading.Thread(target=tunnel.serve_forever, daemon=True).start()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path="/usr/bin/chromium", headless=True, chromium_sandbox=True,
            proxy={"server": "http://127.0.0.1:" + str(tunnel.server_address[1])},
            args=["--proxy-bypass-list=<-loopback>", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"])
        context = browser.new_context(viewport={"width": 1024, "height": 768}, accept_downloads=False, service_workers="block", ignore_https_errors=args.offline_check)
        context.route("**/*", lambda route: route.continue_() if allowed_url(route.request.url, platform) else route.abort())
        page = context.new_page()
        context.set_default_timeout(10000)
        capture = FrameCapture(context)
        ready, checked_at = False, 0.0

        def session_cookies():
            return {c["name"]: c["value"] for c in context.cookies([cookie_origin]) if c["name"] in cookie_names}

        def apply_input(event):
            action = event["action"]
            if action == "click":
                page.mouse.click(max(0, min(page.viewport_size["width"] - 1, event["x"])), max(0, min(page.viewport_size["height"] - 1, event["y"])))
            elif action == "text":
                page.keyboard.insert_text(event["text"])
            elif action == "key":
                page.keyboard.press(event["key"])
            elif action == "scroll":
                page.mouse.wheel(0, event["delta"])
            elif action == "reload":
                page.reload(wait_until="domcontentloaded", timeout=20000)
        for line in sys.stdin:
            request_id = None
            try:
                if len(line) > 16000:
                    raise ValueError
                item = json.loads(line)
                request_id = item.get("id")
                action = item.get("action")
                if args.offline_check and action in {"open", "capture"}:
                    raise ValueError("External login is disabled in the offline check.")
                pages = [p for p in context.pages if not p.is_closed()]
                if pages:
                    page = pages[-1]
                if action == "open":
                    mobile = item.get("mobile") is True
                    if mobile:
                        page.set_viewport_size({"width": 432, "height": 800})
                    url = "https://m.facebook.com/" if mobile and platform == "facebook" else LOGIN_URLS[platform]
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    result = {"started": True}
                elif action == "smoke":
                    page.set_content("<title>Offline browser fixture</title><input aria-label='Synthetic input'>")
                    result = {"sandboxed": True, "title": page.title()}
                elif action == "smoke-check" and page.title() == "Offline browser fixture":
                    result = {"input": page.locator("input").input_value()}
                elif action == "smoke-network" and args.offline_check and platform == "instagram":
                    page.goto(LOGIN_URLS[platform], wait_until="domcontentloaded", timeout=10000)
                    assert required <= session_cookies().keys() and not session_url(page.url, platform)
                    page.get_by_role("link", name="Synthetic Inbox").click()
                    page.wait_for_function("() => globalThis.syntheticInstagramAsset === true", timeout=10000)
                    result = {"title": page.title(), "ready": session_url(page.url, platform)}
                elif action == "smoke-network" and args.offline_check:
                    page.goto("https://fixture.facebook.com/", wait_until="domcontentloaded", timeout=10000)
                    page.wait_for_function("() => globalThis.syntheticChallenge?.script && globalThis.syntheticChallenge?.frame && globalThis.syntheticChallenge?.alternative && globalThis.syntheticArkose?.script && globalThis.syntheticArkose?.client && globalThis.syntheticArkose?.frame", timeout=10000)
                    result = {"title": page.title(), "challenge_loaded": True, "arkose_loaded": True, "unrelated_blocked": page.evaluate("globalThis.syntheticUnrelated === false")}
                elif action == "frame":
                    now = time.monotonic()
                    if now - checked_at >= 0.8:
                        cookies = session_cookies()
                        ready = all(cookies.get(key) for key in required)
                        checked_at = now
                    result = {"site": urlsplit(page.url).hostname or "Starting browser", "width": page.viewport_size["width"], "height": page.viewport_size["height"], "ready": ready and not page.is_closed() and session_url(page.url, platform)}
                    if item.get("metadata_only") is not True:
                        # Direct compositor capture avoids per-frame font/stability
                        # waits in the general screenshot helper. Nothing is saved.
                        result["jpeg"] = capture.jpeg(page)
                elif action == "input":
                    for event in normalize(item.get("events")):
                        apply_input(event)
                    result = {}
                elif action in {"click", "text", "key", "scroll", "reload"}:
                    apply_input(normalize([item])[0])
                    result = {}
                elif action == "capture":
                    if page.is_closed() or not session_url(page.url, platform):
                        raise ValueError
                    result = {"cookies": session_cookies()}
                    if not all(result["cookies"].get(key) for key in required):
                        raise ValueError
                else:
                    raise ValueError
                print(json.dumps({"id": request_id, "ok": True, "result": result}), flush=True)
            except Exception:
                if "--offline-check" in sys.argv:
                    import traceback
                    traceback.print_exc(file=sys.stderr)
                print(json.dumps({"id": request_id, "ok": False}), flush=True)  # Never print provider, page, or input details.
        capture.reset()
        context.close()
        browser.close()
    tunnel.shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        if "--offline-check" in sys.argv:
            import traceback
            traceback.print_exc(file=sys.stderr)
        print('{"ok":false}', flush=True)
        raise SystemExit(1)
