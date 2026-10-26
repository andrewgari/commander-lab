"""Archidekt provider: fetch + normalize a single deck by id or URL.

IMPORT PATH ONLY (docs/ARCHITECTURE.md §0/§5): Archidekt is a one-way import
source. The push/create code that used to live here was deleted in Slice 1 —
providers/__init__.py degrades push calls for "archidekt" to
PushNotConfigured via its hasattr guards.

Cards are normalized into named deck slots (deck_slots.DeckSlot): commanders,
mainboard ("cards"), and the slots mapping (maybeboard / considering /
planar_deck / scheme_deck / attraction_deck). The removed "Sideboard"
category remaps to maybeboard and is surfaced via parse_flags.

``fetch_deck_raw`` returns the unmodified API payload for the Slice-1
importer (workers/importer.py), which needs per-card categories and oracle
ids that the normalized shape intentionally drops.
"""
import re
import requests

import src.server.domain.decks.deck_slots as deck_slots
from src.server.domain.decks.deck_slots import DeckSlot

STRUCTURAL_CATEGORIES = {"Commander", "Sideboard", "Maybeboard", "Considering",
                         "Planar Deck", "Scheme Deck", "Attraction Deck"}

_ID_RE = re.compile(r"archidekt\.com/decks/(\d+)")

_COLOR_NAME_TO_LETTER = {
    "White": "W",
    "Blue": "U",
    "Black": "B",
    "Red": "R",
    "Green": "G",
}
_WUBRG_ORDER = "WUBRG"


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


# folder id -> display name, learned from list rows (the detail endpoint only
# returns the folder id). Display only: the physical rule uses the id.
_FOLDER_NAMES: dict = {}


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
        for d in data.get("results", []):
            deck_ids.append(str(d["id"]))
            if d.get("parentFolderId") is not None and d.get("parentFolderName"):
                _FOLDER_NAMES[d["parentFolderId"]] = d["parentFolderName"]
        url = data.get("next")
        params = None  # `next` is already a fully-qualified URL with querystring

    return deck_ids


def fetch_deck_raw(identifier: str) -> dict:
    """Return the unmodified GET /api/decks/{id}/ payload.

    Used by the Slice-1 importer, which maps cards/categories/oracle ids into
    Postgres itself. Not a substitute for fetch_deck (the normalized shape
    every pre-Postgres caller consumes).
    """
    deck_id = parse_identifier(identifier)
    res = requests.get(f"https://archidekt.com/api/decks/{deck_id}/", timeout=20)
    res.raise_for_status()
    return res.json()


def fetch_deck(identifier: str) -> dict:
    data = fetch_deck_raw(identifier)
    deck_id = parse_identifier(identifier)

    commanders, commander_uids, cards = [], [], []
    slots = deck_slots.empty_slots()
    parse_flags: list = []
    color_letters = set()
    for card in data.get("cards", []):
        categories = card.get("categories") or []
        oracle = card.get("card", {}).get("oracleCard", {})
        name = oracle.get("name")
        if not name:
            continue

        for color_name in oracle.get("colorIdentity") or []:
            letter = _COLOR_NAME_TO_LETTER.get(color_name)
            if letter:
                color_letters.add(letter)

        if "Commander" in categories:
            commanders.append(name)
            uid = card.get("card", {}).get("uid")
            if uid:
                commander_uids.append(uid)
            # A commander is also part of the 100-card list; fall through so
            # it lands in the mainboard cardlist as before.

        slot, remapped = deck_slots.classify_card_slot(categories, oracle.get("types"))
        entry = {"name": name, "quantity": card.get("quantity", 1)}
        if slot == DeckSlot.MAINBOARD or "Commander" in categories:
            cards.append(entry)
        else:
            if remapped:
                entry["remapped_from"] = "sideboard"
                deck_slots.flag_sideboard_remap(
                    parse_flags, context=f"archidekt deck {deck_id}", count=1
                )
            slots[slot].append(entry)

    color_code = "".join(c for c in _WUBRG_ORDER if c in color_letters) or "C"

    # Folder data for the physical-folder rule (registry.apply_folder_rule).
    # The detail endpoint calls it parentFolder; list rows call it parentFolderId.
    parent = data.get("parentFolderId", data.get("parentFolder"))
    folder_name = data.get("parentFolderName")
    if isinstance(parent, dict):
        folder_name = folder_name or parent.get("name")
        parent = parent.get("id")
    folder_name = folder_name or _FOLDER_NAMES.get(parent)

    return {
        "source": "archidekt",
        "source_id": deck_id,
        "name": data.get("name", f"Archidekt Deck {deck_id}"),
        "color": color_code,
        "commanders": commanders,
        "commander_uids": commander_uids,
        "folder": folder_name or "Imported",
        "description": data.get("description", ""),
        "cards": cards,
        "slots": slots,
        "parse_flags": parse_flags,
        "url": f"https://archidekt.com/decks/{deck_id}",
        "parent_folder_id": parent,
        "theorycrafted": bool(data.get("theorycrafted")),
    }


