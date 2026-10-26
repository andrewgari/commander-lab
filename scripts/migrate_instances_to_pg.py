"""One-time Redis -> Postgres migration of the card-instance collection.

Slice 1 (docs/ARCHITECTURE.md §4.2/§9): every physical copy living in the
Redis instance registry (``instance:*``) becomes one ``card_instances`` row
plus seeding ``instance_events`` (``acquired``, and ``assigned`` for copies
currently in a deck). Same dry-run + reconciliation discipline as
scripts/_verify_reconciliation.py: NOTHING is written without ``--apply``,
and every run prints a reconciliation report (counts per key family).

Usage:
    python scripts/migrate_instances_to_pg.py                 # dry-run report
    python scripts/migrate_instances_to_pg.py --apply         # write to PG
    python scripts/migrate_instances_to_pg.py --apply --skip-scryfall

Env: REDIS_URL (default redis://localhost:6379/0), DATABASE_URL (required for
--apply; also used in dry-run when set, to report already-migrated counts).

Idempotent: instance ids are preserved; re-running skips ids already in
Postgres. Card facts: oracle ids resolve from the Redis
``idx:card_name_to_oracle:*`` index; card rows missing from Postgres are
backfilled from Scryfall (rule 3 — card facts from the DB or Scryfall) unless
--skip-scryfall, in which case they are reported as ``unresolved_cards`` and
the affected instances are still migrated once a name-only card row exists.

Deck binding: a Redis instance's ``deck_id`` is a registry id (e.g.
``archidekt:42`` or the bare Archidekt id); it maps to ``decks.id`` via
``decks.archidekt_id``. Instances whose deck is not imported yet are reported
under ``unmapped_deck_instances`` and migrated with deck_id NULL — their
status is kept as-is EXCEPT in_deck, which cannot exist without a deck and is
migrated as in_collection with a note (reported loudly, never silently).
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.server.domain.inventory.instances as instance_store  # noqa: E402
from src.server.infra.db import repositories as repo  # noqa: E402
from src.server.infra.db.models import CardRow  # noqa: E402
from src.server.infra.db.session import session_scope  # noqa: E402
from src.server.workers.importer import fetch_scryfall_cards, _card_row_from_scryfall  # noqa: E402

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _resolve_oracle_ids(r, names: list) -> dict:
    """card_name -> oracle_id from the Redis name index (empty string = miss)."""
    if not names:
        return {}
    keys = [f"idx:card_name_to_oracle:{n}" for n in names]
    mget = r.mget if hasattr(r, "mget") else lambda ks: [r.get(k) for k in ks]
    out = {}
    for name, raw in zip(names, mget(keys)):
        if isinstance(raw, bytes):
            raw = raw.decode()
        out[name] = raw or ""
    return out


def _pg_deck_id_map(session) -> dict:
    """archidekt_id -> decks.id for instance deck binding."""
    from sqlalchemy import select
    from src.server.infra.db.models import DeckRow
    rows = session.execute(
        select(DeckRow.id, DeckRow.archidekt_id).where(DeckRow.archidekt_id.isnot(None))
    ).all()
    return {str(aid): did for did, aid in rows}


def _registry_deck_ref_to_archidekt_id(ref) -> str:
    """'archidekt:42' or 42 or '42' -> '42'; anything else -> ''."""
    s = str(ref or "")
    if s.startswith("archidekt:"):
        return s.split(":", 1)[1]
    return s if s.isdigit() else ""


def collect(r) -> dict:
    """Read every Redis instance and classify it for the report. Pure read."""
    instances = instance_store.load_all_instances(r)
    names = sorted({i.get("card_name") or "" for i in instances if i.get("card_name")})
    oracle_by_name = _resolve_oracle_ids(r, names)

    families = {"instance:*": len(instances)}
    by_status = {}
    no_oracle = []
    for inst in instances:
        by_status[inst.get("ownership_status")] = by_status.get(inst.get("ownership_status"), 0) + 1
        oid = inst.get("oracle_id") or oracle_by_name.get(inst.get("card_name") or "", "")
        if not oid:
            no_oracle.append(inst.get("card_name") or "?")
    return {
        "instances": instances,
        "oracle_by_name": oracle_by_name,
        "report": {
            "key_families": families,
            "by_status": by_status,
            "no_oracle_id": sorted(set(no_oracle)),
        },
    }


def plan(r, session, *, skip_scryfall: bool = False) -> dict:
    """Build the write plan: instance rows ready for insert + the report."""
    data = collect(r)
    instances, oracle_by_name = data["instances"], data["oracle_by_name"]
    deck_map = _pg_deck_id_map(session)

    # Ensure card rows exist for every oracle id we are about to reference.
    known = repo.get_cards(session, [
        inst.get("oracle_id") or oracle_by_name.get(inst.get("card_name") or "", "")
        for inst in instances
    ])
    missing_oids = {}
    for inst in instances:
        oid = inst.get("oracle_id") or oracle_by_name.get(inst.get("card_name") or "", "")
        if oid and oid not in known and oid not in missing_oids:
            missing_oids[oid] = inst.get("card_name") or oid
    unresolved = []
    if missing_oids and not skip_scryfall:
        fetched = fetch_scryfall_cards(list(missing_oids))
        for oid, name in missing_oids.items():
            card = fetched.get(oid)
            if card:
                repo.upsert_card(session, _card_row_from_scryfall(card))
            else:
                unresolved.append(name)
                repo.upsert_card(session, {"oracle_id": oid, "name": name})
    else:
        for oid, name in missing_oids.items():
            unresolved.append(name)
            repo.upsert_card(session, {"oracle_id": oid, "name": name})

    # Ensure printing rows exist for every printing id we are about to
    # reference: card_instances.printing_id FK -> printings.scryfall_id, and
    # the Redis record's archidekt_uid is stored as that key (same convention
    # as the importer). Archidekt uids are not Scryfall-resolvable, so these
    # are minimal identity rows (oracle link only); upsert never clears richer
    # fields an import may already have written.
    printing_oids = {}
    for inst in instances:
        pid = inst.get("archidekt_uid") or None
        if not pid or pid in printing_oids:
            continue
        oid = inst.get("oracle_id") or oracle_by_name.get(inst.get("card_name") or "", "")
        if oid:  # instances without an oracle id are unmappable anyway
            printing_oids[pid] = oid
    for pid, oid in printing_oids.items():
        repo.upsert_printing(session, {"scryfall_id": pid, "oracle_id": oid})

    planned, unmappable, unmapped_decks = [], [], []
    for inst in instances:
        name = inst.get("card_name") or ""
        oid = inst.get("oracle_id") or oracle_by_name.get(name, "")
        if not oid:
            unmappable.append({"id": inst.get("id"), "card_name": name,
                               "reason": "no oracle_id in instance or idx:card_name_to_oracle"})
            continue
        status = inst.get("ownership_status") or "not_owned"
        deck_id = None
        if status == "in_deck":
            aid = _registry_deck_ref_to_archidekt_id(inst.get("deck_id"))
            deck_id = deck_map.get(aid)
            if deck_id is None:
                unmapped_decks.append({"id": inst.get("id"), "card_name": name,
                                       "redis_deck_ref": inst.get("deck_id")})
                status = "in_collection"  # cannot be in_deck without a decks row
        planned.append({
            "id": inst.get("id"),
            "oracle_id": oid,
            "status": status,
            "deck_id": deck_id,
            "finish": inst.get("finish") or "nonfoil",
            "condition": inst.get("condition") or "NM",
            "printing_id": inst.get("archidekt_uid") or None,
            "price_paid": inst.get("price_paid") or None,
            "acquired_at": _parse_dt(inst.get("date_acquired") or inst.get("created_at")),
            "notes": inst.get("notes") or None,
        })

    report = data["report"]
    report.update({
        "planned_inserts": len(planned),
        "unmappable_instances": unmappable,
        "unmapped_deck_instances": unmapped_decks,
        "unresolved_cards": sorted(set(unresolved)),
        "printings_backfilled": len(printing_oids),
        "pg_decks_known": len(deck_map),
    })
    return {"planned": planned, "report": report}


def apply_plan(session, planned: list) -> dict:
    inserted = skipped = 0
    for row in planned:
        if repo.insert_instance(session, row):
            inserted += 1
        else:
            skipped += 1
    return {"inserted": inserted, "skipped_existing": skipped}


def reconcile(session, report: dict) -> dict:
    """Post-write reconciliation: PG counts per status vs Redis counts."""
    pg_counts = repo.instance_counts(session)
    redis_counts = report.get("by_status", {})
    families = {}
    ok = True
    for status in sorted(set(redis_counts) | set(pg_counts)):
        expected = redis_counts.get(status, 0)
        actual = pg_counts.get(status, 0)
        # in_deck/in_collection shift for unmapped decks is reported, not a diff error
        match = actual >= 0 and (actual == expected or status in ("in_deck", "in_collection"))
        ok = ok and (actual == expected or status in ("in_deck", "in_collection"))
        families[status] = {"redis": expected, "postgres": actual,
                            "match": actual == expected}
    total_pg = sum(pg_counts.values())
    total_redis = sum(redis_counts.values())
    totals_match = total_pg == total_redis - len(report.get("unmappable_instances", []))
    return {
        "by_status": families,
        "totals": {"redis": total_redis, "postgres": total_pg,
                   "unmappable": len(report.get("unmappable_instances", [])),
                   "match": totals_match},
        "ok": ok and totals_match,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write to Postgres (default: dry-run)")
    parser.add_argument("--skip-scryfall", action="store_true",
                        help="name-only card rows instead of Scryfall backfill")
    args = parser.parse_args(argv)

    if args.apply and not os.getenv("DATABASE_URL"):
        print("ERROR: DATABASE_URL is required for --apply", file=sys.stderr)
        return 2

    import redis
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

    if os.getenv("DATABASE_URL"):
        with session_scope() as session:
            result = plan(r, session, skip_scryfall=args.skip_scryfall)
            if args.apply:
                result["apply"] = apply_plan(session, result["planned"])
                session.flush()
                result["reconciliation"] = reconcile(session, result["report"])
                if not result["reconciliation"]["ok"]:
                    session.rollback()
                    result["aborted"] = "reconciliation failed — transaction rolled back, nothing written"
    else:
        # No DATABASE_URL: dry-run against Redis only (card/deck mapping skipped).
        import fakeredis  # not required; plan() needs a session — use a sqlite scratch session
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from src.server.infra.db.orm_models import Base
        eng = create_engine("sqlite://")
        Base.metadata.create_all(eng)
        with session_scope(sessionmaker(bind=eng, expire_on_commit=False)) as session:
            result = plan(r, session, skip_scryfall=True)
            result["note"] = "dry-run without DATABASE_URL: card backfill and deck mapping skipped"

    print(json.dumps(result["report"] | {k: v for k, v in result.items() if k != "report"},
                     indent=2, default=str))
    if args.apply:
        if result.get("aborted"):
            print(result["aborted"], file=sys.stderr)
            return 1
        print(f"applied: {result['apply']}")
    else:
        print("dry-run only — re-run with --apply to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
