#!/usr/bin/env python3
"""One-time full-collection migration: Redis -> Postgres (ARCHITECTURE.md
§8 Slice 2, §9).

Covers every collection key family: ``decks`` (all decks, live Archidekt
import with Redis fallback), ``deck_status:*`` (lifecycle statuses mapped to
the §4.2 enum), ``instance:*`` (every physical copy exactly once, with
provenance events seeded from current Redis state).

    python scripts/migrate_collection_to_pg.py --dry-run   # counts per family, writes nothing
    python scripts/migrate_collection_to_pg.py --apply     # migrate (idempotent)
    python scripts/migrate_collection_to_pg.py --verify    # reconciliation report

Env: REDIS_URL (default redis://localhost:6379/0), DATABASE_URL (required for
--apply/--verify; sqlite:///... works for local rehearsal).
Flags: --no-live (skip Archidekt fetches; all decks via Redis fallback),
--sleep N (politeness delay between live fetches).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.server.infra.db.session import get_session_factory
from src.server.infra.services import collection_migration as cm


def _redis_client():
    import os

    import redis  # deferred: clear error on hosts without the redis lib

    return redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))


def _print_report(report: dict) -> None:
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true",
                   help="counts per key family + unresolved names; writes nothing")
    g.add_argument("--apply", action="store_true",
                   help="migrate decks + statuses + instances (idempotent)")
    g.add_argument("--verify", action="store_true",
                   help="reconcile redis vs postgres per key family")
    ap.add_argument("--no-live", action="store_true",
                    help="never fetch Archidekt; every deck uses the Redis fallback")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="seconds to wait between live Archidekt fetches")
    args = ap.parse_args()

    r = _redis_client()
    if args.dry_run:
        report = cm.dry_run(r)
        _print_report(report)
        return 0 if report["ok"] else 1

    factory = get_session_factory()
    if args.apply:
        report = cm.apply(
            r, factory, live=not args.no_live, sleep_seconds=args.sleep,
            progress=lambda msg: print(msg, file=sys.stderr),
        )
        _print_report(report)
        return 0

    report = cm.verify(r, factory)
    _print_report(report)
    if report["ok"]:
        print("reconciliation OK: every redis deck exists in postgres and "
              "instance counts match per (oracle_id, status)")
        return 0
    print("reconciliation FAILED — see families above", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
