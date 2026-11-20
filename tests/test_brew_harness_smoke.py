"""Smoke tests for tests/support (Brew harness + deck assertion helpers)."""
import copy
import unittest

import pytest

from tests.support import deck_assertions as da
from tests.support.brew_harness import (
    EdhrecState, build_fixture_catalog, run_brew,
)

ATRAXA = "Atraxa, Praetors' Voice"
PAIR = ["Tymna the Weaver", "Thrasios, Triton Hero"]  # union WBUG


@pytest.mark.parametrize("power", ["low", "mid", "high"])
@pytest.mark.parametrize("sources", [True, False])
@pytest.mark.parametrize("state", list(EdhrecState))
def test_generated_deck_passes_all_assertions(power, sources, state):
    res = run_brew(ATRAXA, power, sources=sources, edhrec=state)
    da.assert_valid_brew_deck(res.deck, res.catalog)
    assert 1.0 <= res.estimated_power <= 10.0


def test_partner_pair_uses_identity_union():
    res = run_brew(PAIR, "mid")
    da.assert_valid_brew_deck(res.deck, res.catalog)
    assert da.commander_identity(res.deck, res.catalog) == frozenset("WUBG")
    # Atraxa-only-colour check: the same deck is too wide for Tymna alone.
    solo = dict(res.deck, commanders=[PAIR[0]])
    with pytest.raises(da.DeckAssertionError, match="outside commander identity"):
        da.assert_color_identity(solo, res.catalog)


def test_sources_toggle():
    with_src = run_brew(ATRAXA, "mid", sources=True)
    without = run_brew(ATRAXA, "mid", sources=False)
    assert with_src.session["donor_deck_ids"] and not without.session["donor_deck_ids"]
    assert any(c["tier_label"] == "previous_deck" for c in with_src.deck["cards"])
    assert not any(c["tier_label"] == "previous_deck" for c in without.deck["cards"])


def test_edhrec_states_are_injected():
    ok = run_brew(ATRAXA, "mid", sources=False, edhrec=EdhrecState.AVAILABLE)
    assert ok.edhrec_error is None and ok.builder.edhrec_used > 0

    down = run_brew(ATRAXA, "mid", sources=False, edhrec=EdhrecState.UNAVAILABLE)
    assert down.edhrec_error and down.builder.edhrec_used == 0
    assert down.power_source == "bracket_band"
    assert len(down.edhrec_calls) >= 1  # real client retried against the fault

    slow = run_brew(ATRAXA, "mid", sources=False, edhrec=EdhrecState.DEGRADED, degraded_kind="slow")
    assert slow.edhrec_error is None and len(slow.edhrec_calls) == 3  # 2 timeouts, then ok

    timeout = run_brew(ATRAXA, "mid", sources=False, edhrec=EdhrecState.DEGRADED, degraded_kind="timeout")
    assert timeout.edhrec_error and timeout.builder.edhrec_used == 0

    part = run_brew(ATRAXA, "mid", sources=False, edhrec=EdhrecState.DEGRADED, degraded_kind="partial")
    assert 0 < part.builder.edhrec_used < ok.builder.edhrec_used


def test_power_estimate_tracks_requested_level():
    est = {p: run_brew(ATRAXA, p, edhrec=EdhrecState.UNAVAILABLE).estimated_power
           for p in ("low", "mid", "high")}
    assert est["low"] < est["mid"] <= est["high"]


# ---- helpers must FAIL on a deliberately bad deck -----------------------

@pytest.fixture
def good():
    return run_brew(ATRAXA, "mid")


def _mutate(res, fn):
    deck = copy.deepcopy(res.deck)
    fn(deck)
    return deck


def test_bad_fixture_deck_is_rejected(good):
    cat = good.catalog
    base = good.deck["cards"]
    cases = [
        (da.assert_deck_size, lambda d: d["cards"].pop(), "99 cards"),
        (da.assert_deck_size, lambda d: d["cards"].append({"name": "Fixture C Card 30", "quantity": 1}), "101"),
        (da.assert_names_resolved, lambda d: d["cards"][5].update(name=""), "unresolved"),
        (da.assert_names_resolved, lambda d: d["cards"][5].update(name=None), "unresolved"),
        (da.assert_no_guid_names, lambda d: d["cards"][5].update(name="3f2b8c1e-9a4d-4e7b-8c21-0123456789ab"), "GUID"),
        (da.assert_no_guid_names, lambda d: d["cards"][5].update(name="Sol Ring 3f2b8c1e9a4d4e7b8c210123456789ab"), "GUID"),
        (lambda d: da.assert_singleton(d, cat), lambda d: d["cards"][5].update(name=d["cards"][6]["name"]), "singleton"),
        (lambda d: da.assert_color_identity(d, cat), lambda d: d["cards"][-1].update(name="Lightning Bolt (off-colour trap)"), "outside commander identity"),
        (lambda d: da.assert_not_banned(d, cat), lambda d: d["cards"][-1].update(name="Mana Crypt"), "banned"),
        (lambda d: da.assert_not_banned(d), lambda d: d["cards"][-1].update(name="Black Lotus"), "banned"),
    ]
    for check, fn, msg in cases:
        bad = _mutate(good, fn)
        with pytest.raises(da.DeckAssertionError, match=msg):
            check(bad)
    assert len(base) == 100


def test_singleton_exceptions_allowed(good):
    deck = _mutate(good, lambda d: d["cards"].__setitem__(
        slice(-3, None), [{"name": "Plains", "quantity": 3}]))
    da.assert_singleton(deck, good.catalog)  # basics
    cat = build_fixture_catalog()
    rats = {"commanders": ["Tymna the Weaver"], "cards": [{"name": "Relentless Rats", "quantity": 5}]}
    da.assert_singleton(rats, cat)  # "any number" via oracle text


def test_combined_validator_flags_bad_deck(good):
    bad = _mutate(good, lambda d: d["cards"][-1].update(name="Mana Crypt"))
    with pytest.raises(da.DeckAssertionError):
        da.assert_valid_brew_deck(bad, good.catalog)


def test_malformed_quantities_rejected(good):
    for q in (0, -1, 1.0, True, "1"):
        bad = _mutate(good, lambda d, q=q: d["cards"][5].update(quantity=q))
        with pytest.raises(da.DeckAssertionError, match="quantit"):
            da.assert_deck_size(bad)
    # commander at quantity 0 + 100 basics must not pass
    bad = {"commanders": [ATRAXA], "cards": [{"name": ATRAXA, "quantity": 0}, {"name": "Plains", "quantity": 100}]}
    with pytest.raises(da.DeckAssertionError):
        da.assert_deck_size(bad)


def test_singleton_canonicalizes_dfc_names():
    from tests.support.brew_harness import _info
    cat = build_fixture_catalog()
    cat.add(_info("Bala Ged Recovery // Bala Ged Sanctuary", "G", ("Sorcery",)))
    deck = {"commanders": [], "cards": [{"name": "Bala Ged Recovery", "quantity": 1},
                                        {"name": "Bala Ged Recovery // Bala Ged Sanctuary", "quantity": 1}]}
    with pytest.raises(da.DeckAssertionError, match="singleton"):
        da.assert_singleton(deck, cat)


def test_fixture_partners_are_a_legal_pair_and_redis_seeded():
    from src.server.domain.draft.deck_validator import check_partner_pair, catalog_from_redis
    from tests.support.brew_harness import FakeRedis, seed_catalog
    cat = build_fixture_catalog()
    assert check_partner_pair(cat.get(PAIR[0]), cat.get(PAIR[1])) is None
    r = FakeRedis()
    seed_catalog(r, cat)
    seeded = catalog_from_redis(r, [PAIR[0], "Mana Crypt", "Fixture W Card 01"])
    assert seeded.get("Mana Crypt").commander_legality == "banned"
    assert seeded.get(PAIR[0]).color_identity == frozenset("WB")
