"""Brew hard-rule drafting (pluggable drafter) and approval gating; no network."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.server.domain.draft.brew as brew
import src.server.domain.draft.brew_builder as brew_builder
import src.server.domain.draft.brew_rules as br
import src.server.domain.draft.brew_rules_draft as drafter
from src.server.domain.llm.port import LLMError
from tests.support.brew_gate_seed import seed_gate_catalog
from tests.test_brew import FakeRedis

RULE = {"kind": "exclude", "field": "creature_type", "values": ["Dragon", "Angel", "Demon"],
        "label": "No Dragons, Angels or Demons"}


@pytest.fixture(autouse=True)
def _restore():
    yield
    drafter.set_drafter(None)
    drafter.set_client_factory(None)


@pytest.fixture
def env(request):
    r = FakeRedis()
    seed_gate_catalog(r)
    r.set("card_meta:Kaalia of the Vast", json.dumps({"name": "Kaalia of the Vast", "color": "WBR",
          "type": "Legendary Creature — Human Cleric", "commander_legality": "legal"}))
    r.set("card_meta:Shivan Dragon", json.dumps({"name": "Shivan Dragon", "color": "R",
          "type": "Creature — Dragon", "commander_legality": "legal"}))
    r.set("decks", "[]")
    brew_builder.set_builder(brew_builder.StubBrewBuilder())
    s = brew.start_brew(r, "Kaalia of the Vast", [], "owned_first", notes="no Dragons, Angels or Demons")
    return r, s["session_id"]


def test_default_drafter_without_llm_returns_empty_draft_with_note():
    drafter.set_client_factory(None)
    drafter.set_drafter(None)
    out = drafter.draft_rules({"notes": "no Dragons", "commanders": ["X"]})
    assert out["rules"] == [] and "unavailable" in out["note"]


def test_llm_drafter_parses_json_and_validates():
    class Client:
        def chat(self, messages):
            return "```json\n" + json.dumps({"rules": [RULE, {"kind": "bogus"}]}) + "\n```"
    drafter.set_client_factory(lambda task: Client())
    out = drafter.draft_rules({"notes": "no Dragons, Angels or Demons", "commanders": ["Kaalia of the Vast"]})
    assert [x["field"] for x in out["rules"]] == ["creature_type"]
    assert len(out["dropped"]) == 1 and "dropped" in out["note"]


def test_llm_error_degrades_to_empty():
    class Client:
        def chat(self, messages):
            raise LLMError("down")
    drafter.set_client_factory(lambda task: Client())
    out = drafter.draft_rules({"notes": "x", "commanders": []})
    assert out["rules"] == [] and "down" in out["note"]


def test_pluggable_drafter_output_is_validated():
    drafter.set_drafter(lambda s: [RULE, "free text"])
    out = drafter.draft_rules({})
    assert len(out["rules"]) == 1 and out["dropped"][0]["rule"] == "free text"


def test_drafter_crash_never_raises():
    def boom(s): raise RuntimeError("x")
    drafter.set_drafter(boom)
    assert drafter.draft_rules({})["rules"] == []


def test_draft_session_rules_stores_but_does_not_approve(env):
    r, sid = env
    drafter.set_drafter(lambda s: {"rules": [RULE]})
    out = brew.draft_session_rules(r, sid)
    s = brew.get_session(r, sid)
    assert out["rules"][0]["label"] == RULE["label"]
    assert s["draft_rules"]["rules"] and not s["rules_approved"] and s["approved_rules"] == []


def test_build_blocked_until_rules_approved(env):
    r, sid = env
    with pytest.raises(ValueError, match="hard rules must be approved"):
        brew.trigger_build(r, sid, sync=True)


def test_empty_approval_unlocks_build(env):
    r, sid = env
    s = brew.approve_rules(r, sid, [])
    assert s["rules_approved"] and s["approved_rules"] == []
    brew.trigger_build(r, sid, sync=True)  # does not raise the approval error


def test_approval_rejects_invalid_rule_and_stores_valid(env):
    r, sid = env
    with pytest.raises(br.RuleError, match="unknown rule kind"):
        brew.approve_rules(r, sid, [{"kind": "wish"}])
    assert not brew.get_session(r, sid)["rules_approved"]
    brew.approve_rules(r, sid, [RULE])
    assert brew.get_session(r, sid)["approved_rules"][0]["values"] == ["Dragon", "Angel", "Demon"]


def test_redrafting_clears_approval(env):
    r, sid = env
    brew.approve_rules(r, sid, [])
    brew.draft_session_rules(r, sid)
    assert not brew.get_session(r, sid)["rules_approved"]


def test_commander_violating_rule_fails_approval(env):
    r, sid = env
    rule = {"kind": "exclude", "field": "creature_type", "values": ["Human"], "label": "No Humans"}
    with pytest.raises(ValueError, match="commander Kaalia of the Vast breaks the rule"):
        brew.approve_rules(r, sid, [rule])


def test_approved_rules_filter_pools(env):
    r, sid = env
    r.set("card_meta:Grizzly Bears", json.dumps({"type": "Creature — Bear", "color": "G"}))
    brew.approve_rules(r, sid, [RULE])
    session = brew.get_session(r, sid)
    assert brew._session_rules(session)[0]["label"] == RULE["label"]
    brew.approve_rules(r, sid, [])
    assert brew._session_rules(brew.get_session(r, sid)) == []


def test_build_fails_loudly_naming_rule_when_starved(env):
    r, sid = env
    rule = {"kind": "exclude", "field": "card_type",
            "values": ["Artifact", "Instant", "Sorcery", "Enchantment", "Land"], "label": "No noncreature cards"}
    brew.approve_rules(r, sid, [rule])
    with pytest.raises(Exception) as exc:
        brew.trigger_build(r, sid, sync=True)
    assert "No noncreature cards" in str(exc.value) and "basic" in str(exc.value)
    session = brew.get_session(r, sid)
    assert session["status"] == "failed" and "No noncreature cards" in str(session.get("error"))


def test_execute_build_enforces_rules_on_builder_output(env, monkeypatch):
    r, sid = env

    class Builder(brew_builder.BaseBrewBuilder):
        def build(self, session, rr):
            return [{"id": "d", "commanders": ["Kaalia of the Vast"], "cards": [
                {"name": "Shivan Dragon", "quantity": 1}, {"name": "Grizzly Bears", "quantity": 1}]}]

    seen = {}

    def fake_release(rr, session, draft, **kw):
        seen["names"] = [c["name"] for c in draft["cards"]]
        seen["removed"] = draft.get("rule_removed")
        raise RuntimeError("stop-after-enforcement")

    brew_builder.set_builder(Builder())
    monkeypatch.setattr(brew, "release_draft", fake_release)
    monkeypatch.setattr(brew, "MIN_BUILD_CARDS", 1)
    brew.approve_rules(r, sid, [RULE])
    with pytest.raises(RuntimeError, match="stop-after-enforcement"):
        brew.execute_build(r, sid)
    assert seen["names"] == ["Grizzly Bears"]
    assert seen["removed"] == [{"card": "Shivan Dragon", "rule": RULE["label"]}]
