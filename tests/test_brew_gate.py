"""Unit tests for the Brew output gate (src/server/domain/draft/brew_gate.py).

The gate is the single place that guarantees a Brew result is a complete,
legal, fully-named Commander deck: exactly 100 cards, no GUID names, singleton,
colour identity, banlist, commander eligibility.

Run: pytest tests/test_brew_gate.py
"""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.draft import brew_gate
from src.server.domain.draft.deck_validator import (
    CardCatalog, CardInfo, DraftDeck, validate_deck,
)


def creature(name, ci="", legendary=False, legality="legal"):
    return CardInfo(
        name=name, color_identity=frozenset(ci), types=("Creature",),
        supertypes=("Legendary",) if legendary else (), commander_legality=legality,
    )


def spell(name, ci="", legality="legal", types=("Artifact",)):
    return CardInfo(name=name, color_identity=frozenset(ci), types=types, commander_legality=legality)


COMMANDER = "Esper Lord"


def make_catalog(extra=(), commander_ci="WU"):
    cards = [
        creature(COMMANDER, commander_ci, legendary=True),
        spell("Sol Ring"), spell("Arcane Signet"), spell("Command Tower", types=("Land",)),
        spell("Swords to Plowshares", "W", types=("Instant",)),
        spell("Counterspell", "U", types=("Instant",)),
        spell("Lightning Bolt", "R", types=("Instant",)),          # off-colour for WU
        spell("Mana Crypt", "", legality="banned"),                # banned
        creature("Plain Bear", "W"),
    ]
    return CardCatalog([*cards, *extra])


def draft_of(names, commander=COMMANDER, extra_cards=()):
    cards = [{"name": commander, "quantity": 1, "tier": 3}]
    cards += [{"name": n, "quantity": 1, "tier": 3} for n in names]
    cards += list(extra_cards)
    return {"id": "draft_0", "commanders": [commander], "cards": cards}


def total(draft):
    return sum(int(c.get("quantity", 1)) for c in draft["cards"])


def assert_legal(draft, catalog):
    deck = DraftDeck.from_builder_draft(draft)
    res = validate_deck(deck, catalog)
    assert res.valid, res.to_json()
    assert total(draft) == 100
    for c in draft["cards"]:
        assert c["name"].strip()
        assert not brew_gate.is_uuid_like(c["name"]), c["name"]


class FakeR:
    def __init__(self, store=None):
        self.store = store or {}

    def get(self, k):
        return self.store.get(k)


# --------------------------------------------------------------------------
# fill logic
# --------------------------------------------------------------------------

def test_truncated_deck_is_filled_to_100_without_edhrec_or_analytics():
    cat = make_catalog()
    out = brew_gate.finalize_draft(draft_of(["Plain Bear"]), cat, candidates=[])
    assert_legal(out, cat)


def test_fill_prefers_candidates_then_staples_then_basics():
    cat = make_catalog()
    out = brew_gate.finalize_draft(draft_of([]), cat, candidates=["Counterspell"])
    names = [c["name"] for c in out["cards"]]
    assert names.index("Counterspell") < names.index("Sol Ring")
    assert "Sol Ring" in names
    assert any(n in ("Plains", "Island") for n in names)
    assert_legal(out, cat)


def test_basics_match_commander_color_identity():
    cat = make_catalog()
    out = brew_gate.finalize_draft(draft_of([]), cat)
    basics = {c["name"] for c in out["cards"]} & {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}
    assert basics <= {"Plains", "Island"}
    assert basics  # something had to top up


def test_colorless_commander_fills_with_wastes_only_basics():
    cat = make_catalog(commander_ci="")
    out = brew_gate.finalize_draft(draft_of([]), cat)
    basics = {c["name"] for c in out["cards"]} & {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}
    assert basics <= {"Wastes"}
    assert_legal(out, cat)


def test_fill_never_uses_offcolor_or_banned_candidates():
    cat = make_catalog()
    out = brew_gate.finalize_draft(draft_of([]), cat, candidates=["Lightning Bolt", "Mana Crypt"])
    names = {c["name"] for c in out["cards"]}
    assert "Lightning Bolt" not in names and "Mana Crypt" not in names
    assert_legal(out, cat)


def test_fill_skips_candidates_unknown_to_catalog():
    cat = make_catalog()
    out = brew_gate.finalize_draft(draft_of([]), cat, candidates=["Totally Invented Card"])
    assert "Totally Invented Card" not in {c["name"] for c in out["cards"]}
    assert_legal(out, cat)


def test_filled_cards_are_marked_new_tier_with_rationale():
    cat = make_catalog()
    out = brew_gate.finalize_draft(draft_of([]), cat)
    filled = [c for c in out["cards"] if c["name"] == "Sol Ring"][0]
    assert filled["tier"] == 3 and filled["tier_label"] == "new" and filled["rationale"]


# --------------------------------------------------------------------------
# names / GUIDs
# --------------------------------------------------------------------------

def test_is_uuid_like():
    assert brew_gate.is_uuid_like(str(uuid.uuid4()))
    assert brew_gate.is_uuid_like(str(uuid.uuid4()).upper())
    assert brew_gate.is_uuid_like(uuid.uuid4().hex)
    assert not brew_gate.is_uuid_like("Sol Ring")
    assert not brew_gate.is_uuid_like("")  # emptiness is a separate check


def test_uuid_name_resolved_through_catalog_record():
    cat = make_catalog()
    oid = str(uuid.uuid4())
    r = FakeR({f"card:{oid}": json.dumps({"name": "Counterspell"})})
    out = brew_gate.finalize_draft(draft_of([oid]), cat, r=r)
    names = [c["name"] for c in out["cards"]]
    assert "Counterspell" in names and oid not in names
    assert_legal(out, cat)


def test_unresolvable_uuid_is_dropped_and_replaced_not_emitted():
    cat = make_catalog()
    bad = str(uuid.uuid4())
    out = brew_gate.finalize_draft(draft_of([bad, "Plain Bear"]), cat, r=FakeR())
    assert bad not in {c["name"] for c in out["cards"]}
    assert_legal(out, cat)


def test_blank_and_missing_names_are_dropped():
    cat = make_catalog()
    out = brew_gate.finalize_draft(
        draft_of([], extra_cards=[{"name": "", "quantity": 1}, {"quantity": 3}, {"name": None}]), cat)
    assert_legal(out, cat)


# --------------------------------------------------------------------------
# legality repair
# --------------------------------------------------------------------------

def test_offcolor_and_banned_cards_dropped_then_refilled():
    cat = make_catalog()
    out = brew_gate.finalize_draft(draft_of(["Lightning Bolt", "Mana Crypt", "Plain Bear"]), cat)
    names = {c["name"] for c in out["cards"]}
    assert not names & {"Lightning Bolt", "Mana Crypt"}
    assert "Plain Bear" in names
    assert_legal(out, cat)


def test_singleton_duplicates_clamped_but_basics_stack():
    cat = make_catalog()
    dup = [{"name": "Sol Ring", "quantity": 1}, {"name": "Plains", "quantity": 20}]
    out = brew_gate.finalize_draft(draft_of(["Sol Ring"], extra_cards=dup), cat)
    assert sum(c["quantity"] for c in out["cards"] if c["name"] == "Sol Ring") == 1
    assert sum(c["quantity"] for c in out["cards"] if c["name"] == "Plains") >= 20
    assert_legal(out, cat)


def test_oversized_deck_is_trimmed_to_100():
    cat = make_catalog()
    out = brew_gate.finalize_draft(
        draft_of([], extra_cards=[{"name": "Plains", "quantity": 150}]), cat)
    assert_legal(out, cat)


def test_commander_is_never_trimmed_or_dropped():
    cat = make_catalog()
    out = brew_gate.finalize_draft(
        draft_of([], extra_cards=[{"name": "Plains", "quantity": 150}]), cat)
    assert COMMANDER in {c["name"] for c in out["cards"]}


# --------------------------------------------------------------------------
# unrepairable -> raises, never returns an invalid deck
# --------------------------------------------------------------------------

def test_non_legendary_commander_raises():
    cat = make_catalog(extra=[creature("Plain Bear 2", "W")])
    with pytest.raises(brew_gate.BrewInvalidDeckError) as e:
        brew_gate.finalize_draft(draft_of([], commander="Plain Bear 2"), cat)
    assert "COMMANDER_ILLEGAL" in e.value.codes


def test_commander_missing_from_catalog_raises():
    cat = make_catalog()
    with pytest.raises(brew_gate.BrewInvalidDeckError) as e:
        brew_gate.finalize_draft(draft_of([], commander="Nobody Known"), cat)
    assert "UNKNOWN_CARD" in e.value.codes


def test_banned_commander_raises():
    cat = make_catalog(extra=[creature("Banned Boss", "W", legendary=True, legality="banned")])
    with pytest.raises(brew_gate.BrewInvalidDeckError):
        brew_gate.finalize_draft(draft_of([], commander="Banned Boss"), cat)


# --------------------------------------------------------------------------
# verify_draft: the pure last-line check
# --------------------------------------------------------------------------

def test_verify_accepts_legal_deck_and_rejects_99():
    cat = make_catalog()
    good = brew_gate.finalize_draft(draft_of([]), cat)
    brew_gate.verify_draft(good, cat)
    short = json.loads(json.dumps(good))
    short["cards"][-1]["quantity"] -= 1
    with pytest.raises(brew_gate.BrewInvalidDeckError) as e:
        brew_gate.verify_draft(short, cat)
    assert "DECK_SIZE" in e.value.codes


def test_verify_rejects_uuid_name_even_if_catalog_knew_it():
    oid = str(uuid.uuid4())
    cat = make_catalog(extra=[spell(oid)])
    good = brew_gate.finalize_draft(draft_of([]), cat)
    bad = json.loads(json.dumps(good))
    bad["cards"][-1]["name"] = oid
    with pytest.raises(brew_gate.BrewInvalidDeckError) as e:
        brew_gate.verify_draft(bad, cat)
    assert "UUID_NAME" in e.value.codes


def test_finalize_is_idempotent():
    cat = make_catalog()
    once = brew_gate.finalize_draft(draft_of(["Plain Bear"]), cat)
    twice = brew_gate.finalize_draft(once, cat)
    assert [(c["name"], c["quantity"]) for c in once["cards"]] == \
           [(c["name"], c["quantity"]) for c in twice["cards"]]


# --------------------------------------------------------------------------
# Wiring: every Brew build / accept goes through the gate
# --------------------------------------------------------------------------

import src.server.domain.draft.brew as brew
import src.server.domain.draft.brew_builder as brew_builder
import src.server.domain.draft.reconstruct_pools as reconstruct_pools
import src.server.domain.inventory.instances as instances
from tests.brew_catalog import seed_brew_catalog
from tests.test_brew import FakeRedis, make_deck

CMD = "Atraxa, Praetors' Voice"


@pytest.fixture
def env(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(reconstruct_pools, "redis_price_lookup",
                        lambda rr, refresh=True: (lambda names: {}))
    brew_builder.set_builder(brew_builder.StubBrewBuilder())
    return r


def build(r, donors=(), **kw):
    s = brew.start_brew(r, kw.pop("commander", CMD), list(donors), "owned_first")
    return brew.trigger_build(r, s["session_id"], sync=True)


def test_build_with_nothing_but_the_commander_yields_100_legal_cards(env):
    seed_brew_catalog(env, CMD)
    out = build(env)
    draft = out["drafts"][0]
    assert total(draft) == 100
    brew_gate.verify_draft(draft, __import__("src.server.domain.draft.deck_validator", fromlist=["x"])
                           .catalog_from_redis(env, [c["name"] for c in draft["cards"]]))


def test_big_donor_deck_is_not_truncated_to_25(env):
    names = [f"Donor Card {i}" for i in range(80)]
    seed_brew_catalog(env, CMD, names=names)
    env.set("decks", json.dumps([make_deck(deck_id=42, name="Big", status="physical",
                                           cards=[{"name": n, "quantity": 1} for n in names])]))
    out = build(env, donors=["archidekt:42"])
    draft = out["drafts"][0]
    assert total(draft) == 100
    assert sum(1 for c in draft["cards"] if c["name"].startswith("Donor Card")) > 60


def test_build_still_completes_when_pools_edhrec_and_analytics_are_unavailable(env, monkeypatch):
    seed_brew_catalog(env, CMD)

    def boom(*a, **k):
        raise RuntimeError("EDHREC / analytics unavailable")

    s = brew.start_brew(env, CMD, [], "owned_first")
    monkeypatch.setattr(brew, "_build_pools", boom)
    out = brew.trigger_build(env, s["session_id"], sync=True)
    assert out["status"] == brew.STATUS_DRAFTS_READY
    assert total(out["drafts"][0]) == 100


def test_guid_instance_name_never_reaches_the_draft(env):
    seed_brew_catalog(env, CMD)
    ghost = str(uuid.uuid4())
    instances.create_instance(env, ghost, ownership_status="in_collection")
    out = build(env)
    for c in out["drafts"][0]["cards"]:
        assert not brew_gate.is_uuid_like(c["name"])
    assert total(out["drafts"][0]) == 100


def test_guid_instance_name_with_catalog_record_resolves_to_real_name(env):
    seed_brew_catalog(env, CMD, names=["Sol Ring"])
    oid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sol"))
    env.set(f"card:{oid}", json.dumps({"name": "Sol Ring"}))
    instances.create_instance(env, oid, ownership_status="in_collection")
    out = build(env)
    names = [c["name"] for c in out["drafts"][0]["cards"]]
    assert "Sol Ring" in names and oid not in names


def test_unknown_commander_fails_the_build_and_stores_no_draft(env):
    out_session = brew.start_brew(env, "Nobody Known", [], "owned_first")
    with pytest.raises(brew_gate.BrewInvalidDeckError):
        brew.trigger_build(env, out_session["session_id"], sync=True)
    saved = brew.get_session(env, out_session["session_id"])
    assert saved["status"] == brew.STATUS_FAILED
    assert not saved.get("drafts")


def test_broken_builder_output_is_repaired_by_the_gate(env):
    seed_brew_catalog(env, CMD)

    class Bad(brew_builder.BaseBrewBuilder):
        def build(self, session, r):
            return [{"id": "draft_0", "commanders": [CMD], "cards": [
                {"name": CMD, "quantity": 1}, {"name": str(uuid.uuid4()), "quantity": 1},
                {"name": "", "quantity": 1}]}]

    brew_builder.set_builder(Bad())
    try:
        out = build(env)
    finally:
        brew_builder.set_builder(brew_builder.StubBrewBuilder())
    assert total(out["drafts"][0]) == 100
    assert all(c["name"] and not brew_gate.is_uuid_like(c["name"]) for c in out["drafts"][0]["cards"])


def test_accept_repairs_an_incomplete_edited_draft_to_exactly_100(env):
    seed_brew_catalog(env, CMD)
    out = build(env)
    sid = out["session_id"]
    session = brew.get_session(env, sid)
    session["drafts"][0]["cards"][-1]["quantity"] -= 1   # 99 cards
    brew._save_session(env, session)
    res = brew.accept_draft(env, sid)
    assert sum(c.get("quantity", 1) for c in res["created_decks"][0]["cards"]) == 100


def test_accept_refuses_a_draft_that_cannot_be_made_legal(env):
    seed_brew_catalog(env, CMD)
    out = build(env)
    sid = out["session_id"]
    session = brew.get_session(env, sid)
    session["commanders"] = ["Nobody Known"]
    session["drafts"][0]["commanders"] = ["Nobody Known"]
    brew._save_session(env, session)
    with pytest.raises(brew_gate.BrewInvalidDeckError):
        brew.accept_draft(env, sid)
