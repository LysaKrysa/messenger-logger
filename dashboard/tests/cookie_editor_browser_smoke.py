"""Admin-only stored-cookie editing through real forms, with synthetic values."""
import json

from playwright.sync_api import expect


def verify(page, origin, account_url, store, output):
    ident = account_url.rsplit("/", 1)[-1]
    before = store.account(ident, private=True)
    original = store.unseal("cookies:" + ident, before["cookies"])
    page.goto(account_url)
    assert original["xs"] not in page.content()
    page.locator(".session-panel summary").click()
    page.get_by_role("button", name="Edit saved cookies", exact=True).click()
    expect(page.get_by_role("heading", name="Edit saved cookies", exact=True)).to_be_visible()
    editor = page.get_by_label("Saved cookie JSON", exact=True)
    assert json.loads(editor.input_value()) == original
    assert page.locator("body").get_attribute("data-refresh") == "false"
    # A bad edit stays available for correction; it cannot replace storage.
    editor.fill("not valid JSON <literal>")
    page.get_by_role("button", name="Save cookie changes", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Cookie JSON is invalid")
    assert editor.input_value() == "not valid JSON <literal>"
    assert store.account(ident, private=True)["cookies"] == before["cookies"]
    page.get_by_role("button", name="Reload saved cookies", exact=True).click()
    assert json.loads(editor.input_value()) == original
    # A concurrent owner refresh must win over an already open editor.
    refreshed = dict(original, xs="SYNTHETIC_REFRESHED_COOKIE_NOT_REAL")
    store.update_cookies(ident, json.dumps(refreshed))
    editor.fill(json.dumps(dict(original, xs="SYNTHETIC_STALE_EDIT_NOT_REAL")))
    page.get_by_role("button", name="Save cookie changes", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("changed")
    assert store.unseal("cookies:" + ident, store.account(ident, private=True)["cookies"])["xs"] == refreshed["xs"]
    page.get_by_role("button", name="Reload saved cookies", exact=True).click()
    edited = dict(refreshed, xs="SYNTHETIC_ADMIN_EDITED_COOKIE_NOT_REAL", fr="SYNTHETIC_EXTRA_COOKIE_NOT_REAL")
    editor.fill(json.dumps(edited, indent=2))
    for width in (320, 390, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), (width, "Cookie editor overflow")
        assert page.get_by_role("button", name="Save cookie changes").bounding_box()["height"] >= 44
        if width in (390, 1440):
            page.evaluate("window.scrollTo(0,0)")
            page.screenshot(path=str(output / ("cookie-editor-synthetic-" + str(width) + ".png")), full_page=True)
    page.get_by_role("button", name="Save cookie changes", exact=True).click()
    page.wait_for_url(account_url + "?cookies_saved=1")
    current = store.account(ident, private=True)
    assert store.unseal("cookies:" + ident, current["cookies"]) == edited
    assert current["chat_id"] == before["chat_id"] and current["desired"]
    assert current["pair_epoch"] == before["pair_epoch"]
    assert edited["xs"] not in page.content()
    # Leaving the explicit editor returns to an account page without secrets.
    page.goto(account_url)
    page.locator(".session-panel summary").click()
    page.get_by_role("button", name="Edit saved cookies", exact=True).click()
    page.get_by_role("link", name="Cancel", exact=True).click()
    assert edited["xs"] not in page.content()
    page.set_viewport_size({"width": 1440, "height": 1050})
    page.goto(account_url)
