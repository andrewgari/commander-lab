"""Browser check of the shared a11y layer at 375px against the /dev/a11y harness.
Start the app (npm run dev -- --port 5178, or `PORT=3000 node build`), then:
  uv run --with playwright python src/web/tests/a11y_shared.py [--url http://127.0.0.1:5178/dev/a11y]
Set CHROMIUM=/usr/bin/chromium to use a system browser."""
import argparse, os, sys
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--url", default=os.environ.get("A11Y_URL", "http://127.0.0.1:5178/dev/a11y"))
args = ap.parse_args()

fails = []
def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok: fails.append(msg)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path=os.environ.get("CHROMIUM") or None)
    page = browser.new_page(viewport={"width": 375, "height": 812})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(args.url); page.wait_for_load_state("networkidle")

    check(page.evaluate("document.documentElement.scrollWidth === window.innerWidth"), "375px: no page-level horizontal scroll")
    for sel, name in (("[role=region] .table-scroll, .table-scroll[role=region]", "component wrapper"), ("[data-testid=class-wrapper]", "class wrapper")):
        r = page.evaluate("""(sel) => { const w = document.querySelector(sel); return [w.scrollWidth, w.clientWidth, getComputedStyle(w).overflowX]; }""", sel)
        check(r[0] > r[1] and r[2] == "auto", f"{name} scrolls internally (scrollWidth {r[0]} > clientWidth {r[1]})")
    region = page.locator(".table-scroll[role=region]").first
    check(region.get_attribute("tabindex") == "0" and bool(region.get_attribute("aria-label")), "scroll region is focusable and named")
    region.focus()
    check(page.evaluate("getComputedStyle(document.activeElement).outlineStyle") == "solid", "focus-visible ring on scroll region")

    box = page.locator("[data-testid=text-link]").bounding_box()
    check(box["width"] >= 44 and box["height"] >= 44, f".text-link target {box['width']:.0f}x{box['height']:.0f} >= 44x44")

    dimmed = page.evaluate("""() => [...document.querySelectorAll('[data-testid=charts] *')].filter(e => parseFloat(getComputedStyle(e).opacity) < 1).length""")
    check(dimmed == 0, "no opacity-dimmed elements in shared charts")
    check(not errors, f"no page errors {errors}")
    browser.close()

sys.exit(1 if fails else 0)
