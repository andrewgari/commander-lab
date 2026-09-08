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
