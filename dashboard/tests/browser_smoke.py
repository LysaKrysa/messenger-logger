"""Real-browser UI verification with synthetic data; no Facebook/Telegram network."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
import threading
import time

from cryptography.fernet import Fernet
from flask import request
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from dashboard.store import Store
from dashboard.web import create_app
from dashboard.tests.setup_browser_smoke import OfflineBrowser, verify as verify_setup
from dashboard.tests.keyboard_browser_smoke import verify as verify_keyboard
from dashboard.tests.layout_browser_smoke import verify as verify_layout
from dashboard.tests.usage_browser_smoke import seed as seed_usage, verify as verify_usage
from dashboard.tests.delivery_browser_smoke import OfflineTelegram, verify as verify_deliveries
from dashboard.tests.portal_browser_smoke import verify as verify_portal
from dashboard.tests.cookie_editor_browser_smoke import verify as verify_cookie_editor


def verify_instagram(page, origin, store, owner):
    page.goto(origin + "/")
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("link", name="Connect Instagram").click()
    page.get_by_role("heading", name="Connect Instagram").wait_for()
    page.get_by_label("Account label").fill("Instagram pilot")
    page.locator("#cookies").fill(json.dumps({"ds_user_id": "444000887",
        "sessionid": "SYNTHETIC_INSTAGRAM_PRIVATE", "csrftoken": "SYNTHETIC_CSRF"}))
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    page.get_by_role("button", name="Create account").click()
    ident = page.url.rsplit("/", 1)[-1]
    assert store.account(ident)["platform"] == "instagram"
    assert "SYNTHETIC_INSTAGRAM_PRIVATE" not in page.content()
    page.get_by_role("button", name="Create invitation").click()
    assert store.claim_invite(page.locator("#invite").input_value().split("start=")[1], owner, "@synthetic_jordan")
    page.goto(origin + "/accounts/" + ident)
    page.get_by_role("button", name="Approve pairing").click()
    page.get_by_role("button", name="Start", exact=True).click()
    assert store.account(ident)["desired"]
    assert store.user_account(owner, platform="instagram")["id"] == ident
    assert store.user_account(owner)["id"] != ident
    store.action(ident, "remove")
    page.set_viewport_size({"width": 1440, "height": 1050})


def main():
    output = Path(os.environ.get("DASHBOARD_SCREENSHOTS", "dashboard-screenshots"))
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        store = Store(root / "state", Fernet.generate_key())
        store.set_password("synthetic-browser-password")
        store.configure_bot("123456:" + "SYNTHETIC_BOT_TOKEN_NOT_REAL" * 2,
                            {"id": 123456, "username": "workspace_example_bot", "has_topics_enabled": True})
        status = root / "status"
        status.mkdir()
        for index, name in enumerate(("My account", "Alex", "Maya")):
            ident = store.add_account(name, json.dumps({"c_user": str(111222333 + index), "xs": "SYNTHETIC", "datr": "SYNTHETIC"}))
            invite = store.invite(ident).split("start=")[1]
            store.claim_invite(invite, 87654321 + index, "@synthetic_friend")
            if index < 2:
                store.action(ident, "approve")
                store.action(ident, "start")
            (status / (ident + ".json")).write_text(json.dumps({"state": "running" if index < 2 else "stopped", "ready": index < 2, "updated": time.time()}))
        (status / "host.json").write_text(json.dumps({"updated": time.time(), "disk_free": int(38.4 * 1024 ** 3)}))
        store.set_setting("bot_status", "Invitation receiver connected")
        seed_usage(store, root / "accounts", status)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        origin = "https://127.0.0.1:" + str(port)
        offline = OfflineBrowser()
        telegram = OfflineTelegram()
        app = create_app(store, origin=origin, status_root=status, browser_client=offline, delivery_api=telegram)
        @app.after_request
        def simulate_lost_handoff_response(response):
            if request.path == "/setup/api/finish" and offline.lose_finish_response and response.status_code == 200:
                response = app.json.response({"error": "Synthetic response lost after the session was saved"})
                response.status_code = 503
            return response
        server = make_server("127.0.0.1", port, app, ssl_context="adhoc", threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                executable = os.environ.get("DASHBOARD_TEST_BROWSER") or shutil.which("google-chrome") or shutil.which("chromium")
                options = {"executable_path": executable} if executable else {}
                browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"], **options)
                context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1050})
                context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/login")
                # Headless browsers may omit the automatic favicon fetch made
                # by a normal browser's tab UI. Exercise it explicitly.
                assert page.request.get(origin + "/favicon.ico").status == 204
                page.get_by_label("Administrator password").fill("synthetic-browser-password")
                page.get_by_role("button", name="Sign in").click()
                page.wait_for_url(origin + "/")
                page.screenshot(path=str(output / "desktop.png"), full_page=True)
                page.get_by_role("link", name="Add account", exact=False).click()
                page.get_by_label("Account label").fill("Jordan")
                page.locator("#cookies").fill(json.dumps({"c_user": "444000887", "xs": "SYNTHETIC_BROWSER_PRIVATE", "datr": "SYNTHETIC_BROWSER_PRIVATE"}))
                page.get_by_role("button", name="Create account").click()
                account_url = page.url
                ident = account_url.rsplit("/", 1)[-1]
                assert "SYNTHETIC_BROWSER_PRIVATE" not in page.content()
                page.get_by_role("button", name="Create invitation").click()
                token = page.locator("#invite").input_value().split("start=")[1]
                assert store.claim_invite(token, 99999888, "@synthetic_jordan")
                page.goto(account_url)
                page.get_by_role("button", name="Approve pairing").click()
                page.get_by_role("button", name="Start", exact=True).click()
                assert store.account(ident)["desired"] == 1
                page.screenshot(path=str(output / "account.png"), full_page=True)
                verify_cookie_editor(page, origin, account_url, store, output)
                verify_deliveries(page, origin, account_url, store, app.extensions["deliveries"], telegram, output)
                verify_portal(browser, page, origin, store, app, telegram, output)
                verify_instagram(page, origin, store, 99999888)
                page.goto(account_url)
                page.get_by_role("link", name="All accounts", exact=True).click()
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(output / "mobile.png"), full_page=True)
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), "Mobile horizontal overflow"
                assert not errors, errors
                verify_usage(page, origin, store, output)
                verify_layout(browser, page, origin, account_url, output)
                verify_setup(browser, page, origin, store, offline, output)
                verify_keyboard(browser, page, origin, offline, output, "chromium")
                with playwright.webkit.launch(headless=True) as webkit:
                    verify_keyboard(webkit, page, origin, offline, output, "webkit")
                browser.close()
            print("Browser smoke test passed: responsive dashboard/drawer/contrast, guest flow and Chromium/WebKit mobile keyboard, focus, composition and scrolling fixtures. No physical phone keyboard or live Facebook login was used.")
        finally:
            server.shutdown()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
