#!/usr/bin/env python3
"""Fill the card facts of stub ``cards`` rows from Scryfall's oracle-cards bulk file.

``migrate_instances_to_pg.py`` creates name-only stub rows (empty type_line,
color_identity, rarity, cmc 0) for oracle ids no deck import has touched. The
owned-mode search filters on those columns in SQL (docs/ui/card-search.md
§5.2), so stubs make every filter a no-op or a miss. This script hydrates them.

Idempotent: only rows with an empty ``type_line`` are touched. It also fetches the full printing list
(all editions, with set/date/rarity) for hydrated cards that have none, so the
rarity filter matches any printing.

Usage:
    python scripts/hydrate_stub_cards.py [--bulk-file PATH] [--dry-run]

Env: DATABASE_URL (required). Without --bulk-file the oracle-cards bulk file is
downloaded from Scryfall (https://scryfall.com/docs/api/bulk-data).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from src.server.infra.db import repositories as repo  # noqa: E402
from src.server.infra.db.orm_models import CardRow, PrintingRow  # noqa: E402
from src.server.infra.services import dossier_service  # noqa: E402

BULK_INDEX = "https://api.scryfall.com/bulk-data/oracle-cards"
_HEADERS = {"User-Agent": "commander-lab/hydrate-stub-cards", "Accept": "application/json"}


def _face_text(card: dict, key: str) -> str:
    if card.get(key):
        return card[key]
    return "\n//\n".join(f.get(key) or "" for f in card.get("card_faces") or [])


def to_card_dict(sf: dict) -> dict:
    """Scryfall card object -> ``repo.upsert_card`` dict."""
    faces = sf.get("card_faces") or []
    uris = sf.get("image_uris") or (faces[0].get("image_uris") if faces else None) or {}
    colors = sf.get("colors")
    if colors is None and faces:
        colors = faces[0].get("colors")
    return {
        "oracle_id": sf.get("oracle_id") or (faces[0].get("oracle_id") if faces else ""),
        "name": sf.get("name", ""),
        "type_line": sf.get("type_line") or (faces[0].get("type_line", "") if faces else ""),
        "oracle_text": _face_text(sf, "oracle_text"),
        "mana_cost": sf.get("mana_cost") or (faces[0].get("mana_cost", "") if faces else ""),
        "colors": colors or [],
        "color_identity": sf.get("color_identity") or [],
        "cmc": sf.get("cmc") or 0,
        "keywords": sf.get("keywords") or [],
        "legalities": sf.get("legalities") or {},
        "image_uri": uris.get("normal") or uris.get("small") or "",
    }


def hydrate(session, scryfall_cards: Iterable[dict], printing_fetcher=None) -> dict:
    """Hydrate every stub row (empty type_line) found in ``scryfall_cards``.

    Returns {"stubs", "hydrated", "unmatched": [names], "printings"}.
    """
    stubs = {oid: name for oid, name in session.execute(
        select(CardRow.oracle_id, CardRow.name).where(CardRow.type_line == "")).all()}
    have_printing = set(session.scalars(select(PrintingRow.oracle_id).distinct()).all())
    stats = {"stubs": len(stubs), "hydrated": 0, "unmatched": [], "printings": 0}
    done = set()
    for sf in scryfall_cards:
        oid = sf.get("oracle_id")
        if oid not in stubs or oid in done:
            continue
        card = to_card_dict(sf)
        if not card["type_line"]:
            continue
        repo.upsert_card(session, card)
        done.add(oid)
        stats["hydrated"] += 1
        if oid not in have_printing and printing_fetcher is not None:
            try:
                fetched = printing_fetcher(oid)
            except Exception:  # noqa: BLE001 - leave to the lazy dossier fill
                fetched = []
            if fetched:
                default_id = dossier_service._choose_default(fetched)
                repo.clear_default_printing(session, oid)
                for p in fetched:
                    repo.upsert_printing(session, {
                        **p,
                        "released_at": dossier_service._parse_date(p.get("released_at")),
                        "is_default": p["scryfall_id"] == default_id,
                    })
                stats["printings"] += len(fetched)
    stats["unmatched"] = sorted(n for o, n in stubs.items() if o not in done)
    return stats


def _throttled(fetcher):
    def wrapped(oracle_id):
        time.sleep(0.1)  # Scryfall courtesy limit
        return fetcher(oracle_id)
    return wrapped


def _load_bulk(path: str | None) -> list:
    if path:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    import httpx

    meta = httpx.get(BULK_INDEX, headers=_HEADERS, timeout=30).raise_for_status().json()
    resp = httpx.get(meta["download_uri"], headers=_HEADERS, timeout=300, follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--bulk-file", help="local oracle-cards JSON (skips the download)")
    ap.add_argument("--dry-run", action="store_true", help="report, then roll back")
    args = ap.parse_args(argv)

    from src.server.infra.db.session import get_session_factory, session_scope

    bulk = _load_bulk(args.bulk_file)
    with session_scope(get_session_factory()) as session:
        stats = hydrate(session, bulk, printing_fetcher=_throttled(dossier_service.default_printing_fetcher))
        if args.dry_run:
            session.rollback()
    print(f"stubs: {stats['stubs']}, hydrated: {stats['hydrated']}, "
          f"printings written: {stats['printings']}, unmatched: {len(stats['unmatched'])}")
    for name in stats["unmatched"][:50]:
        print(f"  - {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
