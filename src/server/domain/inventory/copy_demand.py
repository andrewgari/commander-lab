"""Proposed copy demand vs known inventory (STA-11 B; discovery SRC-06).

A thin, explicit wrapper over `have_buy.compute_have_buy` (the Slice-1
owned/missing math) that makes unknown copy/printing detail *visible* instead
of implied:

- `deck_cards` rows carry no printing columns, so the exact printing and
  finish of every demanded copy is `unknown` — never guessed, never shown as
  a concrete printing (AGENT_RULES rule 4; STA-9 SRC-06).
- Copies with no instance records are `buy` (derived by have_buy), not
  invented inventory.

Pure domain: no FastAPI, no SQLAlchemy, no infra imports (rule 2).
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.server.domain.inventory import have_buy

PRINTING_UNKNOWN_REASON = (
    "deck list rows carry no printing columns; the exact printing/finish of "
    "demanded copies is unverified"
)


def copy_demand(
    deck_cards: List[dict], instances: List[dict], deck_id: str
) -> Dict[str, Any]:
    """Copy-demand report for one deck.

    Returns the have_buy report (need/have/pending/buy per line and totals)
    with two additions:
      - every line gets `printing` (always None) and `printing_status`
        ("unknown") plus the reason, so consumers render "unknown" instead of
        assuming a printing;
      - a top-level `unknowns` block names every dimension this report
        cannot speak to.
    """
    report = have_buy.compute_have_buy(deck_cards, instances, deck_id)
    lines: List[Dict[str, Any]] = []
    for line in report["lines"]:
        lines.append({
            **line,
            "printing": None,
            "printing_status": "unknown",
            "printing_reason": PRINTING_UNKNOWN_REASON,
        })
    return {
        "deck_id": report["deck_id"],
        "totals": report["totals"],
        "lines": lines,
        "unknowns": {
            "printing": {"status": "unavailable", "reason": PRINTING_UNKNOWN_REASON},
        },
    }
