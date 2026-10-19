"""
Remove duplicate card instances left by running the instance import twice
(prod: 2026-09-08 and 2026-09-25 each wrote the same 5,608 instances).

Two instances are "the same copy" when they share
    (card_name, deck_id, archidekt_uid, ownership_status, considered_for_deck)
Legitimate multiples exist inside ONE import (e.g. 30 Mountains with the same
Archidekt uid), so the number kept per group is the largest count any single
import batch wrote for it (batch = created_at date), not 1.

Which copies survive, per group: instances that were edited after creation
(updated_at != created_at, or more than one history entry) first, then the
oldest. Deletion goes through instances.delete_instance so every secondary
index is cleaned up.

Usage:
    python scripts/dedupe_instances.py            # dry run, prints a report
    python scripts/dedupe_instances.py --apply    # delete the duplicates
"""
import argparse
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis  # noqa: E402

import instances as instance_store  # noqa: E402

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def copy_key(inst: dict) -> tuple:
    return (
        inst.get("card_name") or "",
        str(inst.get("deck_id")) if inst.get("deck_id") not in (None, "") else "",
        inst.get("archidekt_uid") or "",
        inst.get("ownership_status") or "",
        str(inst.get("considered_for_deck") or ""),
    )


def _batch(inst: dict) -> str:
    return (inst.get("created_at") or "")[:10]


def _edited(inst: dict) -> bool:
    return inst.get("updated_at") != inst.get("created_at") or len(inst.get("history") or []) > 1


def plan(all_instances: list) -> list:
    """Return the instance records to delete."""
    groups = defaultdict(list)
    for inst in all_instances:
        groups[copy_key(inst)].append(inst)

    doomed = []
    for members in groups.values():
        keep = max(Counter(_batch(i) for i in members).values())
        if len(members) <= keep:
            continue
        members.sort(key=lambda i: (not _edited(i), i.get("created_at") or "", i["id"]))
        doomed.extend(members[keep:])
    return doomed


def run(r, apply: bool) -> dict:
    all_instances = instance_store.load_all_instances(r)
    doomed = plan(all_instances)
    report = {
        "before": len(all_instances),
        "delete": len(doomed),
        "after": len(all_instances) - len(doomed),
        "delete_by_batch": dict(Counter(_batch(i) for i in doomed)),
    }
    if apply:
        for inst in doomed:
            instance_store.delete_instance(r, inst["id"])
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Actually delete duplicates")
    args = parser.parse_args()
    client = redis.from_url(REDIS_URL, decode_responses=True)
    rep = run(client, apply=args.apply)
    print(f"Instances before: {rep['before']}")
    print(f"{'Deleted' if args.apply else 'Would delete'}: {rep['delete']} {rep['delete_by_batch']}")
    print(f"Instances after:  {rep['after']}")
    if not args.apply:
        print("Dry run only. Re-run with --apply to delete.")
