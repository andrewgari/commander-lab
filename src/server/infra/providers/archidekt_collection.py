"""Archidekt collection fetcher: pull the authenticated user's card collection.

Unlike decks, a collection is NOT publicly readable. Every public-looking
route is auth-gated (verified live, 2026-09):

    GET  /api/collection/{userId}/        -> 400 ["No public collection found."]
    GET  /api/collection/v2/{userId}/     -> 401 {"detail": "Authentication ..."}
    POST /api/collection/export/v2/{id}/  -> 400 {"error": "No collection found."}
                                             when unauthenticated

The only usable read path is the one the site's own "Collection data viewer"
uses: POST /api/collection/export/v2/{userId}/ with a field list, which
returns a CSV page plus pagination flags:

    request:  {"fields": [...], "page": 1, "game": 1, "pageSize": 2500}
    response: {"content": "<csv>", "totalRows": int, "moreContent": bool}

Auth is the same browser-session cookie pair the tag push in app.py already
uses: ARCHIDEKT_SESSION (sessionid) + ARCHIDEKT_CSRF (csrftoken). Grab them
from a logged-in browser's cookies for archidekt.com; see
docs/COLLECTION_IMPORT.md.

This module is read-only and does no Redis work — turning rows into owned
card instances is collection.py's job.
"""
import csv
import io
import os
from typing import Optional

import requests

COLLECTION_EXPORT_URL = "https://archidekt.com/api/collection/export/v2/{user_id}/"
USERS_URL = "https://archidekt.com/api/users/"

# Archidekt's own export field names, in the order its collection data viewer
# requests them. Keep `quantity` and `card__oracleCard__name` — everything
# downstream depends on them.
EXPORT_FIELDS = [
    "quantity",
    "card__oracleCard__name",
    "modifier",
    "condition",
    "createdAt",
    "language",
    "purchasePrice",
    "tags",
    "card__edition__editionname",
    "card__edition__editioncode",
    "card__collectorNumber",
    "card__uid",
    "card__oracle__uid",
]

# game=1 is paper Magic (2 is MTGO/Arena in Archidekt's collection model).
PAPER_GAME = 1
PAGE_SIZE = 2500
MAX_PAGES = 200  # hard stop: ~500k rows, guards against a moreContent loop

# Archidekt sends finishes as its UI labels; instances.py uses Scryfall's.
FINISH_MAP = {
    "": "nonfoil",
    "normal": "nonfoil",
    "nonfoil": "nonfoil",
    "non-foil": "nonfoil",
    "foil": "foil",
    "etched": "etched",
}

# Conditions arrive either as codes or full names depending on export version;
# accept both and fall back to NM rather than failing an entire import row.
CONDITION_MAP = {
    "nm": "NM", "near mint": "NM", "mint": "NM",
    "lp": "LP", "lightly played": "LP", "slightly played": "LP",
    "mp": "MP", "moderately played": "MP", "played": "MP",
    "hp": "HP", "heavily played": "HP", "poor": "HP",
    "dmg": "DMG", "damaged": "DMG",
}

LANGUAGE_MAP = {
    "english": "EN", "en": "EN",
    "german": "DE", "de": "DE",
    "french": "FR", "fr": "FR",
    "italian": "IT", "it": "IT",
    "spanish": "ES", "es": "ES",
    "portuguese": "PT", "pt": "PT",
    "japanese": "JA", "ja": "JA", "jp": "JA",
    "korean": "KO", "ko": "KO",
    "russian": "RU", "ru": "RU",
    "simplified chinese": "ZHS", "zhs": "ZHS",
    "traditional chinese": "ZHT", "zht": "ZHT",
}


class CollectionAuthError(ValueError):
    """Raised when session cookies are missing, expired, or rejected."""


class CollectionFetchError(ValueError):
    """Raised when Archidekt returns an unusable collection response."""


def credentials() -> tuple:
    """(sessionid, csrftoken) from the environment. Raises CollectionAuthError
    when either is unset, so the caller can surface a 400 with a fix-it
    message instead of a confusing upstream 400/401."""
    session_id = os.getenv("ARCHIDEKT_SESSION")
    csrf_token = os.getenv("ARCHIDEKT_CSRF")
    if not session_id or not csrf_token:
        raise CollectionAuthError(
            "ARCHIDEKT_SESSION and ARCHIDEKT_CSRF must be set to import a "
            "collection (Archidekt has no public collection API) — see "
            "docs/COLLECTION_IMPORT.md"
        )
    return session_id, csrf_token


def resolve_user_id(username: str) -> str:
    """Username -> numeric Archidekt user id, via the public users endpoint."""
    username = (username or "").strip()
    if not username:
        raise CollectionFetchError("empty Archidekt username")

    res = requests.get(USERS_URL, params={"username": username}, timeout=20)
    res.raise_for_status()
    results = res.json().get("results") or []
    if not results:
        raise CollectionFetchError(f"no Archidekt user found for username: {username}")
    return str(results[0]["id"])


def _headers(session_id: str, csrf_token: str) -> dict:
    return {
        "Cookie": f"sessionid={session_id}; csrftoken={csrf_token}",
        "X-CSRFToken": csrf_token,
        "Referer": "https://archidekt.com/collection/v2/data",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (commander-lab collection import)",
    }


def _fetch_page(user_id: str, page: int, session_id: str, csrf_token: str) -> dict:
    """One page of the collection export. `page` is 1-indexed, as Archidekt's
    own client sends it."""
    res = requests.post(
        COLLECTION_EXPORT_URL.format(user_id=user_id),
        json={
            "fields": EXPORT_FIELDS,
            "page": page,
            "game": PAPER_GAME,
            "pageSize": PAGE_SIZE,
        },
        headers=_headers(session_id, csrf_token),
        timeout=60,
    )

    if res.status_code in (401, 403):
        raise CollectionAuthError(
            "Archidekt rejected the session cookies (expired?) — refresh "
            "ARCHIDEKT_SESSION/ARCHIDEKT_CSRF from a logged-in browser"
        )
    if res.status_code == 400:
        # Archidekt returns a 400 with this body both for "not logged in" and
        # for "this account has no collection"; treat it as auth since an
        # unauthenticated call is by far the likelier cause.
        raise CollectionAuthError(
            f"Archidekt refused the collection export ({res.text.strip()[:120]}) — "
            "check that ARCHIDEKT_SESSION/ARCHIDEKT_CSRF belong to the account "
            "that owns this collection"
        )
    res.raise_for_status()

    try:
        return res.json()
    except ValueError as exc:
        raise CollectionFetchError(f"non-JSON collection export response: {exc}") from exc


def parse_csv_rows(content: str) -> list:
    """Parse one export page's CSV `content` into normalized row dicts.

    Normalization is deliberately lossy-but-safe: unknown finishes/conditions/
    languages fall back to instances.py's defaults instead of raising, so one
    odd row can't abort a 10k-card import.
    """
    rows = []
    reader = csv.DictReader(io.StringIO(content))
    for raw in reader:
        name = (raw.get("card__oracleCard__name") or "").strip()
        if not name:
            continue

        try:
            quantity = int(float(raw.get("quantity") or 0))
        except (TypeError, ValueError):
            quantity = 0
        if quantity <= 0:
            continue

        finish_raw = (raw.get("modifier") or "").strip().lower()
        condition_raw = (raw.get("condition") or "").strip().lower()
        language_raw = (raw.get("language") or "").strip().lower()

        purchase_price_raw = (raw.get("purchasePrice") or "").strip()
        try:
            purchase_price = float(purchase_price_raw)
        except ValueError:
            purchase_price = 0.0

        tags_raw = (raw.get("tags") or "").strip()
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []

        rows.append({
            "card_name": name,
            "quantity": quantity,
            "set": (raw.get("card__edition__editioncode") or "").strip().upper(),
            "set_name": (raw.get("card__edition__editionname") or "").strip(),
            "collector_number": (raw.get("card__collectorNumber") or "").strip(),
            "finish": FINISH_MAP.get(finish_raw, "nonfoil"),
            "condition": CONDITION_MAP.get(condition_raw, "NM"),
            "language": LANGUAGE_MAP.get(language_raw, (language_raw or "en").upper()[:3]),
            "archidekt_uid": (raw.get("card__uid") or "").strip(),
            "oracle_uid": (raw.get("card__oracle__uid") or "").strip(),
            "date_acquired": (raw.get("createdAt") or "").strip(),
            "price_paid": purchase_price,
            "tags": tags,
        })
    return rows


def fetch_collection(
    username: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    csrf_token: Optional[str] = None,
) -> list:
    """Every paper collection row for one Archidekt account, as normalized
    dicts (see parse_csv_rows). Pass `username` or `user_id`; credentials
    default to the ARCHIDEKT_SESSION/ARCHIDEKT_CSRF environment pair.

    Pages until `moreContent` is false, so the caller gets the whole
    collection in one list rather than having to drive pagination.
    """
    if not session_id or not csrf_token:
        session_id, csrf_token = credentials()
    assert session_id and csrf_token  # credentials() raises rather than returning empties

    if not user_id:
        user_id = resolve_user_id(username or os.getenv("ARCHIDEKT_USERNAME", ""))

    rows = []
    page = 1
    while page <= MAX_PAGES:
        payload = _fetch_page(user_id, page, session_id, csrf_token)
        content = payload.get("content") or ""
        rows.extend(parse_csv_rows(content))
        if not payload.get("moreContent"):
            break
        page += 1

    return rows
