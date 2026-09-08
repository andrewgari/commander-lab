"""
One-time rename of deck lifecycle status values in Redis:
    virtual -> digital
    test    -> testing

Old values were written by sync.py/app.py before the status vocabulary was
renamed to match the physical/digital/retired/testing registry model. This
only touches deck-level `status` (in the `decks` key JSON and any
`deck_status:{id}` keys) — it does NOT touch card-copy ownership status
strings (have/pending/possible/virtual) inside card:{name}.copies[], which
is a separate, unrelated vocabulary using "virtual" for a different concept.

Usage:
    python scripts/migrate_deck_status_rename.py            # dry run
    python scripts/migrate_deck_status_rename.py --apply
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
RENAME = {"virtual": "digital", "test": "testing"}


def migrate(apply: bool):
    r = redis.from_url(REDIS_URL, decode_responses=True)

    decks_json = r.get("decks")
    decks = json.loads(decks_json) if decks_json else []
    changed = 0
    for deck in decks:
        old = deck.get("status")
        if old in RENAME:
            print(f"deck {deck.get('id')} ({deck.get('name')}): {old} -> {RENAME[old]}")
            deck["status"] = RENAME[old]
            changed += 1

    if apply and changed:
        r.set("decks", json.dumps(decks))

    # Also rewrite standalone deck_status:{id} keys
    status_keys_changed = 0
    for deck in decks:
        key = f"deck_status:{deck.get('id')}"
        val = r.get(key)
        if val in RENAME:
            if apply:
                r.set(key, RENAME[val])
            status_keys_changed += 1

    print(f"\n{'Renamed' if apply else 'Would rename'} {changed} deck.status values "
          f"and {status_keys_changed} deck_status:* keys.")
    if not apply:
        print("Dry run only — re-run with --apply to write changes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    migrate(apply=args.apply)
