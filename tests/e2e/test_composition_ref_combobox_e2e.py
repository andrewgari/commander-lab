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


def _slot_input(page):
    page.click("#cpNew")
    return page.locator("#cpSlots .cpi-input")


def test_presets_suggestions_and_pills(page, base_url):
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base_url}/composition/presets")
    inp = _slot_input(page)
    page.click("#cpAddSlot")  # empty input: focuses, never throws
    expect(inp).to_be_focused()
    for word, want in (("draw", ["core:card_advantage"]), ("ramp", ["core:ramp"]), ("wrath", ["core:board_wipe"]),
                       ("inter", ["group:interaction", "group:targeted_interaction"])):
        inp.fill(word)
        texts = " ".join(_options(page))
        for w in want:
            assert w in texts, (word, texts)
    inp.fill("draw")
    inp.press("ArrowDown")
    inp.press("Enter")
    expect(page.locator("#cpSlots .cpi-pill[data-ref='core:card_advantage']")).to_have_count(1)
    inp.fill("ramp")
    page.click("#cpAddSlot")
    expect(page.locator("#cpSlots .cpi-pill[data-ref='core:ramp']")).to_have_count(1)
    page.locator("#cpSlots .cpi-pill[data-ref='core:card_advantage'] .cpi-pill-rm").click()
    expect(page.locator("#cpSlots .cpi-pill")).to_have_count(1)
    assert not errors


def test_presets_round_trip(page, base_url):
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base_url}/composition/presets")
    inp = _slot_input(page)
    name = f"E2E {uuid.uuid4().hex[:6]}"
    page.fill("#cpName", name)
    inp.fill("ramp")
    page.click("#cpAddSlot")
    pill = page.locator("#cpSlots .cpi-pill[data-ref='core:ramp']")
    expect(pill).to_have_count(1)
    pill.locator("input[aria-label='Target']").fill("11")
    pill.locator("input[aria-label='Min']").fill("9")
    pill.locator("input[aria-label='Max']").fill("13")
    pill.locator("input[aria-label='Why']").fill("mana")
    page.click("#cpSave")
    expect(page.locator("#cpMsg")).to_have_text("Saved.")
    page.reload()
    page.locator(".cp-item", has_text=name).click()
    pill = page.locator("#cpSlots .cpi-pill[data-ref='core:ramp']")
    expect(pill).to_have_count(1)
    expect(pill.locator("input[aria-label='Target']")).to_have_value("11")
    expect(pill.locator("input[aria-label='Min']")).to_have_value("9")
    expect(pill.locator("input[aria-label='Max']")).to_have_value("13")
    expect(pill.locator("input[aria-label='Why']")).to_have_value("mana")
    page.once("dialog", lambda d: d.accept())
    page.click("#cpDelete")
    assert not errors


def test_deck_scope_categories(page, base_url, seeded):
    deck, sfx = seeded
    page.goto(f"{base_url}/composition/presets")
    r = _slot_input(page)
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
