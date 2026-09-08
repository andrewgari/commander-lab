"""Archidekt provider: fetch + normalize a single deck by id or URL.

See docs in mtg-apis skill / ../sync.py for the account-wide sync path.
This module is used by the on-demand /api/import endpoint to pull a single
deck (yours or someone else's public deck) without a full account resync.
"""
import re
import requests

STRUCTURAL_CATEGORIES = {"Commander", "Sideboard", "Maybeboard", "Considering"}

_ID_RE = re.compile(r"archidekt\.com/decks/(\d+)")


def parse_identifier(identifier: str) -> str:
    """Accept a bare numeric id or a full archidekt.com/decks/{id}/... URL."""
    identifier = identifier.strip()
    match = _ID_RE.search(identifier)
    if match:
        return match.group(1)
    if identifier.isdigit():
        return identifier
    raise ValueError(f"could not parse an Archidekt deck id from: {identifier}")


def fetch_deck(identifier: str) -> dict:
    deck_id = parse_identifier(identifier)
    res = requests.get(f"https://archidekt.com/api/decks/{deck_id}/", timeout=20)
    res.raise_for_status()
    data = res.json()

    colors_map = data.get("colors", {}) or {}
    color_code = "".join(c for c in ["W", "U", "B", "R", "G"] if colors_map.get(c, 0) > 0) or "C"

    commanders, commander_uids, cards = [], [], []
    for card in data.get("cards", []):
        categories = card.get("categories") or []
        oracle = card.get("card", {}).get("oracleCard", {})
        name = oracle.get("name")
        if not name:
            continue

        if "Commander" in categories:
            commanders.append(name)
            uid = card.get("card", {}).get("uid")
            if uid:
                commander_uids.append(uid)

        if "Maybeboard" in categories or "Sideboard" in categories:
            continue

        cards.append({"name": name, "quantity": card.get("quantity", 1)})

    return {
        "source": "archidekt",
        "source_id": deck_id,
        "name": data.get("name", f"Archidekt Deck {deck_id}"),
        "color": color_code,
        "commanders": commanders,
        "commander_uids": commander_uids,
        "folder": "Imported",
        "description": data.get("description", ""),
        "cards": cards,
        "url": f"https://archidekt.com/decks/{deck_id}",
    }
