"""Moxfield provider: fetch + normalize a single public deck.

Moxfield has no official public API and is behind Cloudflare for browser
traffic, but the unauthenticated JSON endpoint below works for PUBLIC decks
as of this writing (see mtg-apis skill). This is unofficial/undocumented
and may break without notice — if it starts 403ing, the fallback is the
Playwright + authenticated-session scrape path described in that skill.
"""
import re
import requests

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
        if board_name not in MAINBOARD_BOARDS:
            continue
        for entry in (board.get("cards") or {}).values():
            card = entry.get("card", {})
            name = card.get("name")
            if not name:
                continue
            cards.append({"name": name, "quantity": entry.get("quantity", 1)})

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
        "url": data.get("publicUrl", f"https://moxfield.com/decks/{public_id}"),
    }
