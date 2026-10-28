"""Normalize a raw Archidekt deck payload (GET /api/decks/{id}/) into the
Slice 1 Postgres shapes (ARCHITECTURE.md §4.2).

Pure logic: no FastAPI, no SQLAlchemy, no infra imports — the HTTP fetch
lives in infra/providers/archidekt.py, persistence in infra/db/repositories.py.

Decisions encoded here:
- ``board`` uses the §4.2 enum: commander | main | sideboard | maybe |
  considering. Archidekt's "Sideboard" category follows the app's existing
  convention (deck_slots): remapped to ``maybe`` and flagged. Variant-deck
  slots (planar/scheme/attraction) also land in ``maybe`` (zero card loss)
  with a ``variant_slot:<slot>`` parse flag.
- A commander is NOT duplicated into the main board (the old fetch_deck did
  that to keep a 100-card list; deck_cards PKs per board instead).
- ``category`` is the primary thematic category: the first non-structural
  Archidekt category, else None. Structural categories are excluded from
  thematic learning (AGENT_RULES.md §9).
- Duplicate (oracle_id, board) rows are merged with qty summed.
- Deck status from the physical-folder rule: parentFolder ==
  ARCHIDEKT_PHYSICAL_FOLDER_ID (default 588380) -> "have", else "virtual".
  "pending"/"possible" are user lifecycle states, never set by import.
- ``fingerprint`` is a sha256 over the canonical normalized content; re-import
  of an unchanged deck yields the same fingerprint (idempotency signal).
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Optional

from src.server.domain.decks import deck_slots
from src.server.domain.decks.deck_slots import DeckSlot

STRUCTURAL_CATEGORIES = {
    "Commander", "Sideboard", "Maybeboard", "Considering",
    "Planar Deck", "Scheme Deck", "Attraction Deck",
}

PHYSICAL_FOLDER_ENV = "ARCHIDEKT_PHYSICAL_FOLDER_ID"
DEFAULT_PHYSICAL_FOLDER_ID = 588380

# deck_slots slot -> §4.2 board enum
_SLOT_TO_BOARD = {
    DeckSlot.COMMANDER: "commander",
    DeckSlot.MAINBOARD: "main",
    DeckSlot.MAYBEBOARD: "maybe",
    DeckSlot.CONSIDERING: "considering",
    DeckSlot.PLANAR_DECK: "maybe",
    DeckSlot.SCHEME_DECK: "maybe",
    DeckSlot.ATTRACTION_DECK: "maybe",
}

_COLOR_NAME_TO_LETTER = {
    "White": "W", "Blue": "U", "Black": "B", "Red": "R", "Green": "G",
}
_WUBRG_ORDER = "WUBRG"


class DeckImportError(ValueError):
    """The payload cannot be normalized into a deck (missing id/name/cards)."""


def physical_folder_id(env: Optional[dict] = None) -> int:
    raw = ((env or os.environ).get(PHYSICAL_FOLDER_ENV) or "").strip()
    try:
        return int(raw) if raw else DEFAULT_PHYSICAL_FOLDER_ID
    except ValueError:
        return DEFAULT_PHYSICAL_FOLDER_ID


def primary_category(categories: list) -> Optional[str]:
    """First non-structural category (the deck builder's thematic grouping)."""
    for cat in categories or []:
        if cat not in STRUCTURAL_CATEGORIES:
            return str(cat)
    return None


def _color_letters(names: list) -> list:
    letters = {_COLOR_NAME_TO_LETTER.get(n) for n in names or []}
    return [c for c in _WUBRG_ORDER if c in letters]


def _type_line(oracle: dict) -> str:
    parts = list(oracle.get("superTypes") or []) + list(oracle.get("types") or [])
    line = " ".join(str(p) for p in parts)
    subs = list(oracle.get("subTypes") or [])
    return f"{line} — {' '.join(str(s) for s in subs)}" if subs else line


def _normalize_card(entry: dict, flags: list) -> Optional[dict]:
    card = entry.get("card") or {}
    oracle = card.get("oracleCard") or {}
    name = oracle.get("name")
    oracle_id = oracle.get("uid")
    if not name or not oracle_id:
        flags.append(f"skipped_card_without_oracle:{name or card.get('id') or 'unknown'}")
        return None
    categories = [str(c) for c in (entry.get("categories") or [])]
    is_commander = "Commander" in categories
    slot, remapped = deck_slots.classify_card_slot(categories, oracle.get("types"))
    if is_commander:
        slot = DeckSlot.COMMANDER
    if remapped:
        deck_slots.flag_sideboard_remap(flags, context="archidekt import", count=1)
    if slot in deck_slots.VARIANT_SLOTS:
        flag = f"variant_slot:{slot}"
        if flag not in flags:
            flags.append(flag)
    edition = card.get("edition") or {}
    return {
        "oracle_id": str(oracle_id),
        "name": str(name),
        "qty": int(entry.get("quantity") or 1),
        "board": _SLOT_TO_BOARD[slot],
        "category": None if is_commander else primary_category(categories),
        "card": {
            "oracle_id": str(oracle_id),
            "name": str(name),
            "type_line": _type_line(oracle),
            "oracle_text": oracle.get("text") or "",
            "mana_cost": oracle.get("manaCost") or "",
            "colors": _color_letters(oracle.get("colors") or []),
            "color_identity": _color_letters(oracle.get("colorIdentity") or []),
            "cmc": float(oracle.get("cmc") or 0),
            "keywords": [str(k) for k in (oracle.get("keywords") or [])],
            "image_uri": "",
        },
        "printing": {
            "scryfall_id": str(card.get("uid") or ""),
            "oracle_id": str(oracle_id),
            "set_code": str(edition.get("editioncode") or ""),
            "collector_number": str(card.get("collectorNumber") or ""),
            "rarity": str(card.get("rarity") or ""),
            "prices": card.get("prices") or {},
        },
    }


def fingerprint(normalized_cards: list, commanders: list, color_identity: list) -> str:
    """Stable content hash of the decklist (name/folder/status excluded)."""
    canonical = {
        "commanders": sorted(commanders),
        "color_identity": sorted(color_identity),
        "cards": sorted(
            (
                {
                    "o": c["oracle_id"],
                    "q": c["qty"],
                    "b": c["board"],
                    "c": c.get("category"),
                }
                for c in normalized_cards
            ),
            key=lambda c: (c["o"], c["b"]),
        ),
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def normalize_archidekt_deck(payload: dict) -> dict:
    """Raw GET /api/decks/{id}/ payload -> normalized deck dict for the repos."""
    if not isinstance(payload, dict):
        raise DeckImportError("archidekt payload is not an object")
    archidekt_id = payload.get("id")
    if archidekt_id is None:
        raise DeckImportError("archidekt payload has no deck id")
    archidekt_id = str(archidekt_id)
    name = (payload.get("name") or "").strip() or f"Archidekt Deck {archidekt_id}"

    parent = payload.get("parentFolderId", payload.get("parentFolder"))
    if isinstance(parent, dict):
        parent = parent.get("id")
    try:
        parent_folder_id = int(parent) if parent is not None else None
    except (TypeError, ValueError):
        parent_folder_id = None

    flags: list = []
    merged: dict = {}
    commanders: list = []
    color_letters: set = set()
    for entry in payload.get("cards") or []:
        norm = _normalize_card(entry, flags)
        if norm is None:
            continue
        for letter in norm["card"]["color_identity"]:
            color_letters.add(letter)
        if norm["board"] == "commander" and norm["name"] not in commanders:
            commanders.append(norm["name"])
        key = (norm["oracle_id"], norm["board"])
        if key in merged:
            merged[key]["qty"] += norm["qty"]
            if merged[key].get("category") is None:
                merged[key]["category"] = norm["category"]
        else:
            merged[key] = norm
    cards = sorted(merged.values(), key=lambda c: (c["board"], c["name"].lower()))
    if not cards:
        raise DeckImportError(f"archidekt deck {archidekt_id} has no usable cards")

    color_identity = [c for c in _WUBRG_ORDER if c in color_letters]
    status = "have" if parent_folder_id == physical_folder_id() else "virtual"
    return {
        "id": f"archidekt:{archidekt_id}",
        "archidekt_id": archidekt_id,
        "name": name,
        "status": status,
        "color_identity": color_identity,
        "commanders": commanders,
        "source": "archidekt",
        "parent_folder_id": parent_folder_id,
        "fingerprint": fingerprint(cards, commanders, color_identity),
        "cards": cards,
        "parse_flags": flags,
    }
