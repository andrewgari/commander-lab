"""Moxfield provider: fetch + normalize a single public deck.

Moxfield has no official public API and is behind Cloudflare for browser
traffic, but the unauthenticated JSON endpoint below works for PUBLIC decks
as of this writing (see mtg-apis skill). This is unofficial/undocumented
and may break without notice — if it starts 403ing, the fallback is the
Playwright + authenticated-session scrape path described in that skill.
"""
import re
import requests

import deck_slots
from deck_slots import DeckSlot

API_BASE = "https://api2.moxfield.com/v3/decks/all"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

_URL_RE = re.compile(r"moxfield\.com/decks/([A-Za-z0-9_-]+)")
_PUBLIC_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Boards folded into the mainboard cardlist for the physical instance registry.
# Sideboard/maybeboard-equivalents are excluded, matching the Archidekt convention.
MAINBOARD_BOARDS = {"mainboard", "commanders", "companions", "signatureSpells"}

# Non-main boards -> canonical deck slots (deck_slots.DeckSlot). The removed
# "sideboard" board remaps to maybeboard and is flagged on parse.
BOARD_TO_SLOT = {
    "maybeboard": DeckSlot.MAYBEBOARD,
    "sideboard": DeckSlot.MAYBEBOARD,  # remapped; flagged
    "attractions": DeckSlot.ATTRACTION_DECK,
    "planes": DeckSlot.PLANAR_DECK,
    "schemes": DeckSlot.SCHEME_DECK,
}


def parse_identifier(identifier: str) -> str:
    """Accept a bare publicId or a full moxfield.com/decks/{publicId} URL.
    Raises ValueError for anything that isn't a plausible publicId (no
    slashes, whitespace, query strings, etc.), so malformed input fails
    fast with a clear error instead of flowing into the request path.
    """
    identifier = identifier.strip()
    match = _URL_RE.search(identifier)
    candidate = match.group(1) if match else identifier
    if not _PUBLIC_ID_RE.match(candidate):
        raise ValueError(f"could not parse a Moxfield deck id from: {identifier}")
    return candidate


class MoxfieldUserNotFound(ValueError):
    """Raised when a Moxfield username has no matching decks / doesn't exist."""


def list_decks(username: str) -> list:
    """Return every public Moxfield deck publicId owned by `username`.

    Pages through api2.moxfield.com/v2/decks/search with authorUserNames
    (plural — the only param name that actually filters; see
    docs/LINKED_ACCOUNTS.md / mtg-apis skill references/moxfield_api.md).
    A bad/nonexistent username does NOT error at the HTTP layer — Moxfield
    silently returns the global top-N feed instead. Guard against that by
    verifying every returned deck's authors actually include the requested
    username (case-insensitive); if none do, raise MoxfieldUserNotFound
    rather than returning that unrelated feed as if it were the user's decks.
    """
    username = username.strip()
    if not username:
        raise MoxfieldUserNotFound("empty Moxfield username")

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    public_ids = []
    page = 1
    total_pages = 1
    username_lower = username.lower()
    saw_match = False

    while page <= total_pages:
        res = requests.get(
            "https://api2.moxfield.com/v2/decks/search",
            params={"pageSize": 100, "pageNumber": page, "authorUserNames": username},
            headers=headers,
            timeout=20,
        )
        res.raise_for_status()
        data = res.json()
        total_pages = data.get("totalPages", 1) or 1

        for deck in data.get("data", []):
            authors = deck.get("authors") or []
            author_names = {a.get("userName", "").lower() for a in authors}
            if username_lower not in author_names:
                # Wrong/nonexistent username can make the search silently
                # fall back to the unfiltered global feed, which mixes in
                # unrelated decks. Skip those entries rather than aborting
                # the whole call -- only bail out below if NO deck across
                # any page actually lists this user as an author.
                continue
            saw_match = True
            public_id = deck.get("publicId")
            if public_id:
                public_ids.append(public_id)

        page += 1

    if not saw_match:
        raise MoxfieldUserNotFound(
            f"no decks found for Moxfield user {username} (API returned "
            "no results for this username -- check the username)"
        )

    return public_ids


def fetch_deck(identifier: str) -> dict:
    public_id = parse_identifier(identifier)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    res = requests.get(f"{API_BASE}/{public_id}", headers=headers, timeout=20)
    res.raise_for_status()
    data = res.json()

    color_identity = data.get("colorIdentity", []) or []
    color_code = "".join(color_identity) or "C"

    commanders, commander_uids, cards = [], [], []
    slots = deck_slots.empty_slots()
    parse_flags: list = []
    boards = data.get("boards", {}) or {}

    commander_board = boards.get("commanders", {}) or {}
    for entry in (commander_board.get("cards") or {}).values():
        card = entry.get("card", {})
        name = card.get("name")
        if name:
            commanders.append(name)
        scryfall_id = card.get("scryfall_id")
        if scryfall_id:
            commander_uids.append(scryfall_id)

    for board_name, board in boards.items():
        entries = (board.get("cards") or {}).values()
        if board_name in MAINBOARD_BOARDS:
            for entry in entries:
                card = entry.get("card", {})
                name = card.get("name")
                if not name:
                    continue
                cards.append({"name": name, "quantity": entry.get("quantity", 1)})
        elif board_name in BOARD_TO_SLOT:
            slot = BOARD_TO_SLOT[board_name]
            for entry in entries:
                card = entry.get("card", {})
                name = card.get("name")
                if not name:
                    continue
                row = {"name": name, "quantity": entry.get("quantity", 1)}
                if deck_slots.is_sideboard_spelling(board_name):
                    row["remapped_from"] = "sideboard"
                    deck_slots.flag_sideboard_remap(
                        parse_flags, context=f"moxfield deck {public_id}", count=1
                    )
                slots[slot].append(row)

    return {
        "source": "moxfield",
        "source_id": data.get("publicId", public_id),
        "name": data.get("name", f"Moxfield Deck {public_id}"),
        "color": color_code,
        "commanders": commanders,
        "commander_uids": commander_uids,
        "folder": "Imported",
        "description": data.get("description", ""),
        "cards": cards,
        "slots": slots,
        "parse_flags": parse_flags,
        "url": data.get("publicUrl", f"https://moxfield.com/decks/{public_id}"),
    }


# ---------------------------------------------------------------------------
# Push (commander.lab -> Moxfield). Explicit user action only; see
# deck_sync.push_deck and docs/SYNC_UPDATE_PUSH.md.
#
# Moxfield has no official write API. This uses the web app's bulk text
# import (POST /v3/decks/{id}/import, replaceAll) documented in
# docs/moxfield-api.md section 4.1, authenticated with a bearer token a human
# captures from a logged-in browser (sign-in is Turnstile-gated, so it can't
# be automated and the token expires). Not yet exercised against a live
# account -- validate with a throwaway deck.
# ---------------------------------------------------------------------------
import os

from .errors import PushError, PushNotConfigured

PUSH_TOKEN_ENV = "MOXFIELD_TOKEN"
# Boards that aren't part of the local cardlist; re-sent as-is so a
# replaceAll import doesn't wipe them.
_PRESERVED_BOARDS = (("sideboard", "Sideboard"), ("maybeboard", "Maybeboard"))


def push_configured() -> bool:
    return bool(os.getenv(PUSH_TOKEN_ENV, "").strip())


def build_import_text(cards: list, commanders=None, remote_boards=None) -> str:
    commanders = set(commanders or [])
    lines = []
    for c in cards:
        name, qty = c.get("name"), int(c.get("quantity") or 1)
        if name and qty > 0:
            lines.append(f"{qty} {name}" + (" *CMDR*" if name in commanders else ""))
    for key, header in _PRESERVED_BOARDS:
        entries = ((remote_boards or {}).get(key) or {}).get("cards") or {}
        kept = [f"{e.get('quantity', 1)} {(e.get('card') or {}).get('name')}"
                for e in entries.values() if (e.get("card") or {}).get("name")]
        if kept:
            lines += ["", header] + kept
    return "\n".join(lines) + "\n"


def push_deck(identifier: str, cards: list, commanders=None) -> dict:
    """Replace the linked Moxfield deck's main/commander boards with `cards`
    (sideboard/maybeboard preserved)."""
    token = os.getenv(PUSH_TOKEN_ENV, "").strip()
    if not token:
        raise PushNotConfigured(
            f"Moxfield push is not configured: set {PUSH_TOKEN_ENV} to a bearer token from a logged-in browser"
        )
    public_id = parse_identifier(identifier)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json", "Authorization": f"Bearer {token}"}
    res = requests.get(f"{API_BASE}/{public_id}", headers=headers, timeout=20)
    res.raise_for_status()
    data = res.json()
    deck_id = data.get("id")
    if not deck_id:
        raise PushError(f"Moxfield deck {public_id} returned no internal id")

    out = requests.post(
        f"https://api2.moxfield.com/v3/decks/{deck_id}/import",
        json={
            "importText": build_import_text(cards, commanders, data.get("boards")),
            "replaceAll": True,
            "usePrefPrintings": True,
        },
        headers={**headers, "Content-Type": "application/json",
                 "X-Public-Deck-ID": public_id, "X-Deck-Version": str(data.get("version", ""))},
        timeout=30,
    )
    if out.status_code == 401:
        raise PushError(f"Moxfield rejected the token (HTTP 401); refresh {PUSH_TOKEN_ENV}")
    if not out.ok:
        raise PushError(f"Moxfield import failed: HTTP {out.status_code} {out.text[:200]}")
    body = out.json() if out.content else {}
    if body.get("isSuccessful") is False or body.get("errors"):
        raise PushError(f"Moxfield import reported errors: {body.get('errors')}")
    return {"url": data.get("publicUrl", f"https://moxfield.com/decks/{public_id}"), "operations": 1}


def create_deck(name: str, cards: list, commanders=None, description: str = "", private: bool = True) -> dict:
    """Create a NEW Moxfield deck and fill it with `cards`.

    POST /v2/decks (create, private commander deck) then the bulk text import
    via push_deck. Unofficial and unverified against a live account (see
    docs/DECK_EXPORT.md). If the fill step fails the empty deck already exists
    remotely, so the PushError message carries its URL for manual cleanup.
    """
    token = os.getenv(PUSH_TOKEN_ENV, "").strip()
    if not token:
        raise PushNotConfigured(
            f"Moxfield push is not configured: set {PUSH_TOKEN_ENV} to a bearer token from a logged-in browser"
        )
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json",
               "Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    res = requests.post(
        "https://api2.moxfield.com/v2/decks",
        json={"name": name, "format": "commander",
              "visibility": "private" if private else "public",
              "description": description or ""},
        headers=headers, timeout=30,
    )
    if res.status_code == 401:
        raise PushError(f"Moxfield rejected the token (HTTP 401); refresh {PUSH_TOKEN_ENV}")
    if not res.ok:
        raise PushError(f"Moxfield deck creation failed: HTTP {res.status_code} {res.text[:200]}")
    try:
        public_id = res.json()["publicId"]
    except (ValueError, KeyError, TypeError) as exc:
        raise PushError("Moxfield deck creation returned no publicId") from exc
    url = f"https://moxfield.com/decks/{public_id}"
    try:
        push_deck(public_id, cards, commanders)
    except PushError as exc:
        raise PushError(f"{exc} (empty deck was created at {url})") from exc
    except requests.RequestException as exc:
        raise PushError(f"Moxfield fill failed: {exc} (empty deck was created at {url})") from exc
    return {"url": url, "source_id": public_id}
