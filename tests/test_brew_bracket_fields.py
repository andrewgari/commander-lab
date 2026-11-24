"""BP-1: new Brew form fields, defaults, API round-trip, old-session migration, power knowledge md."""
import json
import os

import pytest

from src.server.domain.draft import brew
from src.server.domain.draft.brew_builder import guidance_note
from src.server.domain.knowledge import static_parts
from tests.test_brew import FakeRedis

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


def test_form_defaults_are_bracket_3_power_6():
    html = _read("src/web/templates/brew.html")
    assert 'hud.seg("bracket"' in html and 'selected="3"' in html
    assert 'hud.seg("power"' in html and '("10","10")' in html and 'selected="6"' in html
    for key in ("game_plan", "brewCombo", "brewArchetype", "dial_"):
        assert key in html
    js = _read("src/web/static/js/brew.js")
    assert "DEFAULT_POWER = 6" in js and "DEFAULT_BRACKET = 3" in js
    for key in ("bracket:", "game_plan:", "combo:", "archetype:", "dials:"):
        assert key in js


def test_session_defaults():
    s = brew.start_brew(FakeRedis(), "Atraxa", [], "owned_first")
    assert s["target_bracket"] == 3 and s["target_power"] == 6.0
    assert s["game_plan"] == "midrange" and s["combo"] is False
    assert s["archetype"] == "let the brewer decide"
    assert s["dials"] == {d: "neither" for d in brew.DIAL_NAMES}


def test_new_fields_round_trip():
    r = FakeRedis()
    dials = {"aggressiveness": "high", "political": "low"}
    s = brew.start_brew(r, "Atraxa", [], "best_build", bracket=4, power_level=8, game_plan="aggro",
                        combo=True, archetype="Infect", dials=dials)
    got = brew.get_session(r, s["session_id"])
    assert (got["target_bracket"], got["target_power"], got["game_plan"], got["combo"], got["archetype"]) == \
        (4, 8.0, "aggro", True, "Infect")
    assert got["dials"]["aggressiveness"] == "high" and got["dials"]["commander_focus"] == "neither"
    note = guidance_note(got)
    assert "Bracket 4" in note and "aggro" in note and "Infect" in note and "Aggressiveness high" in note


@pytest.mark.parametrize("kw", [{"bracket": 6}, {"game_plan": "burn"}, {"dials": {"x": "high"}},
                                {"dials": {"political": "max"}}])
def test_invalid_values_rejected(kw):
    with pytest.raises(ValueError):
        brew.start_brew(FakeRedis(), "Atraxa", [], "owned_first", **kw)


def test_old_session_with_only_power_level_loads():
    r = FakeRedis()
    s = brew.start_brew(r, "Atraxa", [], "owned_first")
    old = {k: v for k, v in s.items() if k not in ("target_bracket", "game_plan", "combo", "archetype", "dials")}
    r.set(f"brew:session:{s['session_id']}" if False else brew._session_key(s["session_id"]), json.dumps(old))
    got = brew.get_session(r, s["session_id"])
    assert got["target_bracket"] == 3 and got["game_plan"] == "midrange" and got["dials"]["political"] == "neither"


def test_api_round_trip():
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    import src.server.app.main as app_module
    r = FakeRedis()
    with patch.object(app_module, "r", r):
        c = TestClient(app_module.app)
        body = {"commanders": ["Atraxa, Praetors' Voice"], "build_mode": "owned_first", "bracket": 2, "power_level": 9,
                "game_plan": "control", "combo": True, "archetype": "Superfriends",
                "dials": {"interactiveness": "high"}}
        res = c.post("/api/brew/start", json=body)
        assert res.status_code == 201, res.text
        s = res.json()["session"]
        assert (s["target_bracket"], s["target_power"], s["game_plan"], s["combo"], s["archetype"]) == \
            (2, 9.0, "control", True, "Superfriends")
        assert s["dials"]["interactiveness"] == "high"
        assert c.post("/api/brew/start", json={**body, "bracket": 9}).status_code == 400


def test_power_table_wired_to_brewer_and_reviewer_prompts():
    md = _read("src/server/domain/knowledge/static/power_scale.md")
    assert "Precon baseline" in md and "Command Zone #295" in md
    for task in ("reconstruct", "brew_review"):
        assert any("## Power scale" in p for p in static_parts(task))
    from src.server.domain.draft.brew_builder import BrewBuilder
    assert "## Power scale" in BrewBuilder()._inner.guide
