"""Brew verification matrix run through the PRODUCTION builder (StubBrewBuilder, the
default registered in brew_builder), not the harness ReferenceBrewBuilder.

Same 210 cells as test_brew_verification_matrix (7 commanders x 3 power x sources x 5
EDHREC states), same fixture catalog / fault-injected EDHREC / FakeRedis, offline.

Hard assertions (legality, must-exclude, banned traps, resolved names, validator minus
deck-size) must hold in every cell. Known production findings are strict xfails so they
flip loudly when fixed:
  * deck size: StubBrewBuilder has no basic-land fill, so a small in-identity pool
    (krenko 80, kozilek 41 with the fixture catalog) yields an undersized deck
    (it reports missing_slots instead).
  * power trend: estimated_power ties low/mid for several commanders and is flat for
    pool-limited decks (t_15659d3c).
"""
from __future__ import annotations

import json
from unittest import mock

import pytest

from src.server.domain.analytics.card_eval.edhrec_data import CACHE_PREFIX, parse_page
from src.server.domain.analytics.providers.edhrec import commander_slug
from src.server.domain.draft import brew_builder
from src.server.domain.draft import deck_validator as dv
from src.server.domain.draft.brew_builder import StubBrewBuilder
from tests.support import deck_assertions as da
from tests.support.brew_harness import EdhrecState, edhrec_payload, run_brew
from tests.support.brew_matrix import ROWS, matrix_catalog
from tests.test_brew_verification_matrix import CASES, POWERS, STATES, state_label

_SIZE_ONLY = {"deck_size", "size", "count"}


_COLORS = ("W", "U", "B", "R", "G", "C")


class _SeedingStub(StubBrewBuilder):
    """Production builder; only seeds the EDHREC page cache the scorer reads (it never
    fetches), matching the harness EDHREC state."""

    def __init__(self, state, kind):
        self.state, self.kind = state, kind

    def build(self, session, r):
        if self.state is EdhrecState.AVAILABLE or (
                self.state is EdhrecState.DEGRADED and self.kind == "partial"):
            names = [f"Fixture {c} Card {i:02d}" for c in _COLORS for i in range(11, 31)]
            page = parse_page(edhrec_payload(names, partial=self.state is EdhrecState.DEGRADED))
            r.set(CACHE_PREFIX + commander_slug(list(session["commanders"])), json.dumps(page))
        return super().build(session, r)


def _run(key, power, sources, state, kind):
    cat = matrix_catalog()
    builder = _SeedingStub(state, kind)
    seen = []
    real = brew_builder._cached_edhrec

    def spy(r, cmds):
        out = real(r, cmds)
        seen.append(len(out))
        return out

    with mock.patch.object(brew_builder, "_cached_edhrec", spy):
        res = run_brew(list(ROWS[key].commanders), power, sources=sources, edhrec=state,
                       degraded_kind=kind, builder=builder, catalog=cat)
    res.scorer_edhrec_sizes = seen
    return res, cat


@pytest.mark.parametrize("key,power,sources,state,kind", CASES,
                         ids=[f"{k}-{p}-{'src' if s else 'nosrc'}-{state_label(st, kd)}"
                              for k, p, s, st, kd in CASES])
def test_production_cell_legality(key, power, sources, state, kind):
    row = ROWS[key]
    res, cat = _run(key, power, sources, state, kind)
    deck = res.deck
    assert isinstance(res.builder, StubBrewBuilder)
    # the scorer must actually receive state-appropriate EDHREC data
    assert res.scorer_edhrec_sizes, "scorer never read EDHREC cache"
    n = res.scorer_edhrec_sizes[0]
    if state is EdhrecState.AVAILABLE:
        assert n >= 100, n
    elif state is EdhrecState.DEGRADED and kind == "partial":
        assert 0 < n < 100, n
    else:
        assert n == 0, n
    da.assert_singleton(deck, cat)
    da.assert_color_identity(deck, cat)
    assert da.commander_identity(deck, cat) == row.identity
    da.assert_not_banned(deck, cat)
    da.assert_names_resolved(deck)
    da.assert_no_guid_names(deck)
    names = {dv.normalize_name(c["name"]) for c in deck["cards"]}
    bad = [n for n in (*row.must_exclude, *row.banned_traps) if dv.normalize_name(n) in names]
    assert not bad, bad
    out = dv.validate_deck(dv.DraftDeck.from_builder_draft(deck), cat)
    other = [c for c in out.codes() if str(getattr(c, "value", c)).lower() not in _SIZE_ONLY
             and "size" not in str(getattr(c, "value", c)).lower()]
    assert not other, other
    # production reports a short deck honestly instead of overfilling
    assert da.total_cards(deck) + deck["missing_slots"] == 100


UNDERSIZED = {"krenko", "kozilek"}


_SIZE_PARAMS = [
    pytest.param(k, marks=pytest.mark.xfail(
        strict=True, reason="production: no basic-land fill; small in-identity fixture pool -> <100 cards"))
    if k in UNDERSIZED else k for k in ROWS]


@pytest.mark.parametrize("key", _SIZE_PARAMS)
def test_production_deck_size(key):
    res, _ = _run(key, "mid", True, EdhrecState.AVAILABLE, "slow")
    assert da.total_cards(res.deck) == 100


@pytest.mark.parametrize("sources", [True, False])
@pytest.mark.parametrize("state,kind", STATES, ids=[state_label(*s) for s in STATES])
@pytest.mark.parametrize("key", list(ROWS))
def test_production_power_trend_weak(key, sources, state, kind):
    """Floor that must hold everywhere: high never estimates below low, target_power rises."""
    lo, mid, hi = (_run(key, p, sources, state, kind)[0] for p in POWERS)
    assert lo.deck["target_power"] < mid.deck["target_power"] < hi.deck["target_power"]
    assert lo.deck["estimated_power"] <= hi.deck["estimated_power"]
    assert lo.estimated_bracket <= hi.estimated_bracket


# With EDHREC cache data actually reaching the scorer, low and mid tie for these commanders
# (tracked by t_15659d3c); strict xfail so it flips loudly when the power trend is fixed.
_LOW_MID_TIE = {"meren", "korvold", "thrasios_tymna", "alesha", "wort"}
_STRICT_PARAMS = [
    pytest.param(k, marks=pytest.mark.xfail(
        strict=True, reason="production: low and mid estimated_power tie (t_15659d3c)"))
    if k in _LOW_MID_TIE else k for k in ROWS if k not in UNDERSIZED]


@pytest.mark.parametrize("key", _STRICT_PARAMS)
def test_production_power_trend_strict(key):
    est = [_run(key, p, True, EdhrecState.AVAILABLE, "slow")[0].deck["estimated_power"] for p in POWERS]
    assert est[0] < est[1] < est[2], est


@pytest.mark.parametrize("key", [k for k in ROWS if k not in UNDERSIZED])
def test_production_power_trend_monotone(key):
    est = [_run(key, p, True, EdhrecState.AVAILABLE, "slow")[0].deck["estimated_power"] for p in POWERS]
    assert est[0] <= est[1] <= est[2], est


@pytest.mark.xfail(strict=True, reason="production: pool-limited decks (krenko/kozilek) estimate identical power at every level")
@pytest.mark.parametrize("key", sorted(UNDERSIZED))
def test_production_power_trend_pool_limited(key):
    est = [_run(key, p, True, EdhrecState.AVAILABLE, "slow")[0].deck["estimated_power"] for p in POWERS]
    assert est[0] < est[2], est
