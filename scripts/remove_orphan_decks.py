"""
Remove orphan entries from the `decks` registry key: decks with no `source`
and no cardlist (prod has one, a leftover `test-deck` with id "1"). Also
drops their `deck_status:{id}` key. Instances are never touched; a deck that
still has bound instances is reported and kept.

Usage:
    python scripts/remove_orphan_decks.py            # dry run
    python scripts/remove_orphan_decks.py --apply
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis  # noqa: E402

import instances as instance_store  # noqa: E402
import registry  # noqa: E402

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def is_orphan(deck: dict) -> bool:
    return not deck.get("source") and not deck.get("cards")


def run(r, apply: bool) -> dict:
    decks = registry.list_decks(r)
    removed, kept_bound = [], []
    survivors = []
    for d in decks:
        if not is_orphan(d):
            survivors.append(d)
            continue
        if instance_store.list_deck_instances(r, registry.deck_instance_ids(d)):
            kept_bound.append(d.get("name"))
            survivors.append(d)
            continue
        removed.append(d)
    if apply and removed:
        r.set(registry.DECKS_KEY, json.dumps(survivors))
        for d in removed:
            r.delete(f"deck_status:{d.get('id')}")
    return {"removed": [d.get("name") for d in removed], "kept_bound": kept_bound}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    rep = run(redis.from_url(REDIS_URL, decode_responses=True), apply=args.apply)
    print(f"{'Removed' if args.apply else 'Would remove'}: {rep['removed']}")
    if rep["kept_bound"]:
        print(f"Kept (orphan but has bound instances): {rep['kept_bound']}")
