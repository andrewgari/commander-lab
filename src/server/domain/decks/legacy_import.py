"""Normalize the legacy Redis deck registry (the ``decks`` key + ``deck_status:*``)
into the Slice 1+ Postgres shapes (ARCHITECTURE.md §4.2, §8 Slice 2).

Pure logic: no FastAPI, no SQLAlchemy, no infra imports. Card-name resolution
is injected as a callable so this stays testable and Redis-free.

Two jobs:

1. ``map_legacy_status`` — the old lifecycle vocabulary
   (physical | digital | testing | retired) onto the §4.2 deck status enum
   (have | virtual | pending | possible). ``retired`` has no enum value: the
   deck is archived (``archived_at`` set) and its status becomes ``virtual``.
   Unknown values fall back to ``virtual`` with a parse flag — never guessed
   into ``have`` (AGENT_RULES.md §4).

2. ``normalize_redis_deck`` — fallback deck normalizer used when the live
   Archidekt fetch is unavailable (deck deleted/private upstream, non-Archidekt
   source, offline rehearsal). The Redis decklist carries only {name,
   quantity}; boards are inferred (commander names -> ``commander`` board,
   everything else ``main``) and thematic categories are absent (NULL —
   category data was never in the Redis cache). Names that don't resolve to an
   oracle id are skipped and counted, never silently dropped.
"""
from __future__ import annotations

import hashlib
import json
from typing import Callable, Optional

# Old lifecycle vocabulary -> §4.2 enum. "possible" has no old equivalent
# (it is a new user state); "retired" maps to archived, not to an enum value.
LEGACY_STATUS_MAP = {
    "physical": "have",
    "digital": "virtual",
    "testing": "pending",
}


def map_legacy_status(old: Optional[str]) -> tuple[str, bool, Optional[str]]:
    """Map a legacy status string to (new_status, archived, parse_flag).

    The flag is None on a clean mapping and otherwise describes the fallback
    taken (recorded in the migration report).
    """
    value = (old or "").strip().lower()
    if value in LEGACY_STATUS_MAP:
        return LEGACY_STATUS_MAP[value], False, None
    if value == "retired":
        return "virtual", True, None
    if not value:
        return "virtual", False, "missing_status:defaulted_virtual"
    return "virtual", False, f"unknown_status:{value}:defaulted_virtual"


def fingerprint(cards: list, commanders: list) -> str:
    """Content hash over the normalized card list (same role as
    deck_import.fingerprint for live imports; names stand in for oracle ids
    here so the two fingerprints are NOT comparable — sources differ)."""
    canonical = {
        "commanders": sorted(commanders),
        "cards": sorted(
            ({"o": c["oracle_id"], "q": c["qty"], "b": c["board"]} for c in cards),
            key=lambda c: (c["o"], c["b"]),
        ),
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def normalize_redis_deck(
    entry: dict,
    oracle_for: Callable[[str], Optional[str]],
) -> dict:
    """One ``decks``-array entry -> normalized deck dict for the repositories.

    ``oracle_for(name)`` resolves a card name to an oracle_id (or None).
    Returns a dict with the deck fields plus ``cards`` (deck_cards rows),
    ``unresolved`` (names that did not resolve) and ``parse_flags``.
    """
    registry_id = entry.get("registry_id") or (
        f"archidekt:{entry['source_id']}"
        if entry.get("source") == "archidekt" and entry.get("source_id") is not None
        else f"manual:{entry.get('id')}"
    )
    commanders = [str(c) for c in (entry.get("commanders") or [])]
    commander_keys = {c.strip().lower() for c in commanders}

    cards: dict[tuple[str, str], dict] = {}
    unresolved: list[str] = []
    for line in entry.get("cards") or []:
        name = (line.get("name") or "").strip()
        if not name:
            continue
        qty = int(line.get("quantity") or 1)
        board = "commander" if name.strip().lower() in commander_keys else "main"
        oid = oracle_for(name)
        if not oid:
            unresolved.append(name)
            continue
        key = (oid, board)
        if key in cards:
            cards[key]["qty"] += qty
        else:
            cards[key] = {"oracle_id": oid, "name": name, "qty": qty,
                          "board": board, "category": None}

    color = (entry.get("color") or "").upper()
    color_identity = [c for c in "WUBRG" if c in color]
    flags: list = ["redis_fallback:boards_inferred"]
    card_list = sorted(cards.values(), key=lambda c: (c["board"], c["name"].lower()))
    return {
        "id": str(registry_id),
        "archidekt_id": str(entry["source_id"]) if entry.get("source_id") is not None else None,
        "name": entry.get("name") or registry_id,
        "color_identity": color_identity,
        "commanders": commanders,
        "source": "archidekt" if entry.get("source") == "archidekt" else "manual",
        "fingerprint": fingerprint(card_list, commanders),
        "cards": card_list,
        "unresolved": unresolved,
        "parse_flags": flags,
    }
