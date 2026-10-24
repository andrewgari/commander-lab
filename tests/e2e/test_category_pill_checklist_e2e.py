"""Playwright e2e: checklist for the CategoryPillInput on both composition pages (t_3f9f26d0).

Covers: load (no 404 / console errors), autocomplete + keyboard, autocorrect, garbage rejection,
Add button, duplicates, x-remove, extra fields, save -> reload round trip + payload shape,
pre-existing data rendering, and the "dropdown covers the Add button" regression.

Run (app must be up against a scratch Redis):
  REDIS_URL=redis://localhost:6379/0 uvicorn app:app --port 8000
  REDIS_URL=redis://localhost:6379/0 python -m pytest tests/e2e/test_category_pill_checklist_e2e.py --base-url=http://localhost:8000 -v
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


PAGES = {
    "presets": {"host": "#cpSlots", "add": "#cpAddSlot"},
    "deck": {"host": "#compOverrides", "add": "#compAddOverride"},
}


@pytest.fixture(scope="module")
def rds():
    return redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)


@pytest.fixture
def deck(rds):
    sfx = uuid.uuid4().hex[:8]
    sid = 8_000_000 + int(sfx, 16) % 1_000_000
    d = {"id": sid, "name": f"E2E Pill {sfx}", "color": "G", "commanders": ["Atraxa, Grand Unifier"], "status": "physical",
         "registry_id": f"archidekt:{sid}", "source": "archidekt", "source_id": str(sid),
         "cards": [{"name": "Atraxa, Grand Unifier", "quantity": 1}]}
    rds.set("decks", json.dumps(json.loads(rds.get("decks") or "[]") + [d]))
    yield d
    rds.set("decks", json.dumps([x for x in json.loads(rds.get("decks") or "[]") if x.get("registry_id") != d["registry_id"]]))


@pytest.fixture
def preset(page, base_url):
    body = {"name": f"E2E pre {uuid.uuid4().hex[:6]}", "structure": {"deck_size": 100, "commanders": 1, "lands": 37},
            "slots": [{"ref": "core:ramp", "target": 10, "min": 8, "max": 12, "why": "go fast"},
                      {"ref": "core:card_advantage", "target": 12, "min": None, "max": None, "why": ""}]}
    r = page.request.post(f"{base_url}/api/composition/presets", data=body)
    assert r.status == 201, r.text()
    p = r.json()["preset"]
    yield p
    page.request.delete(f"{base_url}/api/composition/presets/{p['id']}")


def _watch(page):
    log = {"errors": [], "console": [], "bad": []}
    page.on("pageerror", lambda e: log["errors"].append(str(e)))
    page.on("console", lambda m: m.type in ("error", "warning") and log["console"].append(f"{m.type}: {m.text}"))
    page.on("response", lambda r: r.status >= 400 and log["bad"].append(f"{r.status} {r.url}"))
    return log


def _open(page, base_url, which, deck=None, preset=None):
    if which == "presets":
        page.goto(f"{base_url}/composition/presets")
        if preset:
            page.locator(".cp-item", has_text=preset["name"]).click()
        else:
            page.click("#cpNew")
    else:
        page.goto(f"{base_url}/deck/{deck['name'].replace(' ', '%20')}/composition")
        page.check("#compModePinned")
        if preset:
            page.select_option("#compPresetSelect", preset["id"])
    return page.locator(f"{PAGES[which]['host']} .cpi-input")


@pytest.mark.parametrize("which", ["presets", "deck"])
def test_load_clean(page, base_url, deck, which):
    log = _watch(page)
    _open(page, base_url, which, deck)
    page.wait_for_load_state("networkidle")
    assert not [b for b in log["bad"] if "ref_combobox" in b or "/static/" in b], log["bad"]
    assert not log["errors"], log["errors"]
    assert not [c for c in log["console"] if c.startswith("error")], log["console"]


@pytest.mark.parametrize("which", ["presets", "deck"])
def test_autocomplete_autocorrect_dupes_remove(page, base_url, deck, which):
    log = _watch(page)
    inp = _open(page, base_url, which, deck)
    host = PAGES[which]["host"]
    opts = page.locator(f"{host} [role=option]")
    # autocomplete (partial name and partial ref)
    inp.fill("dra")
    expect(opts.first).to_be_visible()
    assert "core:card_advantage" in " ".join(opts.all_inner_texts())
    inp.fill("core:ram")
    expect(opts.first).to_contain_text("core:ramp")
    # keyboard select
    inp.press("ArrowDown")
    inp.press("Enter")
    expect(page.locator(f"{host} .cpi-pill[data-ref='core:ramp']")).to_have_count(1)
    expect(inp).to_have_value("")
    # autocorrect: case mismatch + typo
    inp.fill("CORE:CARD_ADVANTAGE")
    inp.press("Escape")
    inp.press("Enter")
    expect(page.locator(f"{host} .cpi-pill[data-ref='core:card_advantage']")).to_have_count(1)
    inp.fill("removel")  # typo for "removal"
    inp.press("Escape")
    inp.press("Enter")
    expect(page.locator(f"{host} .cpi-pill")).to_have_count(3)
    # garbage
    inp.fill("qqzzxxyy")
    inp.press("Enter")
    expect(page.locator(f"{host} .cpi-error")).to_contain_text("No matching")
    expect(page.locator(f"{host} .cpi-pill")).to_have_count(3)
    # duplicate
    inp.fill("Ramp")
    inp.press("Escape")
    inp.press("Enter")
    expect(page.locator(f"{host} .cpi-error")).to_contain_text("already added")
    expect(page.locator(f"{host} .cpi-pill")).to_have_count(3)
    # remove
    page.locator(f"{host} .cpi-pill[data-ref='core:ramp'] .cpi-pill-rm").click()
    expect(page.locator(f"{host} .cpi-pill")).to_have_count(2)
    assert not log["errors"], log["errors"]


@pytest.mark.parametrize("which", ["presets", "deck"])
def test_add_button_with_dropdown_open(page, base_url, deck, which):
    """A real user types, then clicks the Add button while suggestions are showing."""
    log = _watch(page)
    inp = _open(page, base_url, which, deck)
    inp.fill("wipe")
    expect(page.locator(f"{PAGES[which]['host']} [role=option]").first).to_be_visible()
    page.click(PAGES[which]["add"], timeout=5000)  # fails if the dropdown covers the button
    expect(page.locator(f"{PAGES[which]['host']} .cpi-pill[data-ref='core:board_wipe']")).to_have_count(1)
    assert not log["errors"], log["errors"]


@pytest.mark.parametrize("which", ["presets", "deck"])
def test_existing_data_renders(page, base_url, deck, preset, rds, which):
    if which == "deck":
        r = page.request.put(f"{base_url}/api/decks/{deck['registry_id']}/composition/selection",
                             data={"mode": "pinned", "preset_id": preset["id"], "lands": None,
                                   "overrides": [{"ref": "core:ramp", "target": 10, "min": 8, "max": 12}]})
        assert r.ok, r.text()
    _open(page, base_url, which, deck, preset if which == "presets" else None)
    host = PAGES[which]["host"]
    pill = page.locator(f"{host} .cpi-pill[data-ref='core:ramp']")
    expect(pill).to_have_count(1)
    expect(pill).to_contain_text("Ramp")
    expect(pill.locator("input[aria-label='Target']")).to_have_value("10")
    expect(pill.locator("input[aria-label='Min']")).to_have_value("8")
    expect(pill.locator("input[aria-label='Max']")).to_have_value("12")
    if which == "presets":
        expect(page.locator(f"{host} .cpi-pill")).to_have_count(2)
        expect(pill.locator("input[aria-label='Why']")).to_have_value("go fast")


def test_presets_round_trip_payload(page, base_url):
    inp = _open(page, base_url, "presets")
    name = f"E2E rt {uuid.uuid4().hex[:6]}"
    page.fill("#cpName", name)
    inp.fill("ramp"); inp.press("Escape"); inp.press("Enter")
    inp.fill("draw"); inp.press("Escape"); inp.press("Enter")
    pill = page.locator("#cpSlots .cpi-pill[data-ref='core:ramp']")
    pill.locator("input[aria-label='Target']").fill("11")
    pill.locator("input[aria-label='Max']").fill("13")
    pill.locator("input[aria-label='Why']").fill("mana")
    with page.expect_request(lambda r: r.url.endswith("/api/composition/presets") and r.method == "POST") as req:
        page.click("#cpSave")
    slots = req.value.post_data_json["slots"]
    assert slots[0] == {"ref": "core:ramp", "target": 11, "min": None, "max": 13, "why": "mana"}, slots
    assert slots[1]["ref"] == "core:card_advantage" and slots[1]["min"] is None and slots[1]["why"] == ""
    expect(page.locator("#cpMsg")).to_have_text("Saved.")
    page.reload()
    page.locator(".cp-item", has_text=name).click()
    pill = page.locator("#cpSlots .cpi-pill[data-ref='core:ramp']")
    expect(pill.locator("input[aria-label='Target']")).to_have_value("11")
    expect(pill.locator("input[aria-label='Min']")).to_have_value("")
    expect(pill.locator("input[aria-label='Max']")).to_have_value("13")
    expect(pill.locator("input[aria-label='Why']")).to_have_value("mana")
    page.once("dialog", lambda d: d.accept())
    page.click("#cpDelete")


def test_deck_round_trip_payload(page, base_url, deck, preset):
    inp = _open(page, base_url, "deck", deck, preset)
    inp.fill("ramp"); inp.press("Escape"); inp.press("Enter")
    inp.fill("draw"); inp.press("Escape"); inp.press("Enter")
    pill = page.locator("#compOverrides .cpi-pill[data-ref='core:ramp']")
    pill.locator("input[aria-label='Target']").fill("9")
    pill.locator("input[aria-label='Min']").fill("7")
    pill.locator("input[aria-label='Why']").fill("fast")
    # override pills start blank; the backend rejects an override with no target/min/max
    page.locator("#compOverrides .cpi-pill[data-ref='core:card_advantage'] input[aria-label='Max']").fill("14")
    with page.expect_request(lambda r: "/composition/selection" in r.url and r.method == "PUT") as req:
        with page.expect_navigation():
            page.click("#compPickerSave")
    ov = req.value.post_data_json["overrides"]
    assert ov[0] == {"ref": "core:ramp", "target": 9, "min": 7, "max": None, "why": "fast"}, ov
    assert ov[1] == {"ref": "core:card_advantage", "target": None, "min": None, "max": 14}, ov
    pill = page.locator("#compOverrides .cpi-pill[data-ref='core:ramp']")
    expect(pill.locator("input[aria-label='Target']")).to_have_value("9")
    expect(pill.locator("input[aria-label='Min']")).to_have_value("7")
    expect(pill.locator("input[aria-label='Why']")).to_have_value("fast")
    expect(page.locator("#compOverrides .cpi-pill")).to_have_count(2)
