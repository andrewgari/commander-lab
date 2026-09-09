"""
One-time (but safely re-runnable), reviewable cleanup for the reversed
PR #18 behavior (see docs/PHYSICAL_RESYNC_ADJUDICATION.md section 2):
non-physical decks (digital/testing/retired) must have zero inventory
footprint, but PR #18's now-corrected registry.upsert_deck logic wishlist-
created `not_owned` instances for every deck regardless of status.

This script finds and deletes `not_owned` instances whose
`considered_for_deck` points at a deck whose *current* status is NOT
`physical`. Only `ownership_status == "not_owned"` instances are cleanup
candidates -- any other ownership_status (in_mail, in_collection, in_deck)
represents real inventory a human explicitly created and is never touched
here, regardless of the deck's status.

This does NOT run automatically as part of sync.py or registry.upsert_deck.
Run it manually, review the dry-run report, then re-run with --apply.

Usage:
    python scripts/remove_nonphysical_wishlist_instances.py            # dry run, prints a report
    python scripts/remove_nonphysical_wishlist_instances.py --apply    # actually deletes instances
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis

import registry
import instances as instance_store

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def find_candidates(r) -> list:
    """Return the list of not_owned instance records that are cleanup
    candidates: considered_for_deck resolves to a deck whose current status
    is NOT physical (including decks that no longer exist)."""
    decks = registry.list_decks(r)
    status_by_registry_id = {registry.registry_id_of(d): d.get("status") for d in decks}

    candidates = []
    for inst_id in r.keys("instance:*"):
        instance_id = inst_id[len("instance:"):] if isinstance(inst_id, str) else inst_id.decode()[len("instance:"):]
        record = instance_store.get_instance(r, instance_id)
        if not record:
            continue
        if record.get("ownership_status") != "not_owned":
            continue
        considered_for_deck = record.get("considered_for_deck")
        if not considered_for_deck:
            continue

        deck_status = status_by_registry_id.get(considered_for_deck)
        # Deck missing entirely is treated the same as "not physical" --
        # there's no physical deck backing this wishlist placeholder.
        if deck_status != "physical":
            candidates.append(record)

    return candidates


def cleanup(apply: bool):
    r = redis.from_url(REDIS_URL, decode_responses=True)

    candidates = find_candidates(r)
    print(f"Found {len(candidates)} not_owned instance(s) considered_for a non-physical deck:")

    by_deck = {}
    for record in candidates:
        by_deck.setdefault(record.get("considered_for_deck"), []).append(record)

    for deck_registry_id, records in sorted(by_deck.items(), key=lambda kv: str(kv[0])):
        print(f"  {deck_registry_id}: {len(records)} instance(s)")
        for record in records:
            print(f"    - {record['card_name']} (instance {record['id']})")

    if apply:
        for record in candidates:
            instance_store.delete_instance(r, record["id"])
        print(f"\nDeleted {len(candidates)} instance(s).")
    else:
        print("\nDry run only -- no data deleted. Re-run with --apply to delete these instances.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Actually delete the matched instances")
    args = parser.parse_args()

    cleanup(apply=args.apply)
