#!/usr/bin/env python
"""Pull Scryfall's creature-type catalog into Redis (catalog:creature_types, 7-day TTL).

The Journal's Kindred autocomplete reads that key and falls back to the snapshot bundled in
deck_genre.py (CREATURE_TYPES_SNAPSHOT), so running this is optional: it only matters when
a new set adds creature types. Safe to run from cron weekly.

Usage: REDIS_URL=redis://host:6379/0 python scripts/refresh_creature_types.py [--check]
  --check  print how the live catalog differs from the bundled snapshot; write nothing.
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.server.domain.decks.deck_genre as deck_genre  # noqa: E402


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "commander-lab", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - fixed https URL
        return json.loads(resp.read().decode("utf-8"))


def main(check: bool) -> int:
    if check:
        live = set(_fetch(deck_genre.CATALOG_URL).get("data") or [])
        snap = set(deck_genre.CREATURE_TYPES_SNAPSHOT)
        print(f"live {len(live)} · snapshot {len(snap)} · new {sorted(live - snap)} · gone {sorted(snap - live)}")
        return 0 if live == snap else 1
    import redis

    r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    n = deck_genre.refresh_creature_types(r, fetch=_fetch)
    print(f"Stored {n} creature types at {deck_genre.CATALOG_KEY} (TTL {deck_genre.CATALOG_TTL // 86400}d).")
    return 0


if __name__ == "__main__":
    sys.exit(main("--check" in sys.argv[1:]))
