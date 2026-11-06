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

import pytest

from src.server.domain.draft.brew_builder import StubBrewBuilder
from tests.support.brew_harness import EdhrecState
from tests.support.brew_matrix import ROWS
from tests.test_brew_verification_matrix import CASES, POWERS, STATES, run_cell, state_label

_TREND_BUG = ("PENDING BUG: production StubBrewBuilder ignores session power_level, so the deck "
              "(and its Game Changers / estimated power / bracket) is identical across low/mid/high; "
              "the power-trend assertions cannot hold (e.g. korvold low has 2 GCs, krenko est 9.0 "
              "== 9.0 and bracket 4/4/4). Remove this marker when power-aware picking lands.")
# cell-id -> reason. All 210 legality cells pass on production; every power-trend cell fails.
KNOWN_FAILURES: dict = {
    f"prod-trend-{k}-{'src' if s else 'nosrc'}-{state_label(st, kd)}": _TREND_BUG
    for k in ROWS for s in (True, False) for st, kd in STATES
}


def _prod(cat, row):
    return StubBrewBuilder()


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
    cell = run_cell(ROWS[key], power, sources, state, kind, builder_factory=_prod)
    ctx = (f"commander={'+'.join(cell.row.commanders)} power={power} sources={sources} "
           f"edhrec={cell.state}")
    failed = [c for c, ok in cell.checks.items() if not ok]
    assert not failed, f"{ctx}: failed checks {failed}"
    assert cell.cards == 100, f"{ctx}: failing assertion cards==100, got {cell.cards}"


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
    lo, mid, hi = (run_cell(ROWS[key], p, sources, state, kind, builder_factory=_prod)
                   for p in POWERS)
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
        ok = run_cell(ROWS[key], "mid", False, EdhrecState.AVAILABLE, builder_factory=_prod)
        down = run_cell(ROWS[key], "mid", False, EdhrecState.UNAVAILABLE, builder_factory=_prod)
        assert ok.power_source == "commandersalt", key
        assert down.power_source == "bracket_band", key
