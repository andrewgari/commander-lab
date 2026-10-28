"""Deterministic power-level estimate for a deck (ARCHITECTURE.md §4.2:
profile metrics hold a power-level estimate; AGENT_RULES.md §5: numbers and
labels are deterministic code).

Wraps the existing BR-3/BR-4 bracket pipeline (analytics.bracket_check +
analytics.bracket_eval) with the checked-in data snapshots
(data/game_changers.json, data/bracket_cards.json) — no network, no LLM, no
Redis. Commander Spellbook combo data is not available in this context, so
the two-card-combo rule is reported as "not checked" and the estimate says so
(AGENT_RULES.md §4: partial data is labelled, never hidden).

Without Commander Salt or an engine play-feel estimate, the power level is
the midpoint of the estimated bracket's community power band
(``power_source == "bracket_band"``) — a coarse, documented placeholder, not
a pretend-precise number.
"""
from __future__ import annotations

from src.server.domain.analytics import bracket_eval


def estimate_power(deck_cards: list) -> dict:
    """deck_cards rows (name/board/qty, commander + main) -> power metric dict."""
    commanders: list[str] = []
    cards: list[dict] = []
    for row in deck_cards:
        if row.get("board") not in ("commander", "main"):
            continue
        name = row.get("name")
        if not name:
            continue
        if row.get("board") == "commander":
            commanders.append(name)
        for _ in range(int(row.get("qty") or 1)):
            cards.append({"name": name})
    if not cards and not commanders:
        return {
            "available": False,
            "reason": "no deck cards to evaluate",
            "estimated_bracket": None,
            "bracket_name": None,
            "power_level": None,
            "power_source": None,
            "game_changers": 0,
        }
    ev = bracket_eval.evaluate_bracket(
        {"commanders": commanders, "cards": cards}, None, {}
    )
    findings = ev.findings or {}
    gc = findings.get("game_changers") or {}
    combos = findings.get("two_card_combos") or {}
    notes = []
    if not combos.get("checked", False):
        notes.append("two-card combos not checked (no Spellbook data); "
                     "bracket may be higher")
    if ev.power_source == "bracket_band":
        notes.append("power level is the bracket band midpoint "
                     "(no Commander Salt or play-feel estimate)")
    return {
        "available": True,
        "reason": None,
        "estimated_bracket": ev.estimated_bracket,
        "bracket_name": ev.estimated_bracket_name,
        "power_level": ev.estimated_power_level,
        "power_source": ev.power_source,
        "game_changers": int(gc.get("count") or 0),
        "notes": notes,
    }
