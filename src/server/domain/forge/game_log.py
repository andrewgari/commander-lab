"""Game-level facts derived from a verbose Forge log block (stdlib only).

`forge_game_telemetry.py` (ported verbatim) answers per-player curve/cards;
this module derives the per-GAME fields the `games` table needs (§4.2):
seat→player-name map, winner seat, turn count, match duration. Pure logic:
no FastAPI, no SQLAlchemy, no infra imports (AGENT_RULES.md §2).

Missing data is explicit, never guessed (§4): winner/turns/timestamps that
the log does not carry come back as None with a reason string, and the
parser's `drawn`/`stuck`/`mana_spent` gaps have named reasons here so every
consumer (notes, telemetry rows, report UI) states the same thing.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional

PARSER_VERSION = "forge-telemetry/1.0"

# "unavailable" is a first-class value with an explicit reason (§7.4).
REASON_DRAWS = "Forge logs do not record draws"
REASON_STUCK = "Forge logs do not record hand contents"
REASON_DRAWS_STUCK = "Forge logs record no draws or hand contents"
REASON_MANA_SPENT = "Forge mana log lines carry no player attribution"
REASON_PLAYED_AT = "Forge logs record no wall-clock timestamps"
REASON_RESULT = "log has no 'has won!' line (incomplete or drawn game)"
REASON_TURNS = "log has no 'Game Outcome: Turn N' line and no Turn markers"

RE_SEAT_NAME = re.compile(r"^Turn: Turn \d+ \(Ai\((\d)\)-(.+)\)\s*$")
RE_WINNER = re.compile(r"^Game Result: Game \d+ ended.*?Ai\((\d)\)-(.+?) has won!\s*$")
RE_OUTCOME_TURN = re.compile(r"^Game Outcome: Turn (\d+)\s*$")
RE_GLOBAL_TURN = re.compile(r"^Turn: Turn (\d+) \(")
RE_DURATION = re.compile(r"^Game Result: Game \d+ ended in (\d+) ms")


def seat_names(block: Iterable[str]) -> Dict[int, str]:
    """seat -> player/deck label, from the Turn marker lines."""
    seats: Dict[int, str] = {}
    for ln in block:
        m = RE_SEAT_NAME.match(ln)
        if m:
            seats.setdefault(int(m.group(1)), m.group(2).strip())
    return seats


def winner_seat(block: Iterable[str]) -> Optional[int]:
    for ln in block:
        m = RE_WINNER.match(ln)
        if m:
            return int(m.group(1))
    return None


def match_turns(block: Iterable[str]) -> Optional[int]:
    """Match length in turns: Forge's own 'Game Outcome: Turn N' counter,
    falling back to the highest global Turn marker seen."""
    outcome = None
    highest = 0
    for ln in block:
        m = RE_OUTCOME_TURN.match(ln)
        if m:
            outcome = int(m.group(1))
            continue
        m = RE_GLOBAL_TURN.match(ln)
        if m:
            highest = max(highest, int(m.group(1)))
    if outcome is not None:
        return outcome
    return highest or None


def duration_ms(block: Iterable[str]) -> Optional[int]:
    for ln in block:
        m = RE_DURATION.match(ln)
        if m:
            return int(m.group(1))
    return None


def derive_game(block: List[str]) -> dict:
    """All game-level fields for one completed game block. Never raises."""
    seats = seat_names(block)
    return {
        "seat_decks": seats,
        "seat_count": len(seats),
        "winner_seat": winner_seat(block),
        "turns": match_turns(block),
        "duration_ms": duration_ms(block),
    }


def game_result(derived: dict, user_seat: int) -> tuple[Optional[str], Optional[str]]:
    """(result, unavailable_reason) for the importing deck's seat."""
    winner = derived.get("winner_seat")
    if winner is None:
        return None, REASON_RESULT
    return ("win" if winner == user_seat else "loss"), None


def seat_stats(player: dict) -> dict:
    """Aggregate one parser player entry: {seat, deck, curve, cards}."""
    curve = player.get("curve") or []
    cards = player.get("cards") or {}
    lands = [r for r in curve if r.get("land_played")]
    cast_turns = [c["cast_turn"] for c in cards.values() if c.get("cast_turn") is not None]
    return {
        "seat": player.get("seat"),
        "deck": player.get("deck"),
        "own_turns": len(curve),
        "lands_played": len(lands),
        "first_land_turn": lands[0]["turn"] if lands else None,
        "first_land": lands[0]["land_played"] if lands else None,
        "spells_cast": sum(int(r.get("spells_cast") or 0) for r in curve),
        "distinct_cards_cast": len(cards),
        "earliest_cast_turn": min(cast_turns) if cast_turns else None,
        "latest_cast_turn": max(cast_turns) if cast_turns else None,
    }
