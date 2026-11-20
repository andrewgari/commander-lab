#!/usr/bin/env python3
"""One-time migration: Redis card-instance registry -> Postgres card_instances.

ARCHITECTURE.md §9: run ``--dry-run`` first (counts + unresolved names, writes
nothing), then ``--apply``, then ``--verify`` (reconciliation report comparing
Redis vs Postgres per (oracle_id, status)). Idempotent: existing instance ids
are skipped, so re-running ``--apply`` after a partial run is safe.

Usage:
    python scripts/migrate_instances_to_pg.py --dry-run
    python scripts/migrate_instances_to_pg.py --apply
    python scripts/migrate_instances_to_pg.py --verify

Env: REDIS_URL (default redis://localhost:6379/0), DATABASE_URL (required for
--apply/--verify; may point at sqlite:///... for local rehearsal).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.server.infra.db import repositories as repo
from src.server.infra.db.session import get_session_factory, session_scope


def _redis_client():
    import redis  # deferred: --dry-run on a host without redis lib still errors clearly

    import os

    return redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_redis_instances(r) -> tuple[list[tuple[dict, str]], dict]:
    """All instance:* records + name->oracle resolution stats."""
    records = []
    for key in r.scan_iter("instance:*"):
        raw = r.get(key)
        if not raw:
            continue
        try:
            records.append(json.loads(raw))
        except json.JSONDecodeError:
            records.append({"id": key.split(":", 1)[1], "_unparseable": True})

    unresolved: list[str] = []
    mapped = 0
    name_cache: dict[str, str | None] = {}

    def oracle_for(name: str) -> str | None:
        if name not in name_cache:
            oid = r.get(f"idx:card_name_to_oracle:{name}")
            if oid is None and r.exists(f"card:{name}"):  # legacy name-keyed card
                card = r.get(f"card:{name}")
                oid = json.loads(card).get("oracle_id") if card else None
            name_cache[name] = oid.decode() if isinstance(oid, bytes) else oid
        return name_cache[name]

    out = []
    for rec in records:
        if rec.get("_unparseable"):
            unresolved.append(f"<unparseable:{rec['id']}>")
            continue
        oid = rec.get("oracle_id") or oracle_for(rec.get("card_name", ""))
        if not oid:
            unresolved.append(rec.get("card_name", rec.get("id", "?")))
            continue
        mapped += 1
        out.append((rec, oid))
    stats = {"redis_records": len(records), "resolvable": mapped, "unresolved": unresolved}
    return out, stats


def to_pg_instance(rec: dict, oracle_id: str, existing_deck_ids: set[str]) -> dict:
    """Map a Redis instance record to the card_instances shape."""
    legacy_ref = rec.get("deck_id")
    deck_id = None
    if legacy_ref is not None:
        candidate = f"archidekt:{legacy_ref}"
        if candidate in existing_deck_ids:
            deck_id = candidate
    return {
        "id": str(rec["id"]),
        "oracle_id": oracle_id,
        "status": rec.get("ownership_status", "not_owned"),
        "deck_id": deck_id,
        "finish": rec.get("finish", "nonfoil"),
        "condition": rec.get("condition", "NM"),
        "acquired_at": _parse_ts(rec.get("date_acquired", "")),
        "notes": rec.get("notes", ""),
        "set_code": (rec.get("set") or "")[:16],
        "collector_number": (rec.get("collector_number") or "")[:32],
        "price_paid": float(rec.get("price_paid") or 0),
        "source": (rec.get("source") or "")[:64],
        "considered_for_deck": rec.get("considered_for_deck"),
        "legacy_deck_ref": None if legacy_ref is None else str(legacy_ref),
    }


def redis_counts(pairs: list[tuple[dict, str]]) -> Counter:
    return Counter((oid, rec.get("ownership_status", "not_owned")) for rec, oid in pairs)


def cmd_dry_run(args) -> int:
    r = _redis_client()
    pairs, stats = load_redis_instances(r)
    print(f"redis instance records: {stats['redis_records']}")
    print(f"resolvable to oracle_id: {stats['resolvable']}")
    print(f"unresolved names ({len(stats['unresolved'])}):")
    for name in stats["unresolved"][:50]:
        print(f"  - {name}")
    by_status = Counter(rec.get("ownership_status", "?") for rec, _ in pairs)
    print("by ownership_status:", dict(by_status))
    return 1 if stats["unresolved"] else 0


def cmd_apply(args) -> int:
    r = _redis_client()
    pairs, stats = load_redis_instances(r)
    if stats["unresolved"]:
        print(f"refusing to apply: {len(stats['unresolved'])} unresolved card names "
              f"(run --dry-run); fix idx:card_name_to_oracle first", file=sys.stderr)
        return 1
    factory = get_session_factory()
    inserted = skipped = 0
    with session_scope(factory) as session:
        existing_deck_ids = {d["id"] for d in repo.list_decks(session, include_archived=True)}
        # card_instances.oracle_id FKs to cards: stub rows for oracle_ids no
        # deck import has created yet (import refreshes them with full data).
        known = repo.known_oracle_ids(session, [oid for _, oid in pairs])
        for rec, oid in pairs:
            if oid not in known:
                repo.upsert_card(session, {"oracle_id": oid, "name": rec.get("card_name", "")})
                known.add(oid)
        for rec, oid in pairs:
            inst = to_pg_instance(rec, oid, existing_deck_ids)
            if repo.insert_instance(session, inst):
                inserted += 1
                repo.insert_event(session, {
                    "instance_id": inst["id"],
                    "ts": _parse_ts(rec.get("created_at", "")) or datetime.now(timezone.utc),
                    "kind": "migrated",
                    "to_deck": inst["deck_id"],
                })
            else:
                skipped += 1
        # Rebind pass: instances migrated before their deck was imported land
        # unbound; once the deck exists in Postgres, attach them.
        rebound = repo.rebind_instances(session, existing_deck_ids)
        for instance_id, deck_id in rebound:
            repo.insert_event(session, {
                "instance_id": instance_id,
                "kind": "assigned",
                "to_deck": deck_id,
            })
    print(f"inserted: {inserted}, already present (skipped): {skipped}, "
          f"rebound to imported decks: {len(rebound)}")
    return 0


def cmd_verify(args) -> int:
    r = _redis_client()
    pairs, stats = load_redis_instances(r)
    expected = redis_counts(pairs)
    factory = get_session_factory()
    with session_scope(factory) as session:
        actual = Counter(repo.instance_counts_by_oracle(session))
    keys = set(expected) | set(actual)
    mismatches = [
        (k, expected.get(k, 0), actual.get(k, 0))
        for k in sorted(keys)
        if expected.get(k, 0) != actual.get(k, 0)
    ]
    print(f"redis resolvable instances: {sum(expected.values())}; "
          f"postgres instances: {sum(actual.values())}")
    if stats["unresolved"]:
        print(f"note: {len(stats['unresolved'])} unresolved redis records excluded from both sides")
    if mismatches:
        print(f"MISMATCHES ({len(mismatches)}):")
        for (oid, status), exp, act in mismatches[:50]:
            print(f"  {oid} [{status}]: redis={exp} pg={act}")
        return 1
    print("reconciliation OK: redis and postgres counts match per (oracle_id, status)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="report counts + unresolved names; writes nothing")
    g.add_argument("--apply", action="store_true", help="insert instances (idempotent) + migration events")
    g.add_argument("--verify", action="store_true", help="reconcile redis vs postgres counts")
    args = ap.parse_args()
    if args.dry_run:
        return cmd_dry_run(args)
    if args.apply:
        return cmd_apply(args)
    return cmd_verify(args)


if __name__ == "__main__":
    raise SystemExit(main())
