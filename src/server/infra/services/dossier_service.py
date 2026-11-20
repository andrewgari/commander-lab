"""Card dossier aggregation (docs/architecture/card-dossier-spec.md, adapted
to the src/server layout).

One aggregate read model feeds every panel of ``/card/{oracle_id}``; each
panel maps 1:1 to a response key. All data is real Postgres data — a panel
with no rows renders an explicit empty state (AGENT_RULES.md §4), never a
placeholder.

Printings have no other source: they are fetched lazily from Scryfall
(``unique=prints``) on the first dossier view of a card, then cached in the
``printings`` table (spec §2). Bulk backfill lives in
scripts/backfill_card_dossier.py and is not page blocking.
"""
from __future__ import annotations

import datetime
import logging
from typing import Callable, Optional

import httpx
from sqlalchemy.orm import Session

from src.server.domain.analytics.interactions.vocab import VOCAB_VERSION
from src.server.infra.db import repositories as repo

log = logging.getLogger("commander_lab.dossier")

SCRYFALL_SEARCH_URL = "https://api.scryfall.com/cards/search"
SCRYFALL_TIMEOUT_S = 10.0

# A printing fetcher maps an oracle_id to raw printing dicts (Scryfall-shaped:
# scryfall_id, set_code, set_name, collector_number, rarity, released_at,
# image_uri_normal, image_uri_art_crop, prices, promo, lang).
PrintingFetcher = Callable[[str], list[dict]]


def _parse_date(value) -> Optional[datetime.date]:
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def default_printing_fetcher(oracle_id: str) -> list[dict]:
    """Synchronous Scryfall ``unique=prints`` fetch for one oracle id.

    Raises httpx errors on upstream failure; callers treat any failure as
    "printings unavailable" and render the empty state.
    """
    out: list[dict] = []
    url: Optional[str] = SCRYFALL_SEARCH_URL
    params: Optional[dict] = {"q": f"oracleid:{oracle_id}", "unique": "prints"}
    with httpx.Client(timeout=SCRYFALL_TIMEOUT_S) as client:
        while url:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()
            for card in payload.get("data", []):
                images = card.get("image_uris") or {}
                if not images and card.get("card_faces"):
                    images = card["card_faces"][0].get("image_uris") or {}
                out.append({
                    "scryfall_id": card.get("id", ""),
                    "oracle_id": card.get("oracle_id") or oracle_id,
                    "set_code": card.get("set", ""),
                    "set_name": card.get("set_name", ""),
                    "collector_number": card.get("collector_number", ""),
                    "rarity": card.get("rarity", ""),
                    "released_at": card.get("released_at"),
                    "image_uri_normal": images.get("normal", ""),
                    "image_uri_art_crop": images.get("art_crop", ""),
                    "prices": card.get("prices") or {},
                    "promo": bool(card.get("promo")),
                    "lang": card.get("lang", ""),
                })
            url = payload.get("next_page") if payload.get("has_more") else None
            params = None
    return [p for p in out if p["scryfall_id"]]


def _choose_default(printings: list[dict]) -> Optional[str]:
    """Newest non-promo English printing (spec §1). Falls back to the newest
    English, then the newest overall, when the preferred pool is empty."""
    def _key(p: dict):
        return (_parse_date(p.get("released_at")) or datetime.date.min,
                p.get("set_code", ""), p.get("collector_number", ""))

    if not printings:
        return None
    english = [p for p in printings if p.get("lang") in ("en", "", None)]
    pool = english or list(printings)
    non_promo = [p for p in pool if not p.get("promo")]
    pool = non_promo or pool
    return max(pool, key=_key)["scryfall_id"]


def _ensure_printings(
    session: Session, oracle_id: str, fetcher: Optional[PrintingFetcher]
) -> list[dict]:
    """Printings rows for the card, fetched lazily from Scryfall on first
    view when the table has none. Fetch failures degrade to an empty list
    (the panel renders its 'no data yet' state) and never fail the dossier."""
    rows = repo.list_printings(session, oracle_id)
    if rows:
        return rows
    if fetcher is None:
        fetcher = default_printing_fetcher
    try:
        fetched = fetcher(oracle_id)
    except Exception as e:  # noqa: BLE001 - lazy cache fill must not 503 the page
        log.warning("printings fetch failed for %s: %s", oracle_id, e)
        return []
    if not fetched:
        return []
    default_id = _choose_default(fetched)
    repo.clear_default_printing(session, oracle_id)
    for p in fetched:
        repo.upsert_printing(session, {
            **p,
            "released_at": _parse_date(p.get("released_at")),
            "is_default": p["scryfall_id"] == default_id,
        })
    session.flush()
    return repo.list_printings(session, oracle_id)


def get_dossier(
    session: Session,
    oracle_id: str,
    printing: Optional[str] = None,
    printing_fetcher: Optional[PrintingFetcher] = None,
) -> Optional[dict]:
    """The aggregate dossier read model. None when the card is unknown
    (router renders 404); individual panels are empty lists/states when their
    query has no rows, never fabricated.

    ``printing_fetcher`` defaults to the live Scryfall fetch (resolved at call
    time so tests can monkeypatch it); pass a stub to control or disable the
    lazy printings fill."""
    card = repo.get_card(session, oracle_id)
    if card is None:
        return None

    printings = _ensure_printings(session, oracle_id, printing_fetcher)
    selected = None
    if printing:
        selected = next((p for p in printings if p["scryfall_id"] == printing), None)
    if selected is None:
        selected = next((p for p in printings if p["is_default"]), None)
    if selected is None and printings:
        selected = printings[0]  # newest (list_printings orders released_at desc)

    return {
        "card": card,
        "printing": selected,
        "printings": printings,
        "tags": repo.get_card_tags(session, oracle_id),
        "decks": repo.list_decks_for_card(session, oracle_id),
        "synergy": repo.list_synergy_neighbors(session, oracle_id, VOCAB_VERSION),
        "affinity": repo.get_card_notes(session, oracle_id),
        "collection": repo.card_collection_summary(session, oracle_id),
    }


def put_notes(
    session: Session, oracle_id: str, affinity: Optional[int], notes: str
) -> Optional[dict]:
    """Upsert Andrew's affinity/notes for a card. None when the card is
    unknown. ``affinity`` must be 1–5 or None (ValueError otherwise)."""
    if affinity is not None and not (1 <= int(affinity) <= 5):
        raise ValueError("affinity must be between 1 and 5")
    if repo.get_card(session, oracle_id) is None:
        return None
    return repo.upsert_card_notes(session, oracle_id, affinity, notes)


def import_card_tags_from_redis(r, session: Session) -> dict:
    """Mirror the legacy Redis lab tags (``lab_tags`` + ``lab_tags_source``)
    into ``card_tags``. Names resolve through the cards table (exact or
    front-face, same rule as the review verifier); unresolved names are
    reported, not silently dropped.
    """
    import json

    from src.server.domain.tags import lab_tags as lab_tags_mod

    raw = r.get("lab_tags")
    try:
        lab_tags = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        lab_tags = {}
    if not isinstance(lab_tags, dict):
        lab_tags = {}
    source_map = lab_tags_mod.get_source_map(r)

    names = [n for n, tags in lab_tags.items() if n and tags]
    cards = repo.get_cards_by_names(session, names)
    rows = []
    for name in names:
        card = cards.get(name)
        if card is None:
            continue
        for tag in lab_tags[name]:
            rows.append({
                "oracle_id": card["oracle_id"],
                "tag": tag,
                "source": source_map.get(name, {}).get(tag, lab_tags_mod.LEARNED),
            })
    written = repo.replace_card_tags(session, rows)
    return {
        "names_seen": len(names),
        "names_resolved": len({row["oracle_id"] for row in rows}),
        "names_unresolved": sorted(n for n in names if n not in cards),
        "tags_written": written,
    }
