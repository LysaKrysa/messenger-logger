"""Responsive geometry, usable controls and navigation against the real app."""
from playwright.sync_api import expect


def verify(browser, admin, origin, account_url, output):
    routes = (("/", "overview"), (account_url.removeprefix(origin), "account"),
              ("/bot", "bot"), ("/setup-links", "invitations"), ("/accounts/new", "new-account"))
    target_check = """() => [...document.querySelectorAll(
      'button, a.button, a.nav-item, a.back-link, summary, label.check'
    )].filter(element => element.getClientRects().length &&
      getComputedStyle(element).visibility !== 'hidden').flatMap(element => {
        const box = element.getBoundingClientRect();
        return box.width + .1 >= 44 && box.height + .1 >= 44 ? [] :
          [{text: element.textContent.trim().slice(0, 70), width:box.width, height:box.height}];
    })"""
    contrast_check = """() => {
      const rgb = value => value.match(/[\\d.]+/g).slice(0, 3).map(Number);
      const luminance = values => values.map(v => {
        v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;
      }).reduce((sum,v,i) => sum + v * [.2126,.7152,.0722][i], 0);
      return [...document.querySelectorAll(
        'h1, h2, .muted, .field-note, .primary:not(:disabled), .status, .nav-item'
      )].filter(element => element.getClientRects().length).flatMap(element => {
        let parent = element, bg;
        while (parent) {
          bg = getComputedStyle(parent).backgroundColor;
          if (bg !== 'transparent' && bg !== 'rgba(0, 0, 0, 0)') break;
          parent = parent.parentElement;
        }
        if (!parent) return [];
        const a = luminance(rgb(getComputedStyle(element).color)), b = luminance(rgb(bg));
        const contrast = (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
        return contrast >= 4.5 ? [] : [{text:element.textContent.trim().slice(0,70), contrast}];
      });
    }"""
    for width in (320, 390, 768, 1024, 1440):
        admin.set_viewport_size({"width": width, "height": 900})
        for path, name in routes:
            admin.goto(origin + path)
            assert admin.locator("h1").count() == 1, (width, name, "Page title")
            assert not admin.evaluate("document.documentElement.scrollWidth > innerWidth"), (width, name, "Horizontal overflow")
            assert not admin.evaluate(target_check), (width, name, admin.evaluate(target_check))
            assert not admin.evaluate(contrast_check), (width, name, admin.evaluate(contrast_check))
            if width == 390 and name in ("account", "bot", "invitations"):
                admin.screenshot(path=str(output / ("mobile-" + name + ".png")), full_page=True)
        admin.goto(origin + "/")
        # An account label is capped at 80 characters and must wrap safely even
        # without word boundaries. This changes only the synthetic rendered DOM.
        admin.locator(".account-name strong").first.evaluate("element => element.textContent = 'A'.repeat(80)")
        assert not admin.evaluate("document.documentElement.scrollWidth > innerWidth"), (width, "Long account label")

    mobile = browser.new_context(ignore_https_errors=True, is_mobile=True, has_touch=True,
                                 viewport={"width": 390, "height": 844},
                                 storage_state=admin.context.storage_state())
    mobile.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
    page = mobile.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(origin + "/")
        menu = page.locator("#workspace-navigation")
        toggle = page.locator("#nav-toggle")
        expect(menu).to_be_hidden()
        toggle.tap()
        expect(menu).to_be_visible()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        assert page.locator("#workspace-main").evaluate("element => element.inert")
        expect(page.locator("#nav-close")).to_be_focused()
        assert not page.evaluate(target_check), page.evaluate(target_check)
        assert not page.evaluate(contrast_check), page.evaluate(contrast_check)
        menu.get_by_role("button", name="Sign out").focus()
        page.keyboard.press("Tab")
        expect(menu.locator(".brand")).to_be_focused()
        page.keyboard.press("Shift+Tab")
        expect(menu.get_by_role("button", name="Sign out")).to_be_focused()
        page.keyboard.press("Escape")
        expect(menu).to_be_hidden()
        expect(toggle).to_be_focused()
        assert not page.locator("#workspace-main").evaluate("element => element.inert")
        toggle.tap()
        page.screenshot(path=str(output / "mobile-navigation.png"), full_page=True)
        page.locator("#nav-backdrop").tap(position={"x": 375, "y": 400})
        expect(menu).to_be_hidden()
        toggle.tap()
        menu.get_by_role("link", name="Telegram bot", exact=True).tap()
        page.wait_for_url(origin + "/bot")
        expect(page.get_by_role("heading", name="Telegram bot", exact=True)).to_be_visible()
        expect(page.locator("#nav-toggle")).to_have_attribute("aria-expanded", "false")
        page.locator("#nav-toggle").tap()
        page.set_viewport_size({"width": 1024, "height": 768})
        expect(page.locator("#nav-toggle")).to_be_hidden()
        expect(page.locator("#workspace-navigation")).to_be_visible()
        assert not page.locator("#workspace-main").evaluate("element => element.inert")
        expect(page.locator("#nav-backdrop")).to_be_hidden()
        page.set_viewport_size({"width": 844, "height": 390})
        expect(page.locator("#nav-toggle")).to_be_visible()
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        assert not errors, errors
    finally:
        mobile.close()

    # Sign-in uses a fresh context so this cannot log out the administrator.
    guest = browser.new_context(ignore_https_errors=True, viewport={"width": 320, "height": 700})
    guest.route("**/*", lambda route: route.continue_() if route.request.url.startswith(origin + "/") else route.abort())
    try:
        login = guest.new_page()
        login.goto(origin + "/login")
        assert not login.evaluate("document.documentElement.scrollWidth > innerWidth")
        assert not login.evaluate(target_check), login.evaluate(target_check)
        assert not login.evaluate(contrast_check), login.evaluate(contrast_check)
        login.screenshot(path=str(output / "mobile-login.png"), full_page=True)
    finally:
        guest.close()
