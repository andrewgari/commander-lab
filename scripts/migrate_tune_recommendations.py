"""
Prepare / verify Redis for saved Tune recommendations (analytics/tune_store.py).

Redis has no schema, so there is no table to create. This script is idempotent and:
  * stamps `tunerec:schema_version`
  * repairs the one-active-per-deck invariant for any existing records: if a deck
    has several `active` records the newest stays active, the rest become
    `archived` (archived_at set); the `tunerec:active:{deck}` pointer is made to
    match. Nothing is deleted.

Usage:
    python scripts/migrate_tune_recommendations.py            # dry run
    python scripts/migrate_tune_recommendations.py --apply
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import tune_store as ts  # noqa: E402


def migrate(r, apply: bool = False) -> dict:
    stats = {"decks": 0, "records": 0, "demoted": 0, "pointers_fixed": 0}
    for index_key in sorted(r.keys(ts.INDEX_PREFIX + "*")):
        deck_id = index_key[len(ts.INDEX_PREFIX):]
        stats["decks"] += 1
        recs = []
        for rec_id in r.lrange(index_key, 0, -1):  # newest first
            raw = r.get(ts.REC_PREFIX + rec_id)
            if raw:
                recs.append(json.loads(raw))
        stats["records"] += len(recs)
        actives = [x for x in recs if x.get("status") == ts.ACTIVE]
        keep = actives[0] if actives else None
        for extra in actives[1:]:
            stats["demoted"] += 1
            print(f"{deck_id}: archive duplicate active {extra['id']}")
            if apply:
                extra.update(status=ts.ARCHIVED, archived_at=extra.get("archived_at") or ts._now())
                r.set(ts.REC_PREFIX + extra["id"], json.dumps(extra))
        want = keep["id"] if keep else None
        if r.get(ts.ACTIVE_PREFIX + deck_id) != want:
            stats["pointers_fixed"] += 1
            print(f"{deck_id}: active pointer -> {want}")
            if apply:
                if want:
                    r.set(ts.ACTIVE_PREFIX + deck_id, want)
                else:
                    r.delete(ts.ACTIVE_PREFIX + deck_id)
    if apply:
        r.set(ts.SCHEMA_KEY, str(ts.SCHEMA_VERSION))
    return stats


if __name__ == "__main__":
    import redis

    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    rr = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    print(migrate(rr, a.apply), "(applied)" if a.apply else "(dry run)")
