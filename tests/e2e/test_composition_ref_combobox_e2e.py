"""Playwright e2e: Ref combobox + "Did you mean" on the presets page and deck override form.

Run: REDIS_URL=redis://localhost:6379/0 python -m pytest tests/e2e/test_composition_ref_combobox_e2e.py --base-url=http://localhost:8000
"""
import json
import os
import unittest
import uuid

try:
    import pytest
    import redis
    from playwright.sync_api import expect
except ImportError as exc:
    raise unittest.SkipTest("pytest/playwright not installed") from exc


@pytest.fixture(scope="module")
def rds():
    return redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)


@pytest.fixture
def seeded(rds):
    sfx = uuid.uuid4().hex[:8]
    sid = 9_000_000 + int(sfx, 16) % 1_000_000
    deck = {"id": sid, "name": f"E2E Ref {sfx}", "color": "G", "commanders": ["Atraxa, Grand Unifier"], "status": "physical",
            "registry_id": f"archidekt:{sid}", "source": "archidekt", "source_id": str(sid),
            "cards": [{"name": "Atraxa, Grand Unifier", "quantity": 1}]}
    rds.set("decks", json.dumps(json.loads(rds.get("decks") or "[]") + [deck]))
    gl = json.loads(rds.get("categories:global") or "[]")
    rds.set("categories:global", json.dumps(gl + [{"id": f"g-{sfx}", "name": f"Glob {sfx}"}]))
    rds.set(f"categories:deck:{deck['registry_id']}", json.dumps([{"id": f"d-{sfx}", "name": f"Mine {sfx}"}]))
    yield deck, sfx
    rds.set("decks", json.dumps([d for d in json.loads(rds.get("decks") or "[]") if d.get("registry_id") != deck["registry_id"]]))
    rds.set("categories:global", json.dumps([c for c in json.loads(rds.get("categories:global") or "[]") if c["id"] != f"g-{sfx}"]))
    rds.delete(f"categories:deck:{deck['registry_id']}")


def _options(page):
    return page.locator("[role=option]").all_inner_texts()


def _first_ref(page):
    page.click("#cpNew")
    page.click("#cpAddSlot")
    return page.locator("#cpSlots input[aria-label='Category ref']").first


def test_presets_suggestions_and_did_you_mean(page, base_url):
    page.goto(f"{base_url}/composition/presets")
    ref = _first_ref(page)
    ref.click()
    for word, want in (("draw", ["core:card_advantage"]), ("ramp", ["core:ramp"]), ("wrath", ["core:board_wipe"]),
                       ("inter", ["group:interaction", "group:targeted_interaction"])):
        ref.fill(word)
        texts = " ".join(_options(page))
        for w in want:
            assert w in texts, (word, texts)
    ref.fill("draw")
    assert ref.get_attribute("role") == "combobox"
    ref.press("ArrowDown")
    ref.press("Enter")
    assert ref.input_value() == "core:card_advantage"
    ref.fill("ramp")
    expect(page.locator("[role=option]")).to_have_count(1)
    ref.press("Escape")
    assert ref.input_value() == "ramp"
    page.fill("#cpName", f"E2E {uuid.uuid4().hex[:6]}")
    page.click("#cpSave")
    fix = page.locator(".refcb-fix-btn", has_text="core:ramp")
    expect(fix).to_have_count(1)
    assert ref.input_value() == "ramp"  # never auto-corrected
    fix.click()
    expect(page.locator("#cpMsg")).to_have_text("Saved.")
    page.once("dialog", lambda d: d.accept())
    page.click("#cpDelete")


def test_presets_multi_match(page, base_url):
    page.goto(f"{base_url}/composition/presets")
    ref = _first_ref(page)
    ref.fill("Interaction")
    expect(page.locator("[role=option]")).to_have_count(2)
    ref.press("Escape")
    page.fill("#cpName", "tmp")
    page.click("#cpSave")
    expect(page.locator(".refcb-fix-btn")).to_have_count(2)


def test_deck_scope_categories(page, base_url, seeded):
    deck, sfx = seeded
    page.goto(f"{base_url}/composition/presets")
    r = _first_ref(page)
    r.fill(f"d-{sfx}")
    assert not page.locator("[role=option]").count()
    r.fill(f"g-{sfx}")
    expect(page.locator("[role=option]")).to_have_count(1)
    assert f"cat:g-{sfx}" in " ".join(_options(page))
    page.goto(f"{base_url}/deck/{deck['name'].replace(' ', '%20')}/composition")
    page.check("#compModePinned")
    page.click("#compAddOverride")
    o = page.locator("#compOverrides .cpi-input")
    o.fill(f"d-{sfx}")
    expect(page.locator("[role=option]")).to_have_count(1)
    assert f"cat:d-{sfx}" in " ".join(_options(page))


def test_deck_override_pills_roundtrip(page, base_url, seeded):
    deck, sfx = seeded
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    body = {"name": f"E2E pin {sfx}", "structure": {"deck_size": 100, "commanders": 1, "lands": 38},
            "slots": [{"ref": "core:ramp", "target": 10, "min": None, "max": None, "why": ""},
                      {"ref": "core:card_advantage", "target": 12, "min": None, "max": None, "why": ""}]}
    resp = page.request.post(f"{base_url}/api/composition/presets", data=body)
    assert resp.status == 201, resp.text()
    pid = resp.json()["preset"]["id"]
    page.goto(f"{base_url}/deck/{deck['name'].replace(' ', '%20')}/composition")
    page.check("#compModePinned")
    page.select_option("#compPresetSelect", pid)
    page.click("#compAddOverride")  # empty -> focuses input, no throw
    inp = page.locator("#compOverrides .cpi-input")
    expect(inp).to_be_focused()
    inp.fill("ramp")
    expect(page.locator("[role=option]")).to_have_count(1)
    inp.press("Escape")  # dropdown overlays the button
    page.click("#compAddOverride")  # typed text -> pill
    pill = page.locator("#compOverrides .cpi-pill[data-ref='core:ramp']")
    expect(pill).to_have_count(1)
    pill.locator("input[aria-label='Target']").fill("10")
    pill.locator("input[aria-label='Min']").fill("8")
    pill.locator("input[aria-label='Why']").fill("fast mana")
    with page.expect_navigation():  # save succeeds -> page reloads
        page.click("#compPickerForm button[type=submit]")
    pill = page.locator("#compOverrides .cpi-pill[data-ref='core:ramp']")
    expect(pill).to_have_count(1)
    expect(pill.locator("input[aria-label='Target']")).to_have_value("10")
    expect(pill.locator("input[aria-label='Min']")).to_have_value("8")
    expect(pill.locator("input[aria-label='Max']")).to_have_value("")
    expect(pill.locator("input[aria-label='Why']")).to_have_value("fast mana")
    pill.locator(".cpi-pill-rm").click()
    expect(page.locator("#compOverrides .cpi-pill")).to_have_count(0)
    assert not errors
    page.request.delete(f"{base_url}/api/composition/presets/{pid}")


def test_category_pill_input(page, base_url):
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base_url}/composition/presets")
    page.evaluate("""() => { const d = document.createElement('div'); d.id = 'pillHost'; document.body.prepend(d);
        window.pi = CategoryPillInput.mount(d, {inputName: 'ref'}); }""")
    inp = page.locator("#pillHost .cpi-input")
    inp.fill("ramp")
    expect(page.locator("#pillHost [role=option]").first).to_contain_text("core:ramp")
    inp.press("ArrowDown")
    inp.press("Enter")
    expect(page.locator("#pillHost .cpi-pill[data-ref='core:ramp']")).to_have_count(1)
    inp.fill("ramp")
    inp.press("Enter")
    expect(page.locator("#pillHost .cpi-error")).to_contain_text("already added")
    expect(page.locator("#pillHost .cpi-pill")).to_have_count(1)
    inp.fill("zzzzqq")
    inp.press("Enter")
    expect(page.locator("#pillHost .cpi-error")).to_contain_text("No matching")
    page.locator("#pillHost .cpi-pill-rm").dispatch_event("click")
    expect(page.locator("#pillHost .cpi-pill")).to_have_count(0)
    assert not errors
