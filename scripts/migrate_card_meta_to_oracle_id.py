"""
One-time, reviewable backfill from the legacy name-keyed `card_meta:{name}`
records to the new `card:{oracle_id}` ORM keyspace + `idx:card_name_to_oracle`
index (see cards.py and docs/DECK_MANAGEMENT_VIEW.md ADDENDUM).

This does NOT run automatically as part of sync.py (sync.py writes both
keyspaces going forward for cards it sees; this script exists to backfill
everything sync.py hasn't touched again since the ORM keyspace was added).

Names alone don't carry a Scryfall oracle_id, so this script resolves each
`card_meta:{name}` entry to an oracle_id via a Scryfall `/cards/named` lookup
(rate-limited, same public API the rest of the app already depends on for
card images — see mtg-apis skill). Entries that fail to resolve (typo'd
name, Scryfall outage, etc.) are reported and skipped — nothing is deleted
or overwritten on failure.

Old `card_meta:{name}` keys are left in place; nothing deletes them here.

Usage:
    python scripts/migrate_card_meta_to_oracle_id.py            # dry run, prints a report
    python scripts/migrate_card_meta_to_oracle_id.py --apply    # actually writes card:{oracle_id} + index
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis
import requests

import cards as card_store

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
SCRYFALL_NAMED_URL = "https://api.scryfall.com/cards/named"
RATE_LIMIT_SECONDS = 0.1  # Scryfall asks for ~10 req/s max


def resolve_oracle_id(card_name: str):
    """Look up a card by exact name via Scryfall and return its oracle_id.
    Raises on network error; returns None if Scryfall has no exact match."""
    res = requests.get(SCRYFALL_NAMED_URL, params={"exact": card_name}, timeout=20)
    if res.status_code == 404:
        return None
    res.raise_for_status()
    data = res.json()
    return data.get("oracle_id")


def migrate(apply: bool, r=None):
    """Run the backfill. `r` is an optional injected redis-like client
    (used by tests to avoid a real Redis connection); defaults to a real
    connection built from REDIS_URL when not supplied."""
    if r is None:
        r = redis.from_url(REDIS_URL, decode_responses=True)

    meta_keys = r.keys("card_meta:*")
    print(f"Found {len(meta_keys)} legacy card_meta:* records.")

    migrated = 0
    already_indexed = 0
    unresolved = []

    for key in meta_keys:
        card_name = key[len("card_meta:"):]

        # Skip names that already resolve through the index (e.g. re-running
        # this script, or sync.py already wrote this one going forward).
        existing = card_store.get_card_by_name(r, card_name)
        if existing:
            already_indexed += 1
            continue

        raw = r.get(key)
        if not raw:
            continue
        try:
            meta = json.loads(raw)
        except (TypeError, ValueError):
            unresolved.append((card_name, "unparseable card_meta JSON"))
            continue

        try:
            oracle_id = resolve_oracle_id(card_name)
        except requests.RequestException as e:
            unresolved.append((card_name, f"Scryfall request failed: {e}"))
            continue
        time.sleep(RATE_LIMIT_SECONDS)

        if not oracle_id:
            unresolved.append((card_name, "no exact Scryfall match"))
            continue

        record = {
            "oracle_id": oracle_id,
            "name": card_name,
            "type": meta.get("type", "Unknown"),
            "color": meta.get("color", "C"),
            "super_types": meta.get("super_types", []),
            "sub_types": meta.get("sub_types", []),
            "keywords": meta.get("keywords", []),
            "oracle_text": meta.get("oracle_text", ""),
            "cmc": meta.get("cmc", 0),
        }

        migrated += 1
        if apply:
            card_store.upsert_card(r, record)

    print(f"\n{'Would migrate' if not apply else 'Migrated'} {migrated} card_meta "
          f"records to card:{{oracle_id}}.")
    print(f"Already indexed (skipped): {already_indexed}")

    if unresolved:
        print(f"\nWARNING: {len(unresolved)} card(s) could not be resolved to an "
              f"oracle_id (left untouched, still readable via card_meta:{{name}}):")
        for name, reason in unresolved:
            print(f"  - {name}: {reason}")

    if not apply:
        print("\nDry run only — no data written. Re-run with --apply to write "
              "card:{oracle_id} records + the name index.")

    return {
        "found": len(meta_keys),
        "migrated": migrated,
        "already_indexed": already_indexed,
        "unresolved": unresolved,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Actually write card:{oracle_id} records")
    args = parser.parse_args()

    migrate(apply=args.apply)
