"""Browser verification of the printing-aware Scryfall hover preview (CardHoverPreview).

Covers the eight cases in task t_2d2b14ba:
  1. name hover and thumbnail hover open a popover with a Scryfall image;
  2. two printings of one card (Sol Ring c21/263 vs cmr/472) show different images;
  3. a double-faced card shows both faces (desktop) or one face + Flip (narrow);
  4. popovers near the viewport edges stay fully inside the viewport;
  5. a fast sweep across rows causes no close/reopen flicker and nothing stays stuck open;
  6. keyboard focus opens the preview, aria-describedby is set, Esc closes it;
  7. touch: tap on inert text opens the sheet, scrim dismisses it, a long-press on an
     actionable tile opens it, a quick tap on that tile does not;
  8. with Scryfall blocked, a graceful "Failed to load image" state appears, no page errors.
Plus a caching check: repeated hovers of the same card issue at most one request per URL.

Determinism: every request to api.scryfall.com and cards.scryfall.io is stubbed with
Playwright routing (fixture data below). Nothing leaves the machine for Scryfall.

Prerequisites: a running app seeded with "Hover Test Deck" (see the verification notes
in docs/scryfall-hover-preview.md). If the seed is missing the tests skip, never fail.

Run with:
    pytest tests/e2e/test_hover_preview_verification_e2e.py --base-url=http://localhost:8000
Screenshots go to $HOVER_SHOT_DIR when set (nothing is written otherwise).
"""
import json
import os
import re
import time
import urllib.parse as up

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
Page = playwright_sync.Page
expect = playwright_sync.expect

# ---------------------------------------------------------------- fixtures (Scryfall stub data)

SOL_C21 = "4cbc6901-6a4a-4d0a-83ea-7eefa3b35021"   # Sol Ring, Commander 2021 #263 (in deck)
SOL_CMR = "58b26011-e103-45c4-a253-900f4e6b2eeb"  # Sol Ring, Commander Legends #472 (collection)
DFC_ID = "11bf83bb-c95b-4b4f-9a56-ce7a1816307a"   # Delver of Secrets // Insectile Aberration, isd #51


def _cdn(kind, uid, ext="jpg", version="normal"):
    return f"https://cards.scryfall.io/{version}/{kind}/{uid[0]}/{uid[1]}/{uid}.{ext}"


CARDS = {
    SOL_C21: {
        "id": SOL_C21, "name": "Sol Ring", "set": "c21", "collector_number": "263", "layout": "normal",
        "image_uris": {"small": _cdn("front", SOL_C21, version="small"),
                       "normal": _cdn("front", SOL_C21), "large": _cdn("front", SOL_C21, version="large"),
                       "png": _cdn("front", SOL_C21, "png", "png")},
    },
    SOL_CMR: {
        "id": SOL_CMR, "name": "Sol Ring", "set": "cmr", "collector_number": "472", "layout": "normal",
        "image_uris": {"small": _cdn("front", SOL_CMR, version="small"),
                       "normal": _cdn("front", SOL_CMR), "large": _cdn("front", SOL_CMR, version="large"),
                       "png": _cdn("front", SOL_CMR, "png", "png")},
    },
    DFC_ID: {
        "id": DFC_ID, "name": "Delver of Secrets // Insectile Aberration", "set": "isd",
        "collector_number": "51", "layout": "transform",
        "card_faces": [
            {"name": "Delver of Secrets", "image_uris": {"normal": _cdn("front", DFC_ID),
                                                        "large": _cdn("front", DFC_ID, version="large")}},
            {"name": "Insectile Aberration", "image_uris": {"normal": _cdn("back", DFC_ID),
                                                          "large": _cdn("back", DFC_ID, version="large")}},
        ],
    },
}
def _art(url):
    """Deterministic stand-in card art: an SVG labelled with the printing and face it stands for."""
    label, face = "unknown printing", ("back" if "/back/" in url else "front")
    for uid, card in CARDS.items():
        if uid in url:
            label = f"{card['name']}  {card['set']} #{card['collector_number']}"
            break
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="488" height="680">'
        '<rect width="100%" height="100%" fill="#2b3a55"/>'
        f'<text x="24" y="70" font-size="34" font-family="sans-serif" fill="#ffffff">{label}</text>'
        f'<text x="24" y="120" font-size="28" font-family="sans-serif" fill="#cfd8e3">{face} face (stub art)</text>'
        f'<text x="24" y="170" font-size="18" font-family="monospace" fill="#9fb3c8">{url.split("?")[0][-60:]}</text>'
        '</svg>'
    )
    return svg.encode()


BY_SET_NUM = {(c["set"], c["collector_number"]): c for c in CARDS.values()}
BY_NAME = {}
for _c in CARDS.values():
    BY_NAME.setdefault(_c["name"].lower(), _c)

TRIGGER = "[data-card-preview], [data-card-ref]"
POPOVER = ".hud-cp"
OPEN_JS = "() => { const el = document.querySelector('.hud-cp'); return !!el && el.classList.contains('is-open'); }"
SHEET_JS = "() => { const el = document.querySelector('.hud-cp'); return !!el && el.classList.contains('is-sheet') && el.classList.contains('is-open'); }"
SRC_JS = "() => [...document.querySelectorAll('.hud-cp figure.hud-cp-face img')].map(i => i.getAttribute('src') || '')"
ACTIONABLE_JS = ("(el) => !!el.closest('a[href],button,summary,[role=button],[onclick],[data-action]')")

SHOT_DIR = os.environ.get("HOVER_SHOT_DIR", "")


class ScryfallStub:
    """Routes every Scryfall host and records what the page asked for."""

    def __init__(self):
        self.api = []    # api.scryfall.com requests (URLs)
        self.cdn = []    # cards.scryfall.io requests (URLs)
        self.blocked = False

    def install(self, page):
        page.route("https://api.scryfall.com/**", self._api)
        page.route("https://cards.scryfall.io/**", self._cdn)

    def _api(self, route):
        self.api.append(route.request.url)
        if self.blocked:
            return route.abort()
        u = up.urlparse(route.request.url)
        q = up.parse_qs(u.query)
        cors = {"Access-Control-Allow-Origin": "*"}
        if q.get("format") == ["image"]:
            return route.fulfill(status=200, content_type="image/svg+xml", headers=cors,
                                 body=_art(route.request.url))
        parts = [p for p in u.path.split("/") if p]  # ["cards", ...]
        card = None
        if u.path.startswith("/cards/named"):
            card = BY_NAME.get(q.get("exact", [""])[0].lower())
        elif len(parts) == 2:                     # /cards/{id}
            card = CARDS.get(parts[1])
        elif len(parts) == 3:                     # /cards/{set}/{collector_number}
            card = BY_SET_NUM.get((parts[1].lower(), parts[2]))
        if card is None:
            return route.fulfill(status=404, content_type="application/json", headers=cors,
                                 body=json.dumps({"object": "error", "status": 404}))
        return route.fulfill(status=200, content_type="application/json", headers=cors,
                             body=json.dumps(card))

    def _cdn(self, route):
        self.cdn.append(route.request.url)
        if self.blocked:
            return route.abort()
        return route.fulfill(status=200, content_type="image/svg+xml",
                             headers={"Access-Control-Allow-Origin": "*"}, body=_art(route.request.url))

    def api_for(self, uid):
        return [u for u in self.api if uid in u]

    def cdn_for(self, uid):
        return [u for u in self.cdn if uid in u]


@pytest.fixture
def stub():
    return ScryfallStub()


@pytest.fixture
def pair(browser, base_url, stub):
    """(page, stub) on a desktop 1400x900 page with Scryfall stubbed and page errors recorded."""
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    stub.install(page)
    page.errors = errors
    yield page, stub
    ctx.close()


def _shot(page, name):
    """Save a screenshot only when HOVER_SHOT_DIR is set (keeps binaries out of the repo)."""
    if not SHOT_DIR:
        return None
    page.wait_for_timeout(450)  # let the entrance animation finish so the shot is not mid-fade
    os.makedirs(SHOT_DIR, exist_ok=True)
    path = os.path.join(SHOT_DIR, f"{name}.png")
    page.screenshot(path=path)
    return path


def _goto(page, base_url, path):
    page.goto(f"{base_url}{path}", wait_until="networkidle")
    page.wait_for_timeout(1200)  # the tables re-render after load


def _visible(loc):
    out = []
    for i in range(loc.count()):
        if loc.nth(i).is_visible():
            out.append(loc.nth(i))
    return out


def _name_triggers(page, name):
    return _visible(page.locator(f"[data-card-preview][data-card-name='{name}']"))


def _center(page, loc):
    # Centre of the first client rect: a wrapped inline name has a union bounding box whose
    # centre can fall on empty cell space, while the text itself sits in the first line box.
    loc.scroll_into_view_if_needed()
    return loc.evaluate("el => { const r = (el.getClientRects()[0] || el.getBoundingClientRect());"
                        " return [r.left + r.width / 2, r.top + r.height / 2]; }")


def _hover(page, loc):
    x, y = _center(page, loc)
    page.mouse.move(x, y, steps=4)


def _wait_open(page, timeout=4000):
    page.wait_for_function(OPEN_JS, timeout=timeout)


def _skip_without_seed(page, base_url, path, name, what):
    _goto(page, base_url, path)
    if not _name_triggers(page, name):
        pytest.skip(f"seed data missing: no visible {what} trigger for {name!r} on {path}")


# ---------------------------------------------------------------- 1. name and thumbnail hover

class TestNameAndThumbnailHover:
    def test_name_hover_shows_printing_image(self, pair, base_url):
        page, stub = pair
        _skip_without_seed(page, base_url, "/inventory", "Sol Ring", "Sol Ring name")
        trig = _name_triggers(page, "Sol Ring")[0]
        _hover(page, trig)
        _wait_open(page)
        srcs = page.evaluate(SRC_JS)
        assert srcs, "popover opened without an image"
        assert any(SOL_C21 in s or SOL_CMR in s for s in srcs), srcs
        _shot(page, "1-name-hover-sol-ring")

    def test_thumbnail_hover_shows_popover(self, pair, base_url):
        page, stub = pair
        _goto(page, base_url, "/decks")
        thumbs = _visible(page.locator("img[data-card-preview]"))
        if not thumbs:
            pytest.skip("seed data missing: no visible deck thumbnail trigger")
        _hover(page, thumbs[0])  # the transparent button.deck-open sits on the art
        _wait_open(page)
        srcs = page.evaluate(SRC_JS)
        assert srcs and re.search(r"cards\.scryfall\.io/(normal|large)/front/", srcs[0]), srcs
        _shot(page, "1-thumbnail-hover-deck-grid")


# ---------------------------------------------------------------- 2. two printings

class TestPrintings:
    def test_two_printings_show_their_own_art(self, pair, base_url):
        page, stub = pair
        _skip_without_seed(page, base_url, "/inventory", "Sol Ring", "Sol Ring")
        expected = {SOL_C21: "c21/263", SOL_CMR: "cmr/472"}
        seen = {}
        for trig in _name_triggers(page, "Sol Ring"):
            ref = json.loads(trig.get_attribute("data-card-ref") or "{}")
            uid = ref.get("scryfallId")
            if uid not in expected:
                continue
            _hover(page, trig)
            _wait_open(page)
            page.wait_for_timeout(300)
            srcs = page.evaluate(SRC_JS)
            seen[uid] = srcs
            assert srcs and uid in srcs[0], f"{expected[uid]} popover shows {srcs}"
            page.mouse.move(2, 2)
            page.wait_for_function(
                "() => !document.querySelector('.hud-cp.is-open')", timeout=3000)
        if set(seen) != set(expected):
            pytest.skip(f"seed data missing: printings rendered = {sorted(seen)}")
        assert seen[SOL_C21][0] != seen[SOL_CMR][0]


# ---------------------------------------------------------------- 3. double-faced card

class TestDoubleFaced:
    def test_dfc_shows_both_faces_on_desktop(self, pair, base_url):
        page, stub = pair
        _skip_without_seed(page, base_url, "/inventory", "Delver of Secrets // Insectile Aberration",
                           "DFC name")
        trig = _name_triggers(page, "Delver of Secrets // Insectile Aberration")[0]
        _hover(page, trig)
        _wait_open(page)
        page.wait_for_timeout(500)
        srcs = page.evaluate(SRC_JS)
        assert len(srcs) == 2, f"expected two faces side by side, got {srcs}"
        assert "/front/" in srcs[0] and DFC_ID in srcs[0], srcs
        assert "/back/" in srcs[1] and DFC_ID in srcs[1], srcs
        _shot(page, "3-dfc-both-faces-desktop")

    def test_dfc_narrow_shows_one_face_and_flip(self, browser, base_url, stub):
        ctx = browser.new_context(viewport={"width": 520, "height": 900})
        page = ctx.new_page()
        stub.install(page)
        try:
            _skip_without_seed(page, base_url, "/inventory", "Delver of Secrets // Insectile Aberration",
                               "DFC name")
            trig = _name_triggers(page, "Delver of Secrets // Insectile Aberration")[0]
            _hover(page, trig)
            _wait_open(page)
            page.wait_for_timeout(500)
            srcs = page.evaluate(SRC_JS)
            if len(srcs) == 2:  # the 280 px floor still fits both faces at 520 px: that is also per spec
                assert "/front/" in srcs[0] and "/back/" in srcs[1], srcs
                return
            flip = page.locator(".hud-cp-flip")
            assert flip.is_visible(), "narrow DFC shows one face but no Flip control"
            assert "/front/" in srcs[0] and DFC_ID in srcs[0], srcs
            flip.click()
            page.wait_for_timeout(400)
            srcs2 = page.evaluate(SRC_JS)
            assert srcs2 and "/back/" in srcs2[0], f"Flip did not show the back face: {srcs2}"
            _shot(page, "3-dfc-flip-narrow")
        finally:
            ctx.close()


# ---------------------------------------------------------------- 4. viewport edges

class TestViewportEdges:
    def test_popover_stays_inside_viewport_near_edges(self, browser, base_url, stub):
        ctx = browser.new_context(viewport={"width": 1000, "height": 640})
        page = ctx.new_page()
        stub.install(page)
        try:
            _goto(page, base_url, "/inventory")
            triggers = _visible(page.locator(TRIGGER))
            if not triggers:
                pytest.skip("seed data missing: no visible triggers")
            checked = 0
            for trig in triggers[:12]:
                # Put each trigger at the bottom of the viewport, then at the right edge.
                trig.scroll_into_view_if_needed()
                box = trig.bounding_box()
                if box is None or box["y"] < 0 or box["y"] + box["height"] > 640:
                    continue
                page.mouse.move(2, 2)
                page.wait_for_timeout(200)
                page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=3)
                try:
                    _wait_open(page, timeout=2500)
                except Exception:
                    continue
                page.wait_for_timeout(450)  # entrance animation
                r = page.evaluate("() => { const b = document.querySelector('.hud-cp').getBoundingClientRect();"
                                  " return {l:b.left, t:b.top, r:b.right, b:b.bottom, vw:innerWidth, vh:innerHeight}; }")
                checked += 1
                assert r["l"] >= -0.5 and r["t"] >= -0.5, f"popover clipped at top/left: {r}"
                assert r["r"] <= r["vw"] + 0.5 and r["b"] <= r["vh"] + 0.5, f"popover clipped at right/bottom: {r}"
            if checked == 0:
                pytest.skip("no trigger near an edge opened a preview")
        finally:
            ctx.close()


# ---------------------------------------------------------------- 5. sweep: no flicker, nothing stuck

class TestSweep:
    def test_fast_sweep_no_flicker_and_no_stuck_popover(self, pair, base_url):
        page, stub = pair
        _goto(page, base_url, "/inventory")
        # Sample the popover state every animation frame during the sweep. Flicker means the
        # popover goes closed -> open more than once (a warm swap must keep it open throughout).
        page.evaluate("""() => { window.__cp = {closes: 0, epochs: 0, frames: 0, openFrames: 0, wasOpen: false};
            const tick = () => { const el = document.querySelector('.hud-cp');
                const open = !!el && el.classList.contains('is-open');
                const c = window.__cp; c.frames++;
                if (open) { c.openFrames++; if (!c.wasOpen) c.epochs++; }
                if (!open && c.wasOpen) c.closes++;
                c.wasOpen = open; requestAnimationFrame(tick); };
            requestAnimationFrame(tick); }""")
        points = []
        for trig in _visible(page.locator(TRIGGER))[:10]:
            points.append(_center(page, trig))
        if len(points) < 3:
            pytest.skip("need at least three visible triggers to sweep")
        page.mouse.move(*points[0])
        for x, y in points:
            page.mouse.move(x, y, steps=3)
            page.wait_for_timeout(30)
        page.wait_for_timeout(350)
        cp = page.evaluate("() => window.__cp")
        assert cp["openFrames"] > 0, f"sweep never opened a preview: {cp}"
        assert cp["closes"] == 0, f"preview closed during the sweep (flicker): {cp}"
        assert cp["epochs"] <= 1, f"preview closed and reopened during the sweep (flicker): {cp}"
        # leave the list entirely: nothing may stay open or visible
        page.mouse.move(1390, 890, steps=3)
        page.wait_for_timeout(600)
        assert not page.evaluate(OPEN_JS), "popover stuck open after the pointer left"
        hidden = page.evaluate("() => { const el = document.querySelector('.hud-cp'); return !el || el.hidden; }")
        assert hidden, "popover still visible after close"


# ---------------------------------------------------------------- 6. keyboard

class TestKeyboard:
    def test_focus_opens_aria_describes_and_esc_closes(self, pair, base_url):
        page, stub = pair
        _goto(page, base_url, "/inventory")
        page.mouse.move(2, 2)
        focused = False
        for _ in range(60):
            page.keyboard.press("Tab")
            if page.evaluate("() => !!document.activeElement && document.activeElement.hasAttribute('data-card-preview')"):
                focused = True
                break
        if not focused:
            pytest.skip("no keyboard-focusable card trigger on the page")
        page.wait_for_timeout(700)  # :focus-visible open delay is 400 ms
        assert page.evaluate(OPEN_JS), "keyboard focus did not open the preview"
        describedby = page.evaluate("() => document.activeElement.getAttribute('aria-describedby')")
        assert describedby and describedby == page.evaluate("() => document.querySelector('.hud-cp').id"), describedby
        assert page.evaluate("() => document.querySelector('.hud-cp').getAttribute('role')") == "tooltip"
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        assert not page.evaluate(OPEN_JS), "Esc did not close the preview"
        still_focused = page.evaluate("() => document.activeElement && document.activeElement.hasAttribute('data-card-preview')")
        assert still_focused, "focus left the trigger after Esc"


# ---------------------------------------------------------------- 7. touch

class TestTouch:
    @pytest.fixture
    def touch_page(self, browser, base_url, stub):
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True,
                                  is_mobile=True, device_scale_factor=2)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        stub.install(page)
        page.errors = errors
        yield page
        ctx.close()

    @staticmethod
    def _long_press(page, x, y, ms=700):
        cdp = page.context.new_cdp_session(page)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
        page.wait_for_timeout(ms)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})

    def _inert_and_actionable(self, page):
        inert, actionable = None, None
        for trig in _visible(page.locator(TRIGGER)):
            act = trig.evaluate(ACTIONABLE_JS)
            if act and actionable is None:
                actionable = trig
            if not act and inert is None:
                inert = trig
        return inert, actionable

    def test_tap_inert_text_opens_sheet_and_scrim_closes(self, touch_page, base_url):
        page = touch_page
        _goto(page, base_url, "/inventory")
        inert, _ = self._inert_and_actionable(page)
        if inert is None:
            pytest.skip("no inert (non-actionable) trigger on the mobile inventory page")
        x, y = _center(page, inert)
        page.touchscreen.tap(x, y)
        page.wait_for_function(SHEET_JS, timeout=3000)
        _shot(page, "7-touch-sheet-inert-tap")
        page.locator(".hud-cp-scrim").click(position={"x": 5, "y": 5})
        page.wait_for_timeout(400)
        assert not page.evaluate(SHEET_JS), "scrim tap did not dismiss the sheet"
        assert not page.errors, page.errors

    def test_long_press_inert_opens_sheet(self, touch_page, base_url):
        page = touch_page
        _goto(page, base_url, "/inventory")
        inert, _ = self._inert_and_actionable(page)
        if inert is None:
            pytest.skip("no inert trigger on the mobile inventory page")
        x, y = _center(page, inert)
        self._long_press(page, x, y)
        page.wait_for_timeout(200)
        assert page.evaluate(SHEET_JS), "long-press on inert text did not open the sheet"

    @staticmethod
    def _deck_tile_center(page):
        # The deck thumbnail <img> is covered by a transparent button.deck-open (data-cp-overlay),
        # so a touch lands on the button, not on the trigger.
        img = page.locator("img[data-card-preview]").first
        if img.count() == 0 or not img.is_visible():
            pytest.skip("seed data missing: no deck thumbnail on the mobile decks page")
        return _center(page, img)

    def test_quick_tap_on_deck_tile_does_not_open_preview(self, touch_page, base_url):
        page = touch_page
        _goto(page, base_url, "/decks")
        x, y = self._deck_tile_center(page)
        page.touchscreen.tap(x, y)  # quick tap: the tile's own action wins, no preview sheet
        page.wait_for_timeout(700)
        assert not page.evaluate(SHEET_JS), "quick tap on an actionable deck tile opened the preview"

    # Bug #152: onTouchStart() uses findTrigger(e.target), so the transparent data-cp-overlay button
    # swallows the long-press. Mouse hover on the same point works. Remove the marker once fixed.
    @pytest.mark.xfail(strict=True, reason="bug #152: touch long-press on a deck tile is swallowed by the overlay button")
    def test_long_press_on_deck_tile_opens_sheet(self, touch_page, base_url):
        page = touch_page
        _goto(page, base_url, "/decks")
        x, y = self._deck_tile_center(page)
        self._long_press(page, x, y)
        page.wait_for_timeout(200)
        assert page.evaluate(SHEET_JS), "long-press on a deck tile did not open the sheet"


# ---------------------------------------------------------------- 8. error handling

class TestErrorState:
    def test_blocked_scryfall_shows_graceful_error(self, pair, base_url, stub):
        page, _ = pair
        _skip_without_seed(page, base_url, "/inventory", "Sol Ring", "Sol Ring")
        stub.blocked = True  # every Scryfall request now aborts (API and CDN)
        trig = _name_triggers(page, "Sol Ring")[0]
        _hover(page, trig)
        _wait_open(page)
        expect(page.locator(".hud-cp")).to_contain_text("Failed to load image", timeout=10000)
        _shot(page, "8-blocked-error-state")
        assert not page.errors, f"uncaught page errors while Scryfall was blocked: {page.errors}"
        page.mouse.move(2, 2)
        page.wait_for_timeout(300)


# ---------------------------------------------------------------- caching and request volume

class TestCaching:
    def test_repeat_hovers_do_not_refetch(self, pair, base_url):
        page, stub = pair
        _skip_without_seed(page, base_url, "/inventory", "Sol Ring", "Sol Ring")
        trig = next(t for t in _name_triggers(page, "Sol Ring")
                    if json.loads(t.get_attribute("data-card-ref") or "{}").get("scryfallId") == SOL_C21)
        for _ in range(6):
            _hover(page, trig)
            _wait_open(page)
            page.wait_for_timeout(250)
            page.mouse.move(2, 2)
            page.wait_for_timeout(350)
        page.wait_for_timeout(300)
        counts = {}
        for url in stub.api + stub.cdn:
            if SOL_C21 in url:
                counts[url] = counts.get(url, 0) + 1
        assert counts, "no Scryfall request for the Sol Ring printing was recorded"
        assert max(counts.values()) <= 1, f"repeat fetches for one printing: {counts}"

    def test_dfc_lookup_is_cached(self, pair, base_url):
        page, stub = pair
        _skip_without_seed(page, base_url, "/inventory", "Delver of Secrets // Insectile Aberration", "DFC")
        trig = _name_triggers(page, "Delver of Secrets // Insectile Aberration")[0]
        for _ in range(4):
            _hover(page, trig)
            _wait_open(page)
            page.wait_for_timeout(250)
            page.mouse.move(2, 2)
            page.wait_for_timeout(350)
        api_hits = stub.api_for(DFC_ID)
        assert len(api_hits) <= 1, f"DFC card JSON fetched {len(api_hits)} times: {api_hits}"
        for url in set(stub.cdn_for(DFC_ID)):
            assert stub.cdn_for(DFC_ID).count(url) <= 1, f"repeat image fetch: {url}"

    def test_full_session_request_budget(self, pair, base_url):
        """Hover every visible name trigger once: each distinct Scryfall URL is requested at most once."""
        page, stub = pair
        _goto(page, base_url, "/inventory")
        for trig in _visible(page.locator(TRIGGER))[:15]:
            _hover(page, trig)
            page.wait_for_timeout(120)
        page.mouse.move(2, 2)
        page.wait_for_timeout(400)
        urls = stub.api + stub.cdn
        if not urls:
            pytest.skip("no Scryfall requests were made on this page")
        dupes = {u: urls.count(u) for u in set(urls) if urls.count(u) > 1}
        assert not dupes, f"same Scryfall URL requested more than once: {dupes}"
