"""Browser checks for the form-pending pattern (createPending + SubmitButton) against /dev/components/form-pending.
Start the app (npm run dev -- --port 5178, or `PORT=3000 node build`), then:
  uv run --with playwright python src/web/tests/form_pending.py [--url http://localhost:5178/dev/components/form-pending]
Set CHROMIUM=/usr/bin/chromium to use a system browser. Exits non-zero on any failure."""
import argparse, os, sys
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--url", default=os.environ.get("FORM_PENDING_URL", "http://localhost:5178/dev/components/form-pending"))
args = ap.parse_args()

fails = []
def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok:
        fails.append(msg)

def busy_attr(loc):
    """aria-busy as rendered: Svelte writes "false" when idle, never omits it."""
    return loc.get_attribute("aria-busy")


def wait_pending(page, testid):
    """In flight = aria-disabled set AND the pending label is the visible one (Svelte applies them in separate effects)."""
    page.wait_for_function(
        "(id) => { const b = document.querySelector(`[data-testid=${id}]`);"
        " return b.getAttribute('aria-disabled') === 'true' && getComputedStyle(b.querySelector('.sb__pending')).visibility === 'visible' }",
        arg=testid)


def wait_idle(page, testid):
    page.wait_for_function(
        "(id) => { const b = document.querySelector(`[data-testid=${id}]`);"
        " return !b.hasAttribute('aria-disabled') && b.closest('form').getAttribute('aria-busy') !== 'true' }",
        arg=testid)


ACTIVE_TESTID = "document.activeElement && document.activeElement.getAttribute('data-testid')"
TRANSIENT = "Network or server error — your input was kept. Try again."


def open_page(browser, **ctx):
    context = browser.new_context(viewport={"width": 1280, "height": 900}, **ctx)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    posts = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
    page.goto(args.url)
    page.wait_for_load_state("networkidle")
    return page, posts, errors


def posts_to(posts, action):
    return [u for u in posts if f"?/{action}" in u]


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path=os.environ.get("CHROMIUM") or None)

    # 1. slow: double-click plus Enter -> exactly one POST; aria-busy in flight; focus stays on the button
    page, posts, errors = open_page(browser)
    btn = page.get_by_test_id("btn-slow")
    form = page.get_by_test_id("form-slow")
    check(busy_attr(form) != "true", "1. idle form is not aria-busy")
    btn.dblclick()
    page.keyboard.press("Enter")
    check(form.get_attribute("aria-busy") == "true", "1. aria-busy=true on the form in flight")
    check(btn.get_attribute("aria-disabled") == "true" and not btn.evaluate("e => e.disabled"),
          "1. button is aria-disabled (not disabled) in flight")
    check(page.evaluate(ACTIVE_TESTID) == "btn-slow", "1. focus stays on the button in flight")
    page.keyboard.press("Enter")
    page.get_by_test_id("msg-slow").wait_for(timeout=10000)
    page.wait_for_timeout(300)
    check(len(posts_to(posts, "slow")) == 1, f"1. exactly 1 POST for double-click + Enter (saw {len(posts_to(posts, 'slow'))})")
    wait_idle(page, "btn-slow")
    check(busy_attr(form) != "true" and btn.get_attribute("aria-disabled") is None, "1. lock released after the response")
    check(not errors, f"1. no page errors {errors or ''}")
    page.context.close()

    # 2. the button does not change size between idle and pending
    page, posts, errors = open_page(browser)
    btn = page.get_by_test_id("btn-slow")
    idle = btn.bounding_box()
    assert idle
    btn.click()
    wait_pending(page, "btn-slow")
    busy = btn.bounding_box()
    assert busy
    deltas = {k: abs(idle[k] - busy[k]) for k in ("x", "y", "width", "height")}
    check(all(d <= 1 for d in deltas.values()), f"2. bounding box idle vs pending within 1px {deltas}")
    page.get_by_test_id("msg-slow").wait_for(timeout=10000)
    page.context.close()

    # 3. invalid -> input kept, error shown, lock released
    page, posts, errors = open_page(browser)
    page.locator("[data-testid=form-invalid] input[name=title]").fill("Typed by user")
    page.get_by_test_id("btn-invalid").click()
    page.get_by_test_id("err-invalid").wait_for(timeout=5000)
    check(page.locator("[data-testid=form-invalid] input[name=title]").input_value() == "Typed by user",
          "3. invalid: input value kept")
    check(page.get_by_test_id("err-invalid").inner_text() == "That title is not valid.", "3. invalid: actionError shown")
    wait_idle(page, "btn-invalid")
    check(page.get_by_test_id("btn-invalid").get_attribute("aria-disabled") is None, "3. invalid: lock released")
    check(page.locator("[data-testid=err-transient]").count() == 0, "3. invalid: no transient error")
    page.context.close()

    # 4. boom (503) and throws -> transientError in-form, not the +error page, input kept
    for action in ("boom", "throws"):
        page, posts, errors = open_page(browser)
        field = page.locator(f"[data-testid=form-{action}] input[name=title]")
        field.fill("Keep me")
        page.get_by_test_id(f"btn-{action}").click()
        page.get_by_test_id("err-transient").wait_for(timeout=5000)
        check(page.get_by_test_id("err-transient").inner_text() == TRANSIENT, f"4. {action}: transientError text shown")
        check(page.get_by_role("heading", name="Form pending", level=1).is_visible(), f"4. {action}: still on the fixture, not +error")
        check(page.url.split("?")[0] == args.url.split("?")[0], f"4. {action}: URL unchanged ({page.url})")
        check(field.input_value() == "Keep me", f"4. {action}: input kept")
        wait_idle(page, f"btn-{action}")
        check(page.get_by_test_id(f"btn-{action}").get_attribute("aria-disabled") is None, f"4. {action}: lock released")
        page.context.close()

    # 5. two forms, one instance: A locks B, and only A shows its pending label
    page, posts, errors = open_page(browser)
    a, b = page.get_by_test_id("btn-a"), page.get_by_test_id("btn-b")
    a.click()
    wait_pending(page, "btn-a")
    check(b.get_attribute("aria-disabled") == "true", "5. pressing A locks B")
    check(a.locator(".sb__pending").is_visible() and not b.locator(".sb__pending").is_visible(),
          "5. only A shows its pending label")
    check("Starting A…" in a.inner_text() and "Starting B…" not in b.inner_text(), "5. A text is the pending label, B keeps its idle label")
    b.click(force=True)
    page.wait_for_timeout(300)
    check(len(posts_to(posts, "slow")) == 1, f"5. clicking B while A is in flight sends nothing (saw {len(posts_to(posts, 'slow'))} POST)")
    wait_idle(page, "btn-a")
    check(b.get_attribute("aria-disabled") is None, "5. B unlocks when A finishes")
    page.context.close()

    # 6. confirm dismissed: no request, form not locked; then accepted: one request
    page, posts, errors = open_page(browser)
    dialogs = []
    def on_dialog(d):
        dialogs.append(d.message)
        d.dismiss() if len(dialogs) == 1 else d.accept()
    page.on("dialog", on_dialog)
    btn = page.get_by_test_id("btn-confirm")
    btn.click()
    page.wait_for_timeout(400)
    check(dialogs == ["Save “Zeta”?"], f"6. confirm shown with the page's message {dialogs}")
    check(len(posts_to(posts, "ok")) == 0, "6. dismissed confirm sends no request")
    check(busy_attr(page.get_by_test_id("form-confirm")) != "true" and btn.get_attribute("aria-disabled") is None, "6. dismissed confirm leaves the form unlocked")
    btn.click()
    page.get_by_test_id("msg-confirm").wait_for(timeout=5000)
    check(len(posts_to(posts, "ok")) == 1, "6. accepted confirm sends exactly one request")
    page.context.close()

    # 7. reduced motion: no spinner animation, static "…" and the pending text present
    page, posts, errors = open_page(browser)
    page.get_by_test_id("btn-slow").click()
    wait_pending(page, "btn-slow")
    motion = page.evaluate("""() => { const s = document.querySelector('[data-testid=btn-slow] .sb__spin');
      const cs = getComputedStyle(s); return [cs.animationName, cs.animationDuration] }""")
    check(motion[0] != "none" and motion[1] == "0.6s", f"7. default motion: spinner rotates over 600ms {motion}")
    page.get_by_test_id("msg-slow").wait_for(timeout=10000)
    page.context.close()

    page, posts, errors = open_page(browser, reduced_motion="reduce")
    page.get_by_test_id("btn-slow").click()
    wait_pending(page, "btn-slow")
    name = page.evaluate("getComputedStyle(document.querySelector('[data-testid=btn-slow] .sb__spin')).animationName")
    dots = page.locator("[data-testid=btn-slow] .sb__dots")
    check(name == "none", f"7. reduced motion: spinner animation-name is none (got {name})")
    check(dots.is_visible() and dots.inner_text() == "…", "7. reduced motion: static … shown")
    check("Saving…" in page.get_by_test_id("btn-slow").inner_text(), "7. reduced motion: pending text present")
    page.get_by_test_id("msg-slow").wait_for(timeout=10000)
    page.context.close()

    # 8. JavaScript disabled: the ok form posts natively and the page reloads with the result
    page, posts, errors = open_page(browser, java_script_enabled=False)
    page.get_by_test_id("btn-ok").click()
    page.get_by_test_id("msg-ok").wait_for(timeout=5000)
    check(page.get_by_test_id("msg-ok").inner_text() == "Saved “Beta”", "8. no-JS: native POST rendered the result")
    check(len(posts_to(posts, "ok")) == 1, "8. no-JS: exactly one POST")
    page.context.close()

    # redirect through update(): lands on ?done=1
    page, posts, errors = open_page(browser)
    page.get_by_test_id("btn-redirect").click()
    page.get_by_test_id("done").wait_for(timeout=5000)
    check(page.url.endswith("?done=1"), f"redirect action lands on ?done=1 ({page.url})")
    page.context.close()

    browser.close()

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
