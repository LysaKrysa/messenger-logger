"""Real browser checks for the private archive and capture-health surfaces."""
from playwright.sync_api import expect


def verify(page, admin, origin, store, app, row, other, output):
    now = store.clock()
    archive = app.extensions['message_archive']
    base = {'thread_id': '42', 'message_id': 'web-live', 'sender': 'Ada', 'text': 'A fresh incoming note',
            'original_text': 'A fresh incoming note', 'created_ms': now * 1000, 'captured_at': now,
            'deleted': False, 'deleted_at_ms': None, 'last_edit_ms': None, 'conversation': 'Ada', 'attachments': [], 'preview_truncated': False}
    deleted = dict(base, message_id='web-deleted', text='Deleted incoming note', original_text='Deleted incoming note', deleted=True, deleted_at_ms=now * 1000)
    archive.report(row, {'revision': row['revision'], 'messages': [base, deleted,
        dict(base, thread_id='43', message_id='other-conversation', conversation='Project group', text='Group message')]})
    archive.report(other, {'revision': other['revision'], 'messages': [dict(base, text='OTHER_OWNER_ARCHIVE', conversation='OTHER_OWNER_ARCHIVE')]})
    app.extensions['capture_health'].report(row, {'revision': row['revision'], 'started_at': now - 100,
        'last_capture': now, 'counts': {'decryption': 5, 'media': 0, 'queue': 0},
        'recent': {'decryption': 5, 'media': 0, 'queue': 0}, 'last': {'decryption': now, 'media': None, 'queue': None}})
    for width in (390, 768, 1440):
        page.set_viewport_size({'width': width, 'height': 900})
        page.goto(origin + '/me/messages')
        page.locator('.conversation-item').filter(has=page.get_by_text('Ada', exact=True)).click()
        expect(page.get_by_role('heading', name='Message viewer', exact=True)).to_be_visible()
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        assert 'OTHER_OWNER_ARCHIVE' not in page.content()
        assert page.locator('.message-card').count() == 2
        page.screenshot(path=str(output / ('message-viewer-' + str(width) + '.png')), full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.get_by_label('Message status', exact=True).select_option('undeleted')
    page.locator('.message-filters').get_by_role('button', name='Search', exact=True).click()
    assert page.locator('.message-card').count() == 1
    expect(page.get_by_text('A fresh incoming note', exact=True)).to_be_visible()
    page.get_by_label('Message status', exact=True).select_option('deleted')
    page.get_by_label('Search message text or filenames', exact=True).fill('Deleted incoming')
    page.locator('.message-filters').get_by_role('button', name='Search', exact=True).click()
    assert page.locator('.message-card').count() == 1
    expect(page.get_by_text('Deleted incoming note', exact=True)).to_be_visible()
    page.get_by_role('link').filter(has=page.get_by_text('Project group', exact=True)).click()
    expect(page.get_by_text('Group message', exact=True)).to_be_visible()
    assert 'Deleted incoming note' not in page.locator('.message-list').inner_text()
    page.goto(origin + '/me/health')
    expect(page.get_by_role('heading', name='Capture health', exact=True)).to_be_visible()
    expect(page.get_by_text('Capture needs attention', exact=True)).to_be_visible()
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    page.get_by_label('Telegram alerts for capture problems', exact=True).uncheck()
    page.get_by_role('button', name='Save alert preferences', exact=True).click()
    assert not app.extensions['capture_health'].enabled(row)
    assert app.extensions['capture_health'].enabled(other)
    page.screenshot(path=str(output / 'capture-health-mobile.png'), full_page=True)
    admin.goto(origin + '/health')
    expect(admin.get_by_role('heading', name='Capture health', exact=True)).to_be_visible()
    admin.goto(origin + '/accounts/' + row['id'] + '/health')
    expect(admin.get_by_role('heading', name=row['name'], exact=True)).to_be_visible()
    assert not admin.evaluate('document.documentElement.scrollWidth > innerWidth')
    admin.screenshot(path=str(output / 'admin-capture-health.png'), full_page=True)
