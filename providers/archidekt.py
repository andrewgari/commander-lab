"""Archidekt provider: fetch + normalize a single deck by id or URL.

See docs in mtg-apis skill / ../sync.py for the account-wide sync path.
This module is used by the on-demand /api/import endpoint to pull a single
deck (yours or someone else's public deck) without a full account resync.

Cards are normalized into named deck slots (deck_slots.DeckSlot): commanders,
mainboard ("cards"), and the slots mapping (maybeboard / considering /
planar_deck / scheme_deck / attraction_deck). The removed "Sideboard"
category remaps to maybeboard and is surfaced via parse_flags.
"""
import re
import requests

import deck_slots
from deck_slots import DeckSlot

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


def fetch_deck(identifier: str) -> dict:
    deck_id = parse_identifier(identifier)
    res = requests.get(f"https://archidekt.com/api/decks/{deck_id}/", timeout=20)
    res.raise_for_status()
    data = res.json()

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


# ---------------------------------------------------------------------------
# Push (commander.lab -> Archidekt). Explicit user action only; see
# deck_sync.push_deck and docs/SYNC_UPDATE_PUSH.md.
#
# Archidekt's write endpoint is PATCH /api/decks/{id}/modifyCards/ with a JWT
# (Authorization: JWT <token>). The endpoint and card lookup are confirmed to
# exist (modifyCards answers 401 unauthenticated; /api/cards/v2/?nameSearch=
# &exact=true returns printings), but the op payload shape has not been
# exercised against a live account yet -- validate with a throwaway deck.
# ---------------------------------------------------------------------------
import os
import uuid

from .errors import PushError, PushNotConfigured

PUSH_TOKEN_ENV = "ARCHIDEKT_TOKEN"
# Categories whose entries a mainboard push never touches. Considering and the
# variant-deck labels are here for the same reason as Maybeboard: those cards
# live in their own slots (deck_slots), outside the pushed mainboard list.
_PUSH_SKIP_CATEGORIES = {"Maybeboard", "Sideboard", "Considering",
                         "Planar Deck", "Scheme Deck", "Attraction Deck"}


def push_configured() -> bool:
    return bool(os.getenv(PUSH_TOKEN_ENV, "").strip())


def _push_headers() -> dict:
    token = os.getenv(PUSH_TOKEN_ENV, "").strip()
    if not token:
        raise PushNotConfigured(
            f"Archidekt push is not configured: set {PUSH_TOKEN_ENV} to an Archidekt JWT access token"
        )
    return {
        "Authorization": f"JWT {token}",
        "Content-Type": "application/json",
        "User-Agent": "commander-lab/1.0",
    }


def lookup_card_id(name: str):
    """Archidekt printing id for an exact card name (first printing), or None."""
    res = requests.get(
        "https://archidekt.com/api/cards/v2/",
        params={"nameSearch": name, "exact": "true", "pageSize": 5},
        headers={"User-Agent": "commander-lab/1.0"},
        timeout=20,
    )
    res.raise_for_status()
    results = res.json().get("results") or []
    for row in results:
        if (row.get("oracleCard") or {}).get("name") == name:
            return row.get("id")
    return results[0].get("id") if results else None


def build_modify_ops(remote_cards: list, desired: dict, commanders, card_id_for) -> tuple:
    """Plan modifyCards ops that make the remote mainboard match `desired`
    ({name: qty}). Maybeboard/Sideboard entries are never touched.
    Returns (ops, unresolved_names)."""
    remote = {}
    for entry in remote_cards:
        categories = entry.get("categories") or []
        if _PUSH_SKIP_CATEGORIES & set(categories):
            continue
        # Also skip cards the slot classifier places outside the mainboard
        # (e.g. a Plane living in an uncategorized row): a mainboard push
        # must never propose removing a deck's variant/maybeboard pools.
        oracle = ((entry.get("card") or {}).get("oracleCard") or {})
        slot, _remapped = deck_slots.classify_card_slot(categories, oracle.get("types"))
        if slot != DeckSlot.MAINBOARD:
            continue
        name = oracle.get("name")
        if name:
            remote.setdefault(name, []).append(entry)

    def op(action, card_id, categories, relation=None, quantity=None):
        o = {"action": action, "cardid": card_id, "categories": categories, "patchId": uuid.uuid4().hex[:10]}
        if relation is not None:
            o["deckRelationId"] = relation
        o["modifications"] = {} if quantity is None else {"quantity": quantity}
        return o

    ops, unresolved = [], []
    for name, entries in remote.items():
        want = desired.get(name, 0)
        have = sum(int(e.get("quantity") or 1) for e in entries)
        for i, e in enumerate(entries):
            rel, cid, cats = e.get("id"), (e.get("card") or {}).get("id"), e.get("categories") or []
            if want <= 0 or i > 0 and have != want:
                ops.append(op("remove", cid, cats, rel))
            elif i == 0 and have != want:
                ops.append(op("modify", cid, cats, rel, want))
    commanders = set(commanders or [])
    for name, qty in desired.items():
        if name in remote or qty <= 0:
            continue
        cid = card_id_for(name)
        if cid is None:
            unresolved.append(name)
            continue
        ops.append(op("add", cid, ["Commander"] if name in commanders else [], quantity=qty))
    return ops, unresolved


def push_deck(identifier: str, cards: list, commanders=None) -> dict:
    """Make the linked Archidekt deck's mainboard match `cards`. Card names
    that Archidekt can't resolve abort the push before anything is written."""
    headers = _push_headers()
    deck_id = parse_identifier(identifier)
    res = requests.get(f"https://archidekt.com/api/decks/{deck_id}/", headers=headers, timeout=20)
    res.raise_for_status()
    desired = {}
    for c in cards:
        if c.get("name"):
            desired[c["name"]] = desired.get(c["name"], 0) + int(c.get("quantity") or 1)

    ops, unresolved = build_modify_ops(res.json().get("cards", []), desired, commanders, lookup_card_id)
    if unresolved:
        raise PushError("Archidekt could not resolve: " + ", ".join(sorted(unresolved)))
    if ops:
        out = requests.patch(
            f"https://archidekt.com/api/decks/{deck_id}/modifyCards/",
            json={"cards": ops}, headers=headers, timeout=30,
        )
        if out.status_code in (401, 403):
            raise PushError(f"Archidekt rejected the token (HTTP {out.status_code}); refresh {PUSH_TOKEN_ENV}")
        if not out.ok:
            raise PushError(f"Archidekt modifyCards failed: HTTP {out.status_code} {out.text[:200]}")
    return {"url": f"https://archidekt.com/decks/{deck_id}", "operations": len(ops)}


COMMANDER_FORMAT_ID = 3  # Archidekt deckFormat id for Commander / EDH


def create_deck(name: str, cards: list, commanders=None, description: str = "", private: bool = True) -> dict:
    """Create a NEW Archidekt deck and fill it with `cards`.

    Two calls: POST /api/decks/v2/ (create empty deck) then modifyCards via
    push_deck. Neither is verified against a live account (see
    docs/DECK_EXPORT.md). If the fill step fails the empty deck already exists
    remotely, so the PushError message carries its URL for manual cleanup.
    """
    headers = _push_headers()
    res = requests.post(
        "https://archidekt.com/api/decks/v2/",
        json={"name": name, "deckFormat": COMMANDER_FORMAT_ID, "private": private,
              "description": description or ""},
        headers=headers, timeout=30,
    )
    if res.status_code in (401, 403):
        raise PushError(f"Archidekt rejected the token (HTTP {res.status_code}); refresh {PUSH_TOKEN_ENV}")
    if not res.ok:
        raise PushError(f"Archidekt deck creation failed: HTTP {res.status_code} {res.text[:200]}")
    try:
        deck_id = str(res.json()["id"])
    except (ValueError, KeyError, TypeError) as exc:
        raise PushError("Archidekt deck creation returned no deck id") from exc
    url = f"https://archidekt.com/decks/{deck_id}"
    try:
        push_deck(deck_id, cards, commanders)
    except PushError as exc:
        raise PushError(f"{exc} (empty deck was created at {url})") from exc
    except requests.RequestException as exc:
        raise PushError(f"Archidekt fill failed: {exc} (empty deck was created at {url})") from exc
    return {"url": url, "source_id": deck_id}
