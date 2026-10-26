#!/usr/bin/env python3
"""Migrate legacy AN-3 composition overrides to composition-v1 records.

Usage:
    python scripts/migrate_composition.py
    python scripts/migrate_composition.py --dry-run
"""
import argparse
import logging
import sys

import redis

from src.server.domain.analytics.composition_store import migrate_all_legacy_compositions, _get_json, SPEC_VERSION

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Migrate legacy composition overrides")
    parser.add_argument("--dry-run", action="store_true", help="Count without writing")
    parser.add_argument("--url", default="redis://localhost:6379/0", help="Redis URL")
    args = parser.parse_args()

    r = redis.from_url(args.url)
    r.ping()

    # Count legacy decks first
    count = 0
    for key in r.keys("deck_journal:*"):
        key_str = key.decode() if isinstance(key, bytes) else key
        journal = _get_json(r, key_str) or {}
        legacy = journal.get("composition") or {}
        if legacy and legacy.get("_schema") != SPEC_VERSION:
            count += 1

    if not count:
        log.info("No legacy composition overrides to migrate.")
        return 0

    if args.dry_run:
        log.info("Dry run: %d decks have legacy composition overrides.", count)
        return 0

    migrated = migrate_all_legacy_compositions(r)
    log.info("Migrated %d/%d decks.", migrated, count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
