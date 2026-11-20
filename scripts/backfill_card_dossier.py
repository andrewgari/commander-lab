#!/usr/bin/env python3
"""Backfill card-dossier data into Postgres (spec §2 import note):

1. printings — Scryfall ``unique=prints`` for every card in the ``cards``
   table that has no printings rows yet. NOT page blocking: the dossier
   endpoint fetches lazily per card on first view; this script is the bulk
   warm-up. Idempotent (skips cards that already have rows; upserts
   otherwise) and rate-limited (~10 req/s Scryfall courtesy).
2. card_tags — mirror the legacy Redis ``lab_tags`` / ``lab_tags_source``
   keys into the PG table (names resolve through the cards table;
   unresolved names are reported, never silently dropped).

Usage:
    python scripts/backfill_card_dossier.py [--skip-printings] [--skip-tags]
        [--limit N] [--redis-url URL]

Env: DATABASE_URL (required), REDIS_URL (default redis://localhost:6379/0).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from src.server.infra.db import repositories as repo  # noqa: E402
from src.server.infra.db.orm_models import CardRow  # noqa: E402
from src.server.infra.db.session import get_session_factory, session_scope  # noqa: E402
from src.server.infra.services import dossier_service  # noqa: E402

log = logging.getLogger("backfill_card_dossier")

SCRYFALL_SLEEP_S = 0.1  # ~10 req/s courtesy limit


def backfill_printings(factory, limit: int | None, sleep=time.sleep) -> dict:
    from src.server.infra.db.orm_models import PrintingRow

    with session_scope(factory) as session:
        oracle_ids = session.scalars(select(CardRow.oracle_id)).all()
        have = set(session.scalars(select(PrintingRow.oracle_id).distinct()).all())
    missing = [oid for oid in oracle_ids if oid not in have]
    if limit:
        missing = missing[:limit]

    stats = {"cards_seen": len(oracle_ids), "cards_missing": len(missing),
             "cards_filled": 0, "cards_failed": 0, "printings_written": 0}
    for i, oracle_id in enumerate(missing, 1):
        try:
            fetched = dossier_service.default_printing_fetcher(oracle_id)
        except Exception as e:  # noqa: BLE001 - one card's failure must not stop the run
            log.warning("printings fetch failed for %s: %s", oracle_id, e)
            stats["cards_failed"] += 1
            sleep(SCRYFALL_SLEEP_S)
            continue
        if fetched:
            default_id = dossier_service._choose_default(fetched)
            with session_scope(factory) as session:
                repo.clear_default_printing(session, oracle_id)
                for p in fetched:
                    repo.upsert_printing(session, {
                        **p,
                        "released_at": dossier_service._parse_date(p.get("released_at")),
                        "is_default": p["scryfall_id"] == default_id,
                    })
            stats["cards_filled"] += 1
            stats["printings_written"] += len(fetched)
        if i % 50 == 0:
            log.info("printings backfill: %d/%d cards", i, len(missing))
        sleep(SCRYFALL_SLEEP_S)
    return stats


def import_tags(factory, redis_url: str) -> dict:
    import redis

    r = redis.from_url(redis_url, decode_responses=True)
    with session_scope(factory) as session:
        return dossier_service.import_card_tags_from_redis(r, session)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    p.add_argument("--skip-printings", action="store_true")
    p.add_argument("--skip-tags", action="store_true")
    p.add_argument("--limit", type=int, default=None,
                   help="max cards to fetch printings for (smoke runs)")
    p.add_argument("--redis-url", default=os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    args = p.parse_args(argv)

    if not os.environ.get("DATABASE_URL"):
        p.error("DATABASE_URL is required")

    factory = get_session_factory()
    if not args.skip_printings:
        stats = backfill_printings(factory, args.limit)
        log.info("printings backfill: %s", stats)
    if not args.skip_tags:
        stats = import_tags(factory, args.redis_url)
        log.info("card tags import: %s", stats)
        if stats["names_unresolved"]:
            log.warning("unresolved tag names (%d): %s",
                        len(stats["names_unresolved"]), stats["names_unresolved"][:10])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
