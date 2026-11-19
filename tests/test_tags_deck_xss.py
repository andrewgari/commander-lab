"""Browser regressions for stored tag/card/deck data in the legacy UI."""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

playwright = pytest.importorskip('playwright.sync_api')
ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'src/web'
PAYLOAD = '\"\'><img src=x onerror="window.xss=1"> & \\ name'


def render(name):
    env = Environment(loader=FileSystemLoader(WEB / 'templates'), autoescape=select_autoescape(['html']))
    request = SimpleNamespace(url=SimpleNamespace(path='/' + name.split('.')[0]),
                              query_params={}, state=SimpleNamespace(app_version='test'))
    return env.get_template(name).render(request=request, version='test', providers=['archidekt', 'moxfield'],
                                         status_filter=None, status_label=None, deck_groups=[])


@pytest.fixture
def browser():
    with playwright.sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True, executable_path=shutil.which('chromium'))
        except Exception as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        yield b
        b.close()


def test_tags_data_is_text_and_edit_remove_keep_original_values(browser):
    page = browser.new_page()
    calls = []
    card_name = PAYLOAD + "'"
    lab = {'tag': PAYLOAD, 'source': PAYLOAD}
    reference = {'tag': PAYLOAD, 'count': PAYLOAD, 'total_decks': 1}
    inventory = [{'name': card_name, 'deck': 'D', 'categories': [PAYLOAD]}]

    def route(r):
        path = urlparse(r.request.url).path
        if path == '/tags':
            return r.fulfill(body=render('tags.html'), content_type='text/html')
        if path.startswith('/static/'):
            f = WEB / path.lstrip('/')
            return r.fulfill(body=f.read_text() if f.is_file() else '', content_type='application/javascript' if path.endswith('.js') else 'text/css')
        if path == '/api/tags': data = {'tags': [PAYLOAD]}
        elif path == '/api/tags/core': data = {'core_tags': [PAYLOAD]}
        elif path == '/api/inventory': data = {'inventory': inventory}
        elif path.startswith('/api/card/'):
            if r.request.method == 'POST':
                calls.append(json.loads(r.request.post_data))
                data = {'success': True}
            else: data = {'lab_tags': [lab], 'archidekt_tags': [reference]}
        elif path == '/api/autotag-tags': data = {'tags': []}
        elif path == '/api/sim/focus-chips': data = {'chips': []}
        else: return r.abort()
        r.fulfill(json=data)

    page.add_init_script('window.xss = 0')
    page.route('**/*', route)
    page.goto('http://test.local/tags')
    page.wait_for_function("document.querySelectorAll('#cardList tr').length === 1")
    assert page.locator('#cardList .hud-cp-name').inner_text() == card_name
    inventory.append({'name': '__proto__', 'deck': 'D', 'categories': []})
    page.evaluate('fetchCards()')
    page.wait_for_function("document.querySelectorAll('#cardList tr').length === 2")
    assert '__proto__' in page.locator('#cardList .hud-cp-name').all_text_contents()
    row = page.locator('#cardList tr').filter(has_text=card_name)
    assert PAYLOAD in row.locator('.card-tags').inner_text()
    row.locator('button').click()
    page.wait_for_function("document.querySelectorAll('#currentTagsList .tag-badge').length === 2")
    assert page.locator('#editCardName').inner_text() == card_name
    assert PAYLOAD in page.locator('#currentTagsList').inner_text()
    page.on('dialog', lambda dialog: dialog.accept())
    page.locator('#currentTagsList .tag-badge').first.click()
    page.wait_for_function("document.querySelector('#tagEditor').classList.contains('active')")
    assert calls == [{'action': 'remove', 'tag': PAYLOAD}]
    assert page.locator('#cardList img, #currentTagsList img').count() == 0
    assert page.evaluate('window.xss') == 0
    page.close()


def test_deck_quick_view_and_selection_escape_stored_values(browser):
    page = browser.new_page()
    deck = {'id': PAYLOAD, 'moxfield_id': PAYLOAD, 'name': PAYLOAD, 'status': 'physical',
            'commanders': [PAYLOAD], 'folder': PAYLOAD, 'color': '\" onerror=alert(1)'}

    def route(r):
        path = urlparse(r.request.url).path
        if path == '/decks': return r.fulfill(body=render('decks.html'), content_type='text/html')
        if path.startswith('/static/'):
            f = WEB / path.lstrip('/')
            return r.fulfill(body=f.read_text() if f.is_file() else '', content_type='application/javascript' if path.endswith('.js') else 'text/css')
        if path == '/api/decks': return r.fulfill(json={'decks': [deck]})
        if path == '/api/transcribe/moxfield': return r.fulfill(json={'eligible': 0})
        return r.abort()

    page.add_init_script('window.xss = 0')
    page.route('**/*', route)
    page.goto('http://test.local/decks')
    page.wait_for_selector('.deck-open')
    page.locator('.deck-open').click(force=True)
    assert page.locator('#modalDeckName a').inner_text() == PAYLOAD
    assert page.locator('#modalDeckName img').count() == 0
    assert page.locator('#modalCommander').inner_text() == PAYLOAD
    assert page.locator('#modalFolderName').inner_text() == PAYLOAD
    assert page.locator('#modalLinksContainer [onerror]').count() == 0
    for link in page.locator('#modalLinksContainer a').all():
        assert urlparse(link.get_attribute('href')).hostname in (None, 'edhrec.com', 'archidekt.com', 'moxfield.com')
    assert page.locator('#modalColors [onerror]').count() == 0
    page.evaluate('closeModal()')
    page.evaluate('toggleSelectMode(true)')
    checkbox = page.locator('.deck-pick')
    assert checkbox.get_attribute('data-id') == PAYLOAD
    checkbox.check(force=True)
    assert page.evaluate('[...selectedDeckIds]') == [PAYLOAD]
    page.evaluate("document.querySelector('.deck-card').click()")
    assert page.evaluate('[...selectedDeckIds]') == []
    assert page.evaluate('window.xss') == 0
    page.close()


def test_deck_card_modal_and_list_escape_card_and_tag_data(browser):
    page = browser.new_page()
    page.set_content('<h2 id="modalCardName"></h2><div id="modalCardArt"></div><div id="modalCardInfo"></div><div id="list"></div>')
    page.add_script_tag(content=(WEB / 'static/js/hud.js').read_text())
    template = (WEB / 'templates/deck.html').read_text()
    modal = template[template.index('    function openCardModal(index)'):template.index('    async function postTag(')]
    table = template[template.index('    function renderTable(groups)'):template.index('    function setDecklistView(')]
    card = {'name': PAYLOAD, 'uid': PAYLOAD, 'type': PAYLOAD, 'set_name': PAYLOAD,
            'set': PAYLOAD, 'oracle_text': PAYLOAD, 'cmc': PAYLOAD, 'color': 'U'}
    page.add_script_tag(content=f'''
        window.xss = 0; window.removed = null;
        const inventoryCards = [{json.dumps(card)}], GROUP_ORDER = ['Other'], AT = null;
        const esc = s => HUD.esc(s), isLand = c => false, refOf = c => HUD.cardRef(c);
        const refAttr = c => esc(HUD.cardRefAttrs(refOf(c))['data-card-ref']);
        const flagOf = c => null, reviewActionRow = () => '';
        HUD.openModal = () => {{}};
        window.fetch = async () => ({{json: async () => ({{lab_tags: [{{tag: {json.dumps(PAYLOAD)}, source: {json.dumps(PAYLOAD)}}}]}})}});
        function removeModalTag(i, tag) {{ window.removed = [i, tag]; }}
        {table}
        {modal}
    ''')
    page.evaluate("document.getElementById('list').innerHTML = renderTable({Other: inventoryCards}); openCardModal(0)")
    page.wait_for_selector('#modalTagsContainer .hud-chip-remove')
    assert page.locator('#modalCardName').inner_text() == PAYLOAD
    assert PAYLOAD in page.locator('#modalCardInfo').inner_text()
    assert PAYLOAD in page.locator('#list').inner_text()
    assert page.locator('#modalCardInfo img, #list img, #modalCardArt img[onerror]').count() == 0
    page.locator('#modalTagsContainer .hud-chip-remove').click()
    assert page.evaluate('window.removed') == [0, PAYLOAD]
    assert page.evaluate('window.xss') == 0
    page.close()
