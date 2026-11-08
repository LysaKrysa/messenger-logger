"""Real HTTPS owner sign-in, isolated controls, bilingual pages and mobile layouts."""
from playwright.sync_api import expect

from dashboard.portal_i18n import text


def verify(browser, admin, origin, store, app, api, output):
    row = next(item for item in store.accounts(private=True) if item["name"] == "Jordan")
    other = next(item for item in store.accounts(private=True) if item["name"] == "Alex")
    auth = app.extensions["portal_auth"]
    history = app.extensions["deliveries"]
    history.conversations.remember(other, "999", "OTHER_OWNER_PRIVATE_CHAT")
    store.report_worker(row, {"revision": row["revision"], "connected": True, "last_event": store.clock()})
    counts = dict(revision=row["revision"], started_at=store.clock() - 600, regular_connected=True, encrypted_connected=True,
                  messages=12, deletions=3, attachments=4, files=2, media_bytes=524288,
                  conversations=2, run_messages=5, run_deletions=1, run_events=8)
    store.report_stats(row, counts)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    token = auth.issue(row["chat_id"])
    page.goto(origin + "/me/login#" + token)
    expect(page.get_by_role("heading", name=text("pl", "login_title"), exact=True)).to_be_visible()
    assert page.evaluate("location.hash") == "", "Sign-in secret left in the address bar"
    assert not any(cookie["name"] == "messenger_admin" for cookie in context.cookies())
    page.get_by_role("button", name=text("pl", "login_request"), exact=True).click()
    expect(page.locator("#login-pending")).to_be_visible()
    expect(page.locator("#complete-login")).to_be_hidden()
    value = next(item for item in auth.confirmations() if item["owner_chat"] == row["chat_id"])
    expect(page.locator("#login-code")).to_have_text(value["code"])
    page.screenshot(path=str(output / "user-login-mobile.png"), full_page=True)
    assert not auth.confirm(other["chat_id"], auth.callback_data(value))
    assert auth.confirm(row["chat_id"], auth.callback_data(value))
    expect(page.locator("#login-approved")).to_be_visible()
    expect(page.locator("#login-owner")).to_have_text(str(row["chat_id"]))
    page.get_by_role("button", name=text("pl", "login_continue"), exact=True).click()
    page.wait_for_url(origin + "/me")
    expect(page.get_by_role("heading", name=text("pl", "hello"), exact=True)).to_be_visible()
    assert "SYNTHETIC_ORIGINAL_NOT_VISIBLE_IN_HISTORY" not in page.content()
    assert not any(cookie["name"] == "messenger_admin" for cookie in context.cookies())
    assert page.locator(".status").first.inner_text() == text("pl", "connected")
    # The mobile drawer keeps its localized labels and closes after navigation.
    page.get_by_role("button", name=text("pl", "open_nav"), exact=True).tap()
    expect(page.locator("#workspace-navigation")).to_be_visible()
    page.get_by_role("link", name=text("pl", "settings"), exact=True).tap()
    page.wait_for_url(origin + "/me/settings")
    expect(page.locator("#workspace-navigation")).to_be_hidden()
    page.get_by_label(text("pl", "mode"), exact=True).select_option("all")
    page.get_by_label(text("pl", "media"), exact=True).uncheck()
    page.get_by_label(text("pl", "view_once"), exact=True).check()
    page.get_by_role("button", name=text("pl", "save_preferences"), exact=True).click()
    updated = store.account(row["id"])
    assert updated["mode"] == "all" and updated["view_once"]
    assert updated["revision"] == row["revision"], "Preferences restarted the worker"
    assert store.controls(updated)["media"] is False
    assert store.account(other["id"])["mode"] == "deleted"
    page.goto(origin + "/me/profile")
    page.get_by_label(text("pl", "display_name"), exact=True).fill("Ada & friends")
    page.get_by_label(text("pl", "language"), exact=True).select_option("en")
    page.get_by_role("button", name=text("pl", "save_profile"), exact=True).click()
    expect(page.get_by_role("heading", name="Your profile", exact=True)).to_be_visible()
    assert store.telegram_language(other["chat_id"]) == "pl"
    assert store.telegram_language(row["chat_id"]) == "en"
    page.goto(origin + "/me/conversations")
    assert "OTHER_OWNER_PRIVATE_CHAT" not in page.content()
    conversation = page.locator(".record").filter(has=page.get_by_role("heading", name="Ada", exact=True))
    conversation.locator("summary").click()
    conversation.get_by_label("Telegram forwarding", exact=True).select_option("deleted")
    conversation.get_by_role("button", name="Save conversation", exact=True).click()
    assert store.filters(row["id"])["rules"]["42"]["forward"] == "deleted"
    page.goto(origin + "/me/deliveries")
    page.get_by_role("link", name="Review resend", exact=True).first.click()
    expect(page.get_by_text("This may create a duplicate.", exact=True)).to_be_visible()
    before = len(api.calls)
    page.get_by_role("button", name="Confirm resend", exact=True).click()
    assert len(api.calls) == before
    page.get_by_role("checkbox").check()
    page.get_by_role("button", name="Confirm resend", exact=True).click()
    page.wait_for_url(origin + "/me/deliveries?outcome=sent")
    assert len(api.calls) == before + 1
    assert api.calls[-1][1]["chat_id"] == row["chat_id"]
    page.goto(origin + "/me")
    page.get_by_role("button", name="Pause capture", exact=True).click()
    expect(page.locator(".status").first).to_have_text("Paused")
    page.get_by_role("button", name="Resume capture", exact=True).click()
    assert store.account(row["id"])["desired"]
    # A new revision has no fresh worker report yet; never infer online status.
    assert page.locator(".status").first.inner_text() == "No fresh connection report"
    row = store.account(row["id"], private=True)
    store.report_worker(row, {"revision": row["revision"], "connected": True, "last_event": store.clock()})
    counts["revision"] = row["revision"]; store.report_stats(row, counts)
    routes = (("/me", "overview"), ("/me/settings", "settings"), ("/me/profile", "profile"), ("/me/conversations", "conversations"), ("/me/deliveries", "deliveries"))
    targets = """() => [...document.querySelectorAll('button,a.button,a.nav-item,summary,label.check')].filter(e=>e.getClientRects().length).flatMap(e=>{const r=e.getBoundingClientRect();return r.width+.1>=44&&r.height+.1>=44?[]:[{text:e.textContent,width:r.width,height:r.height}];})"""
    for width in (320, 390, 768, 1024, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        for path, label in routes:
            page.goto(origin + path)
            assert page.locator("h1").count() == 1
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), (width, label, "Overflow")
            assert not page.evaluate(targets), (width, label, page.evaluate(targets))
            assert "OTHER_OWNER_PRIVATE_CHAT" not in page.content()
            assert "SYNTHETIC_BROWSER_PRIVATE" not in page.content()
            if width in (390, 1440):
                page.evaluate("window.scrollTo(0,0)")
                page.screenshot(path=str(output / ("user-" + label + "-" + str(width) + ".png")), full_page=True)
    page.goto(origin + "/accounts/" + other["id"])
    page.wait_for_url(origin + "/login")
    assert "OTHER_OWNER_PRIVATE_CHAT" not in page.content()
    page.goto(origin + "/me/profile")
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Sign out all browsers", exact=True).click()
    page.wait_for_url(origin + "/me/login?lang=en")
    assert store.account(row["id"])["desired"], "Sign-out paused capture"
    assert not errors, errors
    context.close()
    # Owner actions must not change the administrator's independent login.
    admin.goto(origin + "/")
    expect(admin.get_by_role("heading", name="Overview", exact=True)).to_be_visible()
