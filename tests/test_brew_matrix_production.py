"""Brew verification matrix driven through the PRODUCTION builder.

The reference matrix (tests/test_brew_verification_matrix.py) runs only the harness
ReferenceBrewBuilder. This module reuses the SAME cell definitions (ROWS, POWERS, STATES,
run_cell and its legality checks) but installs the production default builder
(`brew_builder.StubBrewBuilder`, what `get_builder()` returns) and runs the full brew
pipeline: start_brew -> trigger_build -> execute_build -> brew_gate.release_draft.

Scope: the FULL 7 x 3 x 2 x 5 = 210 cell cross product (all offline, ms each), so every
power level, source configuration and EDHREC state is covered -- no sampling.

Not covered: the LLM-backed `BrewBuilder` (needs a live model; not runnable offline).

Assertions are identical to the reference path (same `run_cell` checks, same
`test_power_trend` expectations); none are weakened. Known production defects are marked
xfail(strict=True) via KNOWN_FAILURES with a reason; strict means a fix flips them to
XPASS->fail so the entry must be removed.

Note: the production builder never receives the trap cards (they come from a reference-only
EDHREC injection), so `must_exclude` here proves the production pool/gate leaks nothing,
not that the filter rejects offered traps.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.server.domain.analytics.card_eval import edhrec_data
from src.server.domain.analytics.providers.edhrec import commander_slug
from src.server.domain.draft.brew_builder import StubBrewBuilder, _cached_edhrec
from tests.support.brew_harness import _COLORS, EdhrecState, edhrec_payload
from tests.support.brew_matrix import ROWS
from tests.test_brew_verification_matrix import CASES, POWERS, STATES, run_cell, state_label

_TREND_BUG = ("PENDING BUG: production StubBrewBuilder does not pick cards by power_level, so the "
              "released drafts do not follow low/mid/high. Measured on the draft's own estimated_power / "
              "bracket / Game Changers: low power has Game Changers (alesha, korvold, meren, wort), "
              "bracket is flat (thrasios_tymna 4/4/4), draft estimate is flat or non-monotonic "
              "(kozilek 8.4/8.4/8.4, wort 6.3/7.5/7.2). Remove this marker when power-aware picking lands.")
# cell-id -> reason. All 210 legality cells pass on production; every power-trend cell fails.
KNOWN_FAILURES: dict = {
    f"prod-trend-{k}-{'src' if s else 'nosrc'}-{state_label(st, kd)}": _TREND_BUG
    for k in ROWS for s in (True, False) for st, kd in STATES
}


class ProdBuilder(StubBrewBuilder):
    """Production builder. The production path (builder + gate) reads EDHREC ONLY from the
    Redis cache (`card_eval:edhrec:<slug>`), never over HTTP, so the EDHREC state is modelled
    by what is cached (the harness' patched EDHRecClient is not on this path):
      available        -> full page cached
      degraded-partial -> partial page cached
      unavailable / degraded-slow / degraded-timeout -> nothing cached (fetch failures are
        invisible to cache-only readers; HTTP retry/timeout is covered at the fetch boundary,
        tests/test_edhrec_provider.py)."""

    def __init__(self, row, state, kind):
        self.row, self.state, self.kind = row, state, kind
        self.cached_edhrec = None

    def build(self, session, r):
        cmds = list(session["commanders"])
        full = [f"Fixture {c} Card {i:02d}" for c in _COLORS for i in range(11, 31)]
        want = {EdhrecState.AVAILABLE: False, (EdhrecState.DEGRADED, "partial"): True}.get(
            self.state if self.state is EdhrecState.AVAILABLE else (self.state, self.kind))
        if want is not None:
            page = edhrec_data.parse_page(edhrec_payload(full, partial=want))
            rec = {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **page}
            r.set(edhrec_data.CACHE_PREFIX + commander_slug(cmds), json.dumps(rec))
        self.cached_edhrec = len(_cached_edhrec(r, cmds))
        return super().build(session, r)


def _prod_factory(state, kind):
    return lambda cat, row: ProdBuilder(row, state, kind)


def _cell_id(k, p, s, st, kd):
    return f"prod-{k}-{p}-{'src' if s else 'nosrc'}-{state_label(st, kd)}"


def _params():
    out = []
    for k, p, s, st, kd in CASES:
        cid = _cell_id(k, p, s, st, kd)
        marks = []
        if cid in KNOWN_FAILURES:
            marks.append(pytest.mark.xfail(strict=True, reason=KNOWN_FAILURES[cid]))
        out.append(pytest.param(k, p, s, st, kd, id=cid, marks=marks))
    return out


@pytest.mark.parametrize("key,power,sources,state,kind", _params())
def test_prod_cell_legality(key, power, sources, state, kind):
    cell = run_cell(ROWS[key], power, sources, state, kind, builder_factory=_prod_factory(state, kind))
    ctx = (f"commander={'+'.join(cell.row.commanders)} power={power} sources={sources} "
           f"edhrec={cell.state}")
    failed = [c for c, ok in cell.checks.items() if not ok]
    assert not failed, f"{ctx}: failed checks {failed}"
    assert cell.cards == 100, f"{ctx}: failing assertion cards==100, got {cell.cards}"
    # production actually read the state-specific EDHREC cache entry
    n = cell.builder.cached_edhrec
    exp = {"available": lambda n: n > 0, "degraded-partial": lambda n: n > 0}.get(
        cell.state, lambda n: n == 0)
    assert exp(n), f"{ctx}: production read {n} cached EDHREC cards"


def _trend_params():
    out = []
    for key in ROWS:
        for sources in (True, False):
            for st, kd in STATES:
                cid = f"prod-trend-{key}-{'src' if sources else 'nosrc'}-{state_label(st, kd)}"
                marks = ([pytest.mark.xfail(strict=True, reason=KNOWN_FAILURES[cid])]
                         if cid in KNOWN_FAILURES else [])
                out.append(pytest.param(key, sources, st, kd, id=cid, marks=marks))
    return out


@pytest.mark.parametrize("key,sources,state,kind", _trend_params())
def test_prod_power_trend(key, sources, state, kind):
    lo, mid, hi = (run_cell(ROWS[key], p, sources, state, kind,
                            builder_factory=_prod_factory(state, kind)) for p in POWERS)
    # Released draft's own estimated_power (apply_power_estimate), NOT the harness analytics
    # estimate (fixed 3.0/6.5/8.5 when AVAILABLE, which passes even for identical decks).
    lo.est, mid.est, hi.est = lo.draft_est, mid.draft_est, hi.draft_est
    ctx = f"commander={key} sources={sources} edhrec={lo.state}"
    assert lo.est <= mid.est <= hi.est, f"{ctx}: est {(lo.est, mid.est, hi.est)}"
    assert hi.est > lo.est, ctx
    assert lo.bracket <= mid.bracket <= hi.bracket and hi.bracket > lo.bracket, \
        f"{ctx}: bracket {(lo.bracket, mid.bracket, hi.bracket)}"
    assert lo.gcs <= mid.gcs <= hi.gcs, f"{ctx}: gcs {(lo.gcs, mid.gcs, hi.gcs)}"
    assert lo.gcs == 0, f"{ctx}: low power must have 0 Game Changers"
    assert mid.gcs <= 3, f"{ctx}: mid power allows at most 3 Game Changers"
    assert lo.bracket <= 2 and mid.bracket <= 3 and hi.bracket <= 4, \
        f"{ctx}: bracket {(lo.bracket, mid.bracket, hi.bracket)} exceeds power cap"


def test_prod_power_source_follows_analytics_state():
    for key in ROWS:
        ok = run_cell(ROWS[key], "mid", False, EdhrecState.AVAILABLE,
                      builder_factory=_prod_factory(EdhrecState.AVAILABLE, "slow"))
        down = run_cell(ROWS[key], "mid", False, EdhrecState.UNAVAILABLE,
                        builder_factory=_prod_factory(EdhrecState.UNAVAILABLE, "slow"))
        assert ok.power_source == "commandersalt", key
        assert down.power_source == "bracket_band", key
