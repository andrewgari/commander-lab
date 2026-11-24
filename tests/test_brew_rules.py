"""Brew hard rules (BREW-2): schema, matcher, every enforcement point, approval gating, loud failure."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.server.domain.draft.brew as brew
import src.server.domain.draft.brew_rules as br
import src.server.domain.draft.deck_validator as dv
from src.server.domain.draft.deck_fill import fill_deck
from tests.test_brew import FakeRedis

DRAGON_RULE = {"kind": "exclude", "field": "creature_type", "values": ["Dragon", "Angel", "Demon"],
               "label": "No Dragons, Angels or Demons"}

TYPES = {
    "Shivan Dragon": "Creature — Dragon",
    "Serra Angel": "Creature — Angel",
    "Griselbrand": "Legendary Creature — Demon",
    "Grizzly Bears": "Creature — Bear",
    "Sol Ring": "Artifact",
    "Dragon's Approach": "Enchantment",  # name only, no creature subtype
    "Kolaghan's Command": "Instant",
    "Dragon Egg Keeper // Hatched Dragon": "Creature — Human Shaman // Creature — Dragon",
    "Kindred Dragon": "Kindred Creature — Dragon",
    "Island": "Basic Land — Island",
}


def seed(r):
    for n, t in TYPES.items():
        r.set(f"card_meta:{n}", json.dumps({"name": n, "type": t, "commander_legality": "legal"}))


@pytest.fixture
def r():
    x = FakeRedis()
    seed(x)
    return x


# -- schema ------------------------------------------------------------------------------

def test_valid_rule_normalised_and_label_generated():
    rule = br.validate_rule({"kind": "exclude", "field": "creature_type", "values": ["Dragon", "dragon", " Angel "]})
    assert rule["values"] == ["Dragon", "Angel"] and rule["label"]


@pytest.mark.parametrize("bad,msg", [
    ({"kind": "include", "field": "name", "values": ["x"]}, "unknown rule kind"),
    ({"kind": "exclude", "field": "power", "values": ["x"]}, "unknown rule field"),
    ({"kind": "exclude", "field": "name", "values": []}, "non-empty"),
    ({"kind": "exclude", "field": "name", "values": [3]}, "non-empty string"),
    ({"kind": "exclude", "field": "color", "values": ["purple"]}, "unknown colour"),
    ("free text rule", "must be an object"),
])
def test_invalid_rules_rejected_with_clear_error(bad, msg):
    with pytest.raises(br.RuleError, match=msg):
        br.validate_rule(bad)


def test_lenient_validation_reports_dropped_items():
    rules, errors = br.validate_rules([DRAGON_RULE, {"kind": "nope"}])
    assert len(rules) == 1 and errors[0]["index"] == 1
    with pytest.raises(br.RuleError):
        br.validate_rules_strict([DRAGON_RULE, {"kind": "nope"}])


# -- matcher: the Kaalia example + DFC ---------------------------------------------------

def test_dragon_angel_demon_example(r):
    kept, removed = br.filter_names([DRAGON_RULE], list(TYPES), r)
    gone = {x["card"] for x in removed}
    assert {"Shivan Dragon", "Serra Angel", "Griselbrand", "Kindred Dragon"} <= gone
    assert all(x["rule"] == DRAGON_RULE["label"] for x in removed)
    assert {"Grizzly Bears", "Sol Ring", "Dragon's Approach", "Kolaghan's Command", "Island"} <= set(kept)


def test_double_faced_either_face_violates(r):
    kept, removed = br.filter_names([DRAGON_RULE], ["Dragon Egg Keeper // Hatched Dragon"], r)
    assert kept == [] and removed


def test_dfc_with_card_faces_record():
    facts = br.facts_from_record("Front // Back", {"card_faces": [
        {"name": "Front", "type_line": "Creature — Human", "oracle_text": "a"},
        {"name": "Back", "type_line": "Creature — Demon", "oracle_text": "b"}]})
    assert br.violates(DRAGON_RULE, facts)


def test_other_fields(r):
    r.set("card_meta:Blue Thing", json.dumps({"type": "Instant", "colors": ["U"]}))
    r.set("card_meta:Extra Turn", json.dumps({"type": "Sorcery", "oracle_text": "Take an extra turn after this one."}))
    cases = [({"field": "color", "values": ["blue"]}, "Blue Thing"),
             ({"field": "oracle_text", "values": ["extra turn"]}, "Extra Turn"),
             ({"field": "name", "values": ["sol ring"]}, "Sol Ring"),
             ({"field": "card_type", "values": ["Artifact"]}, "Sol Ring")]
    for spec, name in cases:
        rule = br.validate_rule({"kind": "exclude", **spec})
        assert br.filter_names([rule], [name], r)[0] == [], spec


def test_basics_and_protected_never_removed(r):
    rule = br.validate_rule({"kind": "exclude", "field": "card_type", "values": ["Land"]})
    assert br.filter_names([rule], ["Island"], r)[0] == ["Island"]
    assert br.filter_names([DRAGON_RULE], ["Griselbrand"], r, protected=["Griselbrand"])[0] == ["Griselbrand"]


# -- every enforcement point -------------------------------------------------------------

class _PC:
    def __init__(self, name):
        self.name = name


class _Pools:
    def __init__(self):
        self.tier1 = [_PC("Shivan Dragon"), _PC("Sol Ring")]
        self.tier2 = [_PC("Serra Angel"), _PC("Grizzly Bears")]
        self.tier3 = [_PC("Griselbrand"), _PC("Island")]


def test_filter_pools_every_tier(r):
    pools = _Pools()
    removed = br.filter_pools([DRAGON_RULE], pools, r)
    assert {x["card"] for x in removed} == {"Shivan Dragon", "Serra Angel", "Griselbrand"}
    names = [pc.name for t in (pools.tier1, pools.tier2, pools.tier3) for pc in t]
    assert sorted(names) == ["Grizzly Bears", "Island", "Sol Ring"]
    assert pools.rule_removed == removed


def test_enforce_draft_records_removed(r):
    draft = {"cards": [{"name": "Shivan Dragon", "quantity": 1}, {"name": "Sol Ring", "quantity": 1}]}
    br.enforce_draft([DRAGON_RULE], draft, r)
    assert [c["name"] for c in draft["cards"]] == ["Sol Ring"]
    assert draft["rule_removed"] == [{"card": "Shivan Dragon", "rule": DRAGON_RULE["label"]}]
    assert br.draft_violations([DRAGON_RULE], draft, r) == []


def _catalog():
    def ci(n, types=("Creature",), sub=()):
        return dv.CardInfo(name=n, color_identity=dv.parse_color_identity(""), types=tuple(types),
                           supertypes=(), subtypes=tuple(sub), oracle_text="", commander_legality="legal")
    cards = [dv.CardInfo(name="Cmdr", color_identity=dv.parse_color_identity(""), types=("Creature",),
                         supertypes=("Legendary",), oracle_text="", commander_legality="legal")]
    cards += [ci(f"Dragon {i}", sub=("Dragon",)) for i in range(150)]
    cards += [ci(f"Bear {i}") for i in range(30)]
    return dv.CardCatalog(cards)


def test_deck_fill_honours_rule_scope(r):
    for i in range(150):
        r.set(f"card_meta:Dragon {i}", json.dumps({"type": "Creature — Dragon"}))
    for i in range(30):
        r.set(f"card_meta:Bear {i}", json.dumps({"type": "Creature — Bear"}))
    cat = _catalog()
    sources = [("src", [f"Dragon {i}" for i in range(150)] + [f"Bear {i}" for i in range(30)])]
    with br.rules_scope([DRAGON_RULE], r):
        with pytest.raises(br.RulesStarvedError) as exc:
            fill_deck("Cmdr", [], sources, cat)
    assert DRAGON_RULE["label"] in str(exc.value) and "basic" in str(exc.value)
    # without a scope the dragons are used
    assert len(fill_deck("Cmdr", [], sources, cat)) == 100


def test_starved_message_names_rules():
    msg = br.starved_message([{"card": "a", "rule": "No X"}, {"card": "b", "rule": "No X"}], 3, 49)
    assert "No X" in msg and "removed 2" in msg


def test_color_rule_reads_color_key_as_well_as_colors():
    rule = br.validate_rule({"kind": "exclude", "field": "color", "values": ["red"], "label": "No red"})
    only_color = br.facts_from_record("A", {"type": "Creature — Goblin", "color": "R"})
    assert br.violates(rule, only_color)
    assert br.violates(rule, br.facts_from_record("B", {"colors": ["R", "G"]}))
    assert br.violates(rule, br.facts_from_record("C", {"color": "WR"}))
    assert not br.violates(rule, br.facts_from_record("D", {"color": "G"}))
    assert not br.violates(rule, br.facts_from_record("E", {"colors": ["U"], "color": "U"}))


def test_rule_shortfall_cannot_be_padded_with_extra_basics(r):
    # 55 usable non-basics (above the old 49 threshold) + 80 rule-removed Dragons: the
    # remaining 44 slots would be basics, more than a normal land count, so it must fail.
    for i in range(80):
        r.set(f"card_meta:Dragon {i}", json.dumps({"type": "Creature — Dragon"}))
    for i in range(55):
        r.set(f"card_meta:Bear {i}", json.dumps({"type": "Creature — Bear"}))
    cards = [f"Dragon {i}" for i in range(80)] + [f"Bear {i}" for i in range(55)]
    ci = lambda n, **k: dv.CardInfo(name=n, color_identity=dv.parse_color_identity(""), types=("Creature",),
                                    commander_legality="legal", **k)
    cat = dv.CardCatalog([dv.CardInfo(name="Cmdr", color_identity=dv.parse_color_identity(""), types=("Creature",),
                                      supertypes=("Legendary",), commander_legality="legal")]
                         + [ci(f"Dragon {i}", subtypes=("Dragon",)) for i in range(80)]
                         + [ci(f"Bear {i}") for i in range(55)])
    with br.rules_scope([DRAGON_RULE], r):
        with pytest.raises(br.RulesStarvedError) as exc:
            fill_deck("Cmdr", [], [("src", cards)], cat)
    assert DRAGON_RULE["label"] in str(exc.value) and "basic" in str(exc.value)
    assert br.MIN_NON_BASIC == 99 - br.NORMAL_LAND_COUNT


# -- shortfall guards (brew.py _enforce_rules, brew_builder.py StubBrewBuilder) -----------

def _starved_session(r, n_dragons, n_bears):
    for i in range(n_dragons):
        r.set(f"card_meta:Dragon {i}", json.dumps({"name": f"Dragon {i}", "type": "Creature — Dragon"}))
    for i in range(n_bears):
        r.set(f"card_meta:Bear {i}", json.dumps({"name": f"Bear {i}", "type": "Creature — Bear"}))
    r.set("card_meta:Cmdr", json.dumps({"name": "Cmdr", "type": "Legendary Creature — Human"}))
    return {
        "session_id": "s-starved", "status": brew.STATUS_BUILDING, "build_mode": "owned_first",
        "commanders": ["Cmdr"], "preferences": [{"commanders": ["Cmdr"], "notes": ""}],
        "donor_decks": [], "approved_rules": [DRAGON_RULE], "rules_approved": True,
    }


def _basic_cards(cards):
    return [c["name"] for c in cards if (c.get("name") or "").casefold() in br._BASICS]


def test_execute_build_rule_shortfall_fails_loudly_without_basics(r, monkeypatch):
    import src.server.domain.draft.brew_builder as bb

    session = _starved_session(r, 20, 50)
    brew._save_session(r, session)
    cards = ([{"name": "Cmdr", "quantity": 1}]
             + [{"name": f"Dragon {i}", "quantity": 1} for i in range(20)]
             + [{"name": f"Bear {i}", "quantity": 1} for i in range(50)])
    assert 50 < br.MIN_NON_BASIC  # 51 non-basics survive: below the minimum

    class Fixed(bb.BaseBrewBuilder):
        def build(self, s, rr):
            return [{"cards": [dict(c) for c in cards], "commanders": ["Cmdr"]}]

    gated = []
    monkeypatch.setattr(brew, "release_draft", lambda *a, **k: gated.append(1) or a[2])
    monkeypatch.setattr(bb, "_builder_instance", Fixed())

    with pytest.raises(br.RulesStarvedError) as exc:
        brew.execute_build(r, "s-starved")
    assert DRAGON_RULE["label"] in str(exc.value)
    assert gated == []  # failed at the guard, before the gate could refill with basics
    saved = brew.get_session(r, "s-starved")
    assert saved["status"] == brew.STATUS_FAILED and DRAGON_RULE["label"] in saved["error"]
    assert not saved.get("drafts")


def test_brew_builder_rule_shortfall_fails_loudly_without_basics(r, monkeypatch):
    import src.server.domain.draft.brew_builder as bb
    from src.server.domain.draft.reconstruct_pools import CandidatePools

    session = _starved_session(r, 20, 50)
    session["donor_decks"] = [{"deck_id": "d1", "commanders": [], "cards": (
        [{"name": f"Dragon {i}"} for i in range(20)] + [{"name": f"Bear {i}"} for i in range(50)])}]
    monkeypatch.setattr(brew, "_build_pools", lambda *a, **k: CandidatePools())

    with pytest.raises(br.RulesStarvedError) as exc:
        bb.StubBrewBuilder().build(session, r)
    msg = str(exc.value)
    assert DRAGON_RULE["label"] in msg and "removed 20" in msg
