"""Brew verification suite (brew-test-matrix-v1, docs/brew-test-matrix.md).

Full cross product, no sampling (each cell is a few ms, all offline):
  7 commander rows x 3 power x sources{yes,no} x 5 EDHREC states
  (available, unavailable, degraded-slow, degraded-timeout, degraded-partial) = 210 runs.

Determinism: no network, FakeRedis, fixture catalog, scripted EDHREC faults with no-op
sleeps, no randomness anywhere in the harness. Fixture caveat: the builder under test is
the harness' ReferenceBrewBuilder driven by a "trap-first" subclass (it is offered every
MUST_EXCLUDE / banned-trap card before anything else), so the assertions prove the
filtering path, not just that bad cards were never offered.

Report: `python -m tests.test_brew_verification_matrix [out.md]` prints the per-run table.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Dict, List, Optional
from unittest import mock

import pytest

import tests.support.brew_harness as bh
from src.server.domain.analytics import brackets as gc_mod
from src.server.domain.draft import deck_validator as dv
from src.server.domain.draft import reconstruct_pools as rp
from tests.support import deck_assertions as da
from tests.support.brew_harness import EdhrecState, ReferenceBrewBuilder, run_brew
from tests.support.brew_matrix import ROWS, Row, matrix_catalog

POWERS = bh.POWER_LEVELS
STATES = [(EdhrecState.AVAILABLE, "slow"), (EdhrecState.UNAVAILABLE, "slow"),
          (EdhrecState.DEGRADED, "slow"), (EdhrecState.DEGRADED, "timeout"),
          (EdhrecState.DEGRADED, "partial")]


def state_label(state, kind):
    return state.value if state is not EdhrecState.DEGRADED else f"degraded-{kind}"


GC_SNAPSHOT = gc_mod.load_fallback().cards
CHECKS = ("size", "singleton", "identity", "banned", "names", "must_exclude",
          "must_allow", "validator")


class TrapFirstBuilder(ReferenceBrewBuilder):
    """Offers every trap / must-allow card BEFORE normal selection, through a channel that
    does not depend on deck sources: the EDHREC page (prepended) so sources=False cells keep
    an EMPTY donor list. When EDHREC is down the page is replaced by a traps-only page and the
    error is recorded on `edhrec_error` (so the traps are still offered)."""

    def __init__(self, catalog, row: Row):
        super().__init__(catalog)
        self.row = row

    def _offered(self, session):
        offered = [n for n in (*self.row.must_exclude, *self.row.banned_traps,
                               *self.row.soft_exclude, *self.row.must_allow)
                   if not gc_mod.is_game_changer(n, GC_SNAPSHOT)]  # GC count is power-driven only
        # Power-level GCs are offered first (0 / 2 / 4 for low / mid / high).
        want = bh._GAME_CHANGERS_BY_POWER[str(session.get("power_level"))]
        return list(self.row.gc_picks[:want]) + offered

    def build(self, session, r):
        real = bh.edhrec_data.get_page
        offered = self._offered(session)

        def get_page(rr, commanders, *a, **kw):
            extra = {n: {"name": n} for n in offered}
            try:
                page = real(rr, commanders, *a, **kw)
            except bh.EDHRecError as exc:
                self._err = str(exc)
                return {"cards": extra}
            self._err = None
            return {**page, "cards": {**extra, **page["cards"]}}

        self._err = None
        with mock.patch.object(bh.edhrec_data, "get_page", get_page):
            out = super().build(session, r)
        self.edhrec_error = self._err
        return out


@dataclass
class Cell:
    row: Row
    power: str
    sources: bool
    state: str
    est: float
    bracket: int
    power_source: str
    cards: int
    gcs: int
    checks: Dict[str, bool]
    notes: List[str]
    draft_est: Optional[float] = None   # released draft's own estimated_power (not the analytics estimate)
    builder: object = None


def _gc_count(deck) -> int:
    return len({c["name"] for c in deck["cards"] if gc_mod.is_game_changer(c["name"], GC_SNAPSHOT)})


def _verdict(fn) -> bool:
    try:
        fn()
        return True
    except AssertionError:
        return False


def run_cell(row: Row, power: str, sources: bool, state: EdhrecState, kind: str = "slow",
             builder_factory=None) -> Cell:
    """builder_factory(cat, row) -> builder; default is the harness TrapFirstBuilder
    (reference path). tests/test_brew_matrix_production.py passes the production builder."""
    cat = matrix_catalog()
    builder = builder_factory(cat, row) if builder_factory else TrapFirstBuilder(cat, row)
    with mock.patch.object(bh, "GAME_CHANGER_NAMES", []):
        res = run_brew(list(row.commanders), power, sources=sources, edhrec=state,
                       degraded_kind=kind, builder=builder, catalog=cat)
    deck = res.deck
    names = {dv.normalize_name(c["name"]) for c in deck["cards"]}

    def must_exclude():
        bad = [n for n in (*row.must_exclude, *row.banned_traps) if dv.normalize_name(n) in names]
        assert not bad, bad

    def must_allow():  # each allowed card is accepted by the validator, independent of selection
        for n in row.must_allow:
            assert_allowed(cat, row, n)

    def validator():
        out = dv.validate_deck(dv.DraftDeck.from_builder_draft(deck), cat)
        assert out.valid, out.codes()

    checks = {
        "size": _verdict(lambda: da.assert_deck_size(deck)),
        "singleton": _verdict(lambda: da.assert_singleton(deck, cat)),
        "identity": _verdict(lambda: (da.assert_color_identity(deck, cat),
                                      _eq(da.commander_identity(deck, cat), row.identity))),
        "banned": _verdict(lambda: da.assert_not_banned(deck, cat)),
        "names": _verdict(lambda: (da.assert_names_resolved(deck), da.assert_no_guid_names(deck))),
        "must_exclude": _verdict(must_exclude),
        "must_allow": _verdict(must_allow),
        "validator": _verdict(validator),
    }
    notes = [f"soft: {n} present (no mana with colorless commander)"
             for n in row.soft_exclude if dv.normalize_name(n) in names]
    return Cell(row, power, sources, state_label(state, kind), res.estimated_power,
                res.estimated_bracket, res.power_source,
                da.total_cards(deck), _gc_count(deck), checks, notes,
                draft_est=deck.get("estimated_power"), builder=builder)


_CARD_RULES = {dv.RuleCode.COLOR_IDENTITY, dv.RuleCode.SINGLETON, dv.RuleCode.BANNED,
               dv.RuleCode.NOT_LEGAL, dv.RuleCode.UNKNOWN_LEGALITY, dv.RuleCode.UNKNOWN_CARD,
               dv.RuleCode.INVALID_QUANTITY}


def assert_allowed(cat, row: Row, name: str) -> None:
    """Probe deck = commander(s) + the card (x7 for Seven Dwarves). Deck-size and other
    whole-deck errors are expected for a probe; any per-card legality error is a failure."""
    qty = 7 if name == "Seven Dwarves" else 1
    res = dv.validate_deck(dv.DraftDeck(list(row.commanders), [dv.DraftCard(name, qty)]), cat)
    bad = [e for e in res.errors if str(getattr(e.code, "value", e.code)) in
           {c.value for c in _CARD_RULES} and (not e.cards or name in e.cards)]
    assert not bad, [e.to_dict() for e in bad]


def test_must_allow_probe_rejects_a_bad_card():
    assert_allowed(matrix_catalog(), ROWS["krenko"], "Gut Shot")
    with pytest.raises(AssertionError):
        assert_allowed(matrix_catalog(), ROWS["krenko"], "Kitchen Finks")


def _eq(a, b):
    assert a == b, f"{sorted(a)} != {sorted(b)}"


CASES = [(k, p, s, st, kd) for k in ROWS for p in POWERS for s in (True, False) for st, kd in STATES]


@pytest.mark.parametrize("key,power,sources,state,kind", CASES,
                         ids=[f"{k}-{p}-{'src' if s else 'nosrc'}-{state_label(st, kd)}"
                              for k, p, s, st, kd in CASES])
def test_cell_legality(key, power, sources, state, kind):
    cell = run_cell(ROWS[key], power, sources, state, kind)
    failed = [c for c, ok in cell.checks.items() if not ok]
    assert not failed, f"{key}/{power}/{sources}/{cell.state}: failed {failed}"
    assert cell.cards == 100


@pytest.mark.parametrize("sources", [True, False])
@pytest.mark.parametrize("state,kind", STATES, ids=[state_label(*s) for s in STATES])
@pytest.mark.parametrize("key", list(ROWS))
def test_power_trend(key, sources, state, kind):
    lo, mid, hi = (run_cell(ROWS[key], p, sources, state, kind) for p in POWERS)
    assert lo.est <= mid.est <= hi.est, (lo.est, mid.est, hi.est)
    assert hi.est > lo.est
    assert lo.bracket <= mid.bracket <= hi.bracket and hi.bracket > lo.bracket
    assert lo.gcs <= mid.gcs <= hi.gcs
    assert lo.gcs == 0, "low power must have 0 Game Changers"
    assert mid.gcs <= 3, "mid power allows at most 3 Game Changers"
    assert lo.bracket <= 2 and mid.bracket <= 3 and hi.bracket <= 4


def test_power_source_follows_analytics_state():
    for key in ROWS:
        ok = run_cell(ROWS[key], "mid", False, EdhrecState.AVAILABLE)
        down = run_cell(ROWS[key], "mid", False, EdhrecState.UNAVAILABLE)
        assert ok.power_source == "commandersalt" and down.power_source == "bracket_band"


def test_kozilek_uses_wastes_not_basic_types():
    cell_deck = _deck(ROWS["kozilek"])
    names = {c["name"] for c in cell_deck["cards"]}
    assert "Wastes" in names and not names & {"Plains", "Island", "Swamp", "Mountain", "Forest"}


def test_colorless_soft_warning_is_reported_not_failed():
    # Matrix: Command Tower / Arcane Signet SHOULD NOT be in a colorless deck (soft).
    # The reference builder includes them, so this surfaces as a note in the report.
    cell = run_cell(ROWS["kozilek"], "mid", False, EdhrecState.AVAILABLE)
    assert all(cell.checks.values())
    assert cell.notes  # documented soft finding, see PR body


def test_alesha_and_wort_identity_come_from_rules_text_and_hybrid_cost():
    assert ROWS["alesha"].identity == frozenset("WBR")
    deck = _deck(ROWS["alesha"])
    present = {c["name"] for c in deck["cards"]}
    assert "Swords to Plowshares" in present  # legal only via {W/B} in rules text


def _deck(row: Row):
    cat = matrix_catalog()
    with mock.patch.object(bh, "GAME_CHANGER_NAMES", []):
        return run_brew(list(row.commanders), "mid", sources=False, builder=TrapFirstBuilder(cat, row),
                        catalog=cat).deck


# --------------------------------------------------------------------------
# Section 3 pair fixtures, section 4 singleton rules
# --------------------------------------------------------------------------
CAT = matrix_catalog()


@pytest.mark.parametrize("a,b,legal", [
    ("Thrasios, Triton Hero", "Tymna the Weaver", True),
    ("Thrasios, Triton Hero", "Kraum, Ludevic's Opus", True),
    ("Pir, Imaginative Rascal", "Toothy, Imaginary Friend", True),
    ("Thrasios, Triton Hero", "Pir, Imaginative Rascal", False),
    ("Wilson, Refined Grizzly", "Raised by Giants", True),
    ("Wilson, Refined Grizzly", "Tymna the Weaver", False),
])
def test_commander_pair_legality(a, b, legal):
    assert (dv.check_partner_pair(CAT.get(a), CAT.get(b)) is None) is legal


def test_background_never_solo_commander():
    assert not CAT.get("Raised by Giants").can_be_commander


def test_three_commanders_illegal():
    deck = dv.DraftDeck(["Thrasios, Triton Hero", "Tymna the Weaver", "Kraum, Ludevic's Opus"], [])
    assert dv.RuleCode.COMMANDER_COUNT in dv.validate_deck(deck, CAT).codes()


def test_banned_commander_flagged():
    deck = dv.DraftDeck(["Nadu, Winged Wisdom"], [])
    assert dv.RuleCode.BANNED in dv.validate_deck(deck, CAT).codes()


def _singleton(commander, name, qty):
    return {"commanders": [commander], "cards": [{"name": commander, "quantity": 1},
                                                  {"name": name, "quantity": qty}]}


def test_seven_dwarves_cap():
    da.assert_singleton(_singleton("Krenko, Mob Boss", "Seven Dwarves", 7), CAT)
    with pytest.raises(da.DeckAssertionError):
        da.assert_singleton(_singleton("Krenko, Mob Boss", "Seven Dwarves", 8), CAT)


def test_relentless_rats_still_obeys_identity_under_krenko():
    deck = _singleton("Krenko, Mob Boss", "Relentless Rats", 5)
    da.assert_singleton(deck, CAT)  # unlimited copies are fine...
    with pytest.raises(da.DeckAssertionError, match="outside commander identity"):
        da.assert_color_identity(deck, CAT)  # ...but B is outside {R}


def test_snow_covered_basic_illegal_under_colorless_commander():
    deck = _singleton("Kozilek, the Great Distortion", "Snow-Covered Plains", 1)
    with pytest.raises(da.DeckAssertionError):
        da.assert_color_identity(deck, CAT)


# --------------------------------------------------------------------------
# Production findings from the PR #457 review (t_d461913b). strict xfail: these
# document REAL defects (verified failing today); they flip to XPASS->fail when
# fixed so the marker must then be removed.
# --------------------------------------------------------------------------
def test_F2_identity_from_rules_text_when_field_missing():
    info = dv.CardInfo.from_record({"name": "Alesha", "mana_cost": "{2}{R}",
                                    "type_line": "Legendary Creature — Human",
                                    "oracle_text": "you may pay {W/B}{W/B}."})
    assert info.color_identity == frozenset("WBR")


def test_F3_hybrid_list_form_not_dropped():
    assert dv.parse_color_identity(["R/G"]) == frozenset("RG")


def test_F4_partial_partner_identity_not_silent():
    """F4 (fixed by #468): a partner missing from the catalog yields None (unknown), not a partial union."""
    pool_cat = rp.InMemoryCatalog([rp.CatalogCard("Thrasios, Triton Hero", frozenset("GU"))])
    assert rp.identity_of_cards(["Thrasios, Triton Hero", "No Such Partner"], pool_cat) is None
    assert rp.identity_of_cards(["Thrasios, Triton Hero"], pool_cat) == frozenset("GU")


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
def report_markdown() -> str:
    rows = [run_cell(ROWS[k], p, s, st, kd) for k in ROWS for p in POWERS for s in (True, False)
            for st, kd in STATES]
    head = ("| commander | power requested | power estimated | sources | EDHREC | cards | "
            + " | ".join(CHECKS) + " |\n|" + "---|" * (6 + len(CHECKS)) + "\n")
    lines = [
        f"| {' + '.join(c.row.commanders)} | {c.power} | {c.est:.1f} ({c.power_source}) | "
        f"{'yes' if c.sources else 'no'} | {c.state} | {c.cards} | "
        + " | ".join("PASS" if c.checks[k] else "FAIL" for k in CHECKS) + " |"
        for c in rows]
    return head + "\n".join(lines) + "\n"


if __name__ == "__main__":
    md = report_markdown()
    if len(sys.argv) > 1:
        open(sys.argv[1], "w", encoding="utf-8").write(md)
    print(md)
