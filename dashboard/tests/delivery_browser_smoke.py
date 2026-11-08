"""Real admin browser controls, mobile layouts and one confirmed synthetic resend."""
import json
import uuid


class OfflineTelegram:
    def __init__(self):
        self.calls = []

    def post(self, method, body, **options):
        assert method == "sendMessage", "Unexpected synthetic delivery method"
        self.calls.append((method, json.loads(body), options))
        return 200, {"ok": True, "result": {"message_id": 123}}


def verify(page, origin, account_url, store, history, api, output):
    ident = account_url.rsplit("/", 1)[-1]
    row = store.account(ident, private=True)
    group_name = "Research group with a long conversation title for smaller mobile screens"
    history.conversations.report(row, {"revision": row["revision"], "conversations": [
        {"id": "42", "name": "Ada"}, {"id": "43", "name": group_name}]})
    for tid, status, payload in (("42", 200, {"ok": True}), ("43", 400, {"ok": False, "error_code": 400}), ("42", 502, {"ok": False})):
        context = {"group": uuid.uuid4().hex, "thread": tid, "name": "Ada" if tid == "42" else group_name, "kind": "deleted", "expires": store.clock() + 259200}
        delivery = history.begin(row, "sendMessage", context)
        history.save(row, delivery, "sendMessage", {"chat_id": row["chat_id"], "text": "SYNTHETIC_ORIGINAL_NOT_VISIBLE_IN_HISTORY"})
        history.finish(row, delivery, status, payload)
    page.goto(account_url)
    page.get_by_role("link", name="Conversation filters", exact=True).click()
    page.get_by_label("Conversations to capture").select_option("include")
    page.get_by_role("button", name="Save default policy", exact=True).click()
    assert not store.filters(ident)["capture_default"]
    cards = page.locator(".record")
    ada = cards.filter(has=page.get_by_role("heading", name="Ada", exact=True))
    ada.locator("summary").click()
    ada.get_by_label("Capture", exact=True).select_option("include")
    ada.get_by_label("Telegram forwarding", exact=True).select_option("all")
    ada.get_by_role("button", name="Save conversation", exact=True).click()
    group = page.locator(".record").filter(has=page.get_by_role("heading", name=group_name, exact=True))
    group.locator("summary").click()
    group.get_by_label("Capture", exact=True).select_option("exclude")
    group.get_by_label("Telegram forwarding", exact=True).select_option("off")
    group.get_by_role("button", name="Save conversation", exact=True).click()
    assert store.filters(ident)["rules"]["42"]["capture"] is True
    assert store.filters(ident)["rules"]["43"]["capture"] is False
    for width in (320, 390, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.locator(".record summary").first.click()
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), "Conversation page overflow"
        assert page.get_by_role("button", name="Save default policy").bounding_box()["height"] >= 44
        page.screenshot(path=str(output / ("conversations-" + str(width) + ".png")), full_page=True)
    page.get_by_role("link", name="Delivery history", exact=True).click()
    assert page.get_by_text("Sent", exact=True).count() == 1
    assert page.get_by_text("Failed", exact=True).count() == 1
    assert page.get_by_text("Uncertain", exact=True).count() == 1
    assert "SYNTHETIC_ORIGINAL_NOT_VISIBLE_IN_HISTORY" not in page.content()
    uncertain = page.locator(".record").filter(has=page.get_by_text("Uncertain", exact=True))
    uncertain.get_by_role("link", name="Review resend", exact=True).click()
    assert page.get_by_text("This may create a duplicate.", exact=True).is_visible()
    page.get_by_role("button", name="Confirm resend", exact=True).click()
    assert len(api.calls) == 0, "Browser allowed an unacknowledged resend"
    page.get_by_role("checkbox").check()
    page.get_by_role("button", name="Confirm resend", exact=True).click()
    page.wait_for_url(account_url + "/deliveries?outcome=sent")
    assert len(api.calls) == 1
    assert api.calls[0][1] == {"chat_id": row["chat_id"], "text": "SYNTHETIC_ORIGINAL_NOT_VISIBLE_IN_HISTORY"}
    page.reload()
    assert len(api.calls) == 1
    for width in (320, 390, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), "Delivery page overflow"
        assert page.get_by_role("link", name="Review resend").first.bounding_box()["height"] >= 44
        page.screenshot(path=str(output / ("deliveries-" + str(width) + ".png")), full_page=True)
    page.goto(account_url)
