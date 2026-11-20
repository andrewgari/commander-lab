"""Browser a11y runner for the shared-component fixtures (/dev/components/*) or any other page.

Start the app (npm run dev -- --port 5178, or `PORT=3000 node build`), then:
  uv run --with playwright python src/web/tests/components_a11y.py [--url http://127.0.0.1:5178/dev/components] [--url ...]

For each --url the page itself is checked, plus every link under its own path that returns 200 (the
/dev/components index lists its fixtures; a 404 means the fixture has not landed yet and is skipped).
Every page runs the full axe rule set (contrast included, except under forced-colors) at 1280x800 and 375x812, then again under
forced-colors: active and prefers-reduced-motion: reduce. At 375px it asserts there is no horizontal
page scroll. It asserts nothing inside <main> is opacity-dimmed. Exits non-zero on any failure.
Set CHROMIUM=/usr/bin/chromium to use a system browser."""
import argparse, os, sys
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

AXE = Path(__file__).resolve().parents[1] / "node_modules" / "axe-core" / "axe.min.js"

ap = argparse.ArgumentParser()
ap.add_argument("--url", action="append", default=None,
                help="page to check (repeatable); default is the /dev/components index")
args = ap.parse_args()
urls = args.url or [os.environ.get("A11Y_URL", "http://127.0.0.1:5178/dev/components")]

if not AXE.exists():
    sys.exit(f"axe-core not found at {AXE}; run `npm ci` in src/web")

fails = []
def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok:
        fails.append(msg)

# Under forced-colors the UA replaces every author colour with system colours (Canvas/LinkText/...),
# so axe's colour-contrast reads the stale author palette and reports false positives (seen on the
# layout header, not fixture code). That rule is skipped there; all other rules still run, and
# contrast itself is covered by the default-mode run and tests/test_web_shared_components.py.
RUN_AXE = """async (skipContrast) => {
  const r = await axe.run(document, {
    runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa']},
    rules: skipContrast ? {'color-contrast': {enabled: false}} : {}
  });
  return r.violations.map(v => v.id + ': ' + v.help + ' [' + v.nodes.slice(0, 5).map(n => n.target.join(' ')).join(' | ') + ']');
}"""
DIMMED = """() => [...document.querySelectorAll('main, main *')]
  .filter(e => parseFloat(getComputedStyle(e).opacity) < 1)
  .map(e => e.tagName.toLowerCase() + (typeof e.className === 'string' && e.className ? '.' + e.className.split(' ').join('.') : ''))"""
LINKS = """(prefix) => [...new Set([...document.querySelectorAll('main a[href]')]
  .map(a => a.href).filter(h => new URL(h).pathname.startsWith(prefix)))]"""


def expand(page, url):
    """The url itself plus every 200 link under its path."""
    resp = page.goto(url)
    page.wait_for_load_state("networkidle")
    status = resp.status if resp else None
    check(status == 200, f"{url} loads (HTTP {status})")
    if status != 200:
        return []
    out = [url]
    prefix = urlparse(url).path.rstrip("/") + "/"
    for link in page.evaluate(LINKS, prefix):
        r = page.request.get(link)
        if r.status == 200:
            out.append(link)
        else:
            print(f"SKIP {link} (HTTP {r.status}: fixture not landed yet)")
    return out


def check_scroll_table(page, url):
    """Tab reaches the scroll region, ArrowRight scrolls it, and the sticky first column stays put."""
    tag = f"{url} @375 [scroll-table]"
    page.goto(url)
    page.wait_for_load_state("networkidle")
    region = page.locator('[role="region"][aria-label="Cards to buy"]')
    page.evaluate("document.activeElement && document.activeElement.blur()")
    reached = False
    for _ in range(12):
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement && document.activeElement.getAttribute('aria-label')") == "Cards to buy":
            reached = True
            break
    check(reached, f"{tag}: Tab reaches the scroll region")
    check(region.evaluate("e => e.scrollWidth > e.clientWidth"), f"{tag}: region overflows horizontally at 375")
    left0 = region.evaluate("e => e.scrollLeft")
    sticky0 = page.evaluate("document.querySelector('[aria-label=\"Cards to buy\"] tbody th[scope=row]').getBoundingClientRect().left")
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(300)
    left1 = region.evaluate("e => e.scrollLeft")
    check(left1 > left0, f"{tag}: ArrowRight increases scrollLeft ({left0} -> {left1})")
    region.evaluate("e => { e.scrollLeft = e.scrollWidth }")
    page.wait_for_timeout(300)
    sticky1 = page.evaluate("document.querySelector('[aria-label=\"Cards to buy\"] tbody th[scope=row]').getBoundingClientRect().left")
    check(abs(sticky1 - sticky0) < 1, f"{tag}: sticky first column left unchanged after scroll ({sticky0} -> {sticky1})")
    page.context.close()


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path=os.environ.get("CHROMIUM") or None)

    def new_page(width, height, **ctx):
        context = browser.new_context(viewport={"width": width, "height": height}, **ctx)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        return page, errors

    scout, _ = new_page(1280, 800)
    pages = []
    for u in urls:
        for x in expand(scout, u):
            if x not in pages:
                pages.append(x)
    scout.context.close()

    modes = [
        ("default", {}),
        ("forced-colors", {"forced_colors": "active"}),
        ("reduced-motion", {"reduced_motion": "reduce"}),
    ]
    for mode, ctx in modes:
        for width, height in ((1280, 800), (375, 812)):
            page, errors = new_page(width, height, **ctx)
            for url in pages:
                tag = f"{url} @{width} [{mode}]"
                errors.clear()
                page.goto(url)
                page.wait_for_load_state("networkidle")
                page.add_script_tag(path=str(AXE))
                violations = page.evaluate(RUN_AXE, mode == "forced-colors")
                check(not violations, f"{tag}: axe, no violations {violations or ''}")
                dimmed = page.evaluate(DIMMED)
                check(not dimmed, f"{tag}: no opacity-dimmed element in main {dimmed or ''}")
                if width == 375:
                    sw, iw = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
                    check(sw <= iw, f"{tag}: no horizontal page scroll (scrollWidth {sw} <= innerWidth {iw})")
                check(not errors, f"{tag}: no page errors {errors or ''}")
            page.context.close()
    # ScrollTable behaviour (keyboard scrolling, sticky first column) on its fixture.
    for url in [u for u in pages if u.rstrip("/").endswith("/scroll-table")]:
        check_scroll_table(new_page(375, 812)[0], url)
    browser.close()

print(f"\n{len(pages)} page(s) checked, {len(fails)} failure(s)")
sys.exit(1 if fails else 0)
