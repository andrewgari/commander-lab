"""
One-time, reviewable migration from the old card:{name}.copies[] blob model
to the new instance:{id} registry (see docs/CARD_DATABASE.md).

This does NOT run automatically as part of sync.py. Run it manually once you're
ready, review the output, then optionally delete the old card:* keys.

Usage:
    python scripts/migrate_to_instances.py            # dry run, prints a report
    python scripts/migrate_to_instances.py --apply     # actually writes instances
    python scripts/migrate_to_instances.py --apply --delete-old   # + removes old card:* keys
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis
import instances as instance_store

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# old copy.status -> (new ownership_status, deck_id_used)
STATUS_MAP = {
    "have": ("in_deck", True),
    "pending": ("in_mail", False),
    "possible": ("not_owned", False),
    "virtual": ("not_owned", False),
}


def resolve_deck_id(decks_by_name: dict, deck_name: str):
    deck = decks_by_name.get(deck_name)
    return deck.get("id") if deck else None


def migrate(apply: bool, delete_old: bool):
    r = redis.from_url(REDIS_URL, decode_responses=True)

    decks_json = r.get("decks")
    all_decks = json.loads(decks_json) if decks_json else []
    decks_by_name = {d["name"]: d for d in all_decks}

    old_keys = r.keys("card:*")
    print(f"Found {len(old_keys)} legacy card:* records.")

    total_instances = 0
    counts_by_new_status = {"in_deck": 0, "in_mail": 0, "in_collection": 0, "not_owned": 0}
    unresolved_decks = set()

    for key in old_keys:
        card_name = key[len("card:"):]
        raw = r.get(key)
        if not raw:
            continue
        card_data = json.loads(raw)

        # Write/refresh card_meta regardless of apply mode's copy handling
        meta = {
            "type": card_data.get("type", "Unknown"),
            "color": card_data.get("color", "C"),
            "super_types": card_data.get("super_types", []),
            "sub_types": card_data.get("sub_types", []),
            "keywords": card_data.get("keywords", []),
            "oracle_text": card_data.get("oracle_text", ""),
            "cmc": card_data.get("cmc", 0),
        }
        if apply:
            r.set(f"card_meta:{card_name}", json.dumps(meta))

        for copy in card_data.get("copies", []):
            old_status = copy.get("status", "have")
            new_status, needs_deck = STATUS_MAP.get(old_status, ("not_owned", False))
            deck_name = copy.get("deck", "")
            deck_id = resolve_deck_id(decks_by_name, deck_name) if deck_name else None

            considered_for_deck = None
            if not needs_deck and deck_name:
                considered_for_deck = deck_name

            if needs_deck and not deck_id:
                unresolved_decks.add(deck_name)
                # Can't place in_deck without a resolvable deck id; fall back
                new_status = "in_collection"
                considered_for_deck = deck_name

            total_instances += 1
            counts_by_new_status[new_status] += 1

            if apply:
                instance_store.create_instance(
                    r,
                    card_name=card_name,
                    ownership_status=new_status,
                    set=copy.get("set", ""),
                    set_name=copy.get("set_name", ""),
                    finish="foil" if copy.get("modifier") == "Foil" else "nonfoil",
                    deck_id=deck_id if new_status == "in_deck" else None,
                    deck_name=deck_name if new_status == "in_deck" else "",
                    considered_for_deck=considered_for_deck,
                    price_paid=copy.get("price", 0.0),
                    archidekt_uid=copy.get("uid", ""),
                )

    print(f"\n{'Would create' if not apply else 'Created'} {total_instances} instances:")
    for status, count in counts_by_new_status.items():
        print(f"  {status}: {count}")

    if unresolved_decks:
        print(f"\nWARNING: {len(unresolved_decks)} deck name(s) from old copies could not "
              f"be resolved to a deck id (fell back to in_collection):")
        for name in sorted(unresolved_decks):
            print(f"  - {name}")

    if apply and delete_old:
        r.delete(*old_keys)
        print(f"\nDeleted {len(old_keys)} legacy card:* keys.")
    elif not apply:
        print("\nDry run only — no data written. Re-run with --apply to write instances.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Actually write instance records")
    parser.add_argument("--delete-old", action="store_true", help="Delete old card:* keys after applying")
    args = parser.parse_args()

    if args.delete_old and not args.apply:
        print("--delete-old requires --apply")
        sys.exit(1)

    migrate(apply=args.apply, delete_old=args.delete_old)
