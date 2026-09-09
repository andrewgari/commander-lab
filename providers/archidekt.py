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


class ArchidektUserNotFound(ValueError):
    """Raised when an Archidekt username has no matching account."""


def list_decks(username: str) -> list:
    """Return every Archidekt deck id owned by `username`.

    Mirrors the account-wide pull previously implemented ad hoc in
    ../sync.py (see docs/LINKED_ACCOUNTS.md): resolve username -> user id,
    then page through /api/decks/v3/?ownerId=...&pageSize=100.

    Raises ArchidektUserNotFound for a username with no matching account
    (never returns an empty list silently for a bad username).
    """
    username = username.strip()
    if not username:
        raise ArchidektUserNotFound("empty Archidekt username")

    user_res = requests.get(
        "https://archidekt.com/api/users/", params={"username": username}, timeout=20
    )
    user_res.raise_for_status()
    user_data = user_res.json()
    results = user_data.get("results") or []
    if not results:
        raise ArchidektUserNotFound(f"no Archidekt user found for username: {username}")
    user_id = results[0]["id"]

    deck_ids = []
    url = "https://archidekt.com/api/decks/v3/"
    params = {"ownerId": user_id, "pageSize": 100}
    while url:
        res = requests.get(url, params=params, timeout=20)
        res.raise_for_status()
        data = res.json()
        deck_ids.extend(str(d["id"]) for d in data.get("results", []))
        url = data.get("next")
        params = None  # `next` is already a fully-qualified URL with querystring

    return deck_ids


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
