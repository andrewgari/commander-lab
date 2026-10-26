"""Full collection migration: Redis -> Postgres (ARCHITECTURE.md §8 Slice 2,
§9 migration rehearsal discipline).

One-time scripted job covering every collection key family:

- ``decks`` (registry array) — each deck is imported through the Slice 1
  Archidekt one-way path (full fidelity: boards, categories, printings, card
  reference rows). Decks whose live fetch fails — deleted/private upstream,
  non-Archidekt sources, offline runs — fall back to the Redis-cached
  decklist (``domain.decks.legacy_import``), so no deck is ever dropped.
- ``deck_status:{id}`` — lifecycle status is read from Redis (standalone
  ``deck_status:*`` key wins over the array entry) and mapped onto the §4.2
  enum (``legacy_import.map_legacy_status``); ``retired`` decks are archived.
- ``instance:*`` — every physical copy lands in ``card_instances`` exactly
  once (idempotent by instance id), with provenance events seeded from the
  current Redis state: ``acquired`` (at ``date_acquired``), ``assigned``
  (current deck binding), and the ``migrated`` marker. A final rebind pass
  attaches instances whose deck was imported after an earlier run.

Reports give counts per key family (§9): ``--dry-run`` counts and resolves
everything but writes nothing; ``--verify`` reconciles Redis vs Postgres and
fails on any instance-count mismatch or missing deck.

The redis client is injected (anything with ``get``/``scan_iter``), so tests
run against an in-memory stub.
"""
from __future__ import annotations

import json
import logging
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy.orm import sessionmaker

from src.server.domain.decks import legacy_import
from src.server.domain.inventory import have_buy as have_buy_mod
from src.server.domain.profile import power as power_mod
from src.server.domain.profile import snapshot as snapshot_mod
from src.server.infra.db import repositories as repo
from src.server.infra.db.session import session_scope
from src.server.infra.providers import archidekt as archidekt_provider
from src.server.infra.services import import_service

log = logging.getLogger("commander_lab.collection_migration")


# ---------------------------------------------------------------------------
# Redis reads
# ---------------------------------------------------------------------------

def _decode(value):
    return value.decode("utf-8") if isinstance(value, bytes) else value


def _parse_ts(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def load_redis_decks(r) -> dict:
    """The ``decks`` array + ``deck_status:*`` overrides, with family counts."""
    raw = _decode(r.get("decks"))
    decks = json.loads(raw) if raw else []
    status_overrides: dict[str, str] = {}
    for key in r.scan_iter("deck_status:*"):
        value = _decode(r.get(key))
        if value:
            status_overrides[str(key).split(":", 1)[1]] = value
    return {
        "decks": decks if isinstance(decks, list) else [],
        "status_overrides": status_overrides,
    }


def load_redis_instances(r) -> tuple[list[tuple[dict, str]], dict]:
    """All ``instance:*`` records resolved to oracle ids, plus resolution stats.

    Same resolution discipline as Slice 1 (scripts/migrate_instances_to_pg.py):
    the record's own oracle_id, else ``idx:card_name_to_oracle:{name}``, else
    the legacy ``card:{name}`` blob.
    """
    records = []
    for key in r.scan_iter("instance:*"):
        raw = r.get(key)
        if not raw:
            continue
        try:
            records.append(json.loads(_decode(raw)))
        except json.JSONDecodeError:
            records.append({"id": str(key).split(":", 1)[1], "_unparseable": True})

    unresolved: list[str] = []
    mapped = 0
    name_cache: dict[str, Optional[str]] = {}

    def oracle_for(name: str) -> Optional[str]:
        if name not in name_cache:
            oid = r.get(f"idx:card_name_to_oracle:{name}")
            if oid is None and r.exists(f"card:{name}"):
                card = r.get(f"card:{name}")
                oid = json.loads(_decode(card)).get("oracle_id") if card else None
            name_cache[name] = _decode(oid) if oid else None
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


def make_name_resolver(r) -> Callable[[str], Optional[str]]:
    """Name -> oracle_id resolver over the Redis indexes (for the decklist
    fallback path)."""
    cache: dict[str, Optional[str]] = {}

    def resolve(name: str) -> Optional[str]:
        if name not in cache:
            oid = r.get(f"idx:card_name_to_oracle:{name}")
            if oid is None and r.exists(f"card:{name}"):
                card = r.get(f"card:{name}")
                oid = json.loads(_decode(card)).get("oracle_id") if card else None
            cache[name] = _decode(oid) if oid else None
        return cache[name]

    return resolve


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

def dry_run(r) -> dict:
    """Counts per key family + resolution coverage. Writes nothing, fetches
    nothing (live-fetch failures are only knowable at apply time)."""
    decks = load_redis_decks(r)
    pairs, stats = load_redis_instances(r)
    by_status = Counter(rec.get("ownership_status", "?") for rec, _ in pairs)
    planned = Counter(
        "archidekt_live" if d.get("source") == "archidekt" and d.get("source_id") is not None
        else "redis_fallback"
        for d in decks["decks"]
    )
    return {
        "families": {
            "decks": {
                "redis": len(decks["decks"]),
                "planned_method": dict(planned),
                "status_values": dict(Counter(
                    (d.get("status") or "?") for d in decks["decks"])),
            },
            "deck_status": {
                "redis_keys": len(decks["status_overrides"]),
                "values": dict(Counter(decks["status_overrides"].values())),
            },
            "instances": {
                "redis_records": stats["redis_records"],
                "resolvable": stats["resolvable"],
                "unresolved": stats["unresolved"],
                "by_ownership_status": dict(by_status),
            },
        },
        "ok": not stats["unresolved"],
    }


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def _import_deck_live(identifier: str, factory: sessionmaker,
                      fetcher: Optional[Callable] = None) -> dict:
    return import_service.import_archidekt_deck(
        identifier, session_factory=factory, fetcher=fetcher
    )


def _import_deck_fallback(entry: dict, oracle_for, factory: sessionmaker) -> dict:
    """Redis-cached decklist -> decks/deck_cards (+ card stubs), with the same
    snapshot treatment as a live import."""
    normalized = legacy_import.normalize_redis_deck(entry, oracle_for)
    with session_scope(factory) as session:
        known = repo.known_oracle_ids(session, [c["oracle_id"] for c in normalized["cards"]])
        for card in normalized["cards"]:
            if card["oracle_id"] not in known:
                repo.upsert_card(session, {"oracle_id": card["oracle_id"], "name": card["name"]})
                known.add(card["oracle_id"])
        existing = repo.get_deck(session, normalized["id"])
        created = existing is None
        changed = created or existing.get("fingerprint") != normalized["fingerprint"]
        repo.upsert_deck(session, normalized)
        rows = repo.replace_deck_cards(session, normalized["id"], normalized["cards"])
        for instance_id, deck_id in repo.rebind_instances(session, {normalized["id"]}):
            repo.insert_event(session, {
                "instance_id": instance_id, "kind": "assigned", "to_deck": deck_id,
            })
        snapshot_id = None
        if changed:
            deck_cards = repo.get_deck_cards(session, normalized["id"])
            instances = repo.list_instances(
                session, oracle_ids={c["oracle_id"] for c in deck_cards})
            report = have_buy_mod.compute_have_buy(deck_cards, instances, normalized["id"])
            power = power_mod.estimate_power(deck_cards)
            snap = snapshot_mod.build_snapshot(
                normalized, deck_cards, report, repo.collection_stats(session), power=power)
            snapshot_id = repo.append_snapshot(
                session, "import", snap["inputs"], snap["metrics"], snap["summary_text"])
    return {
        "deck_id": normalized["id"], "rows": rows, "created": created,
        "changed": changed, "snapshot_id": snapshot_id,
        "unresolved": normalized["unresolved"],
        "parse_flags": normalized["parse_flags"],
    }


def _redis_status_for(entry: dict, overrides: dict) -> str:
    """deck_status:{registry_id} or deck_status:{raw id} wins over the array."""
    rid = str(entry.get("registry_id") or "")
    raw = str(entry.get("id") or "")
    return overrides.get(rid) or overrides.get(raw) or entry.get("status") or ""


def apply(r, factory: sessionmaker, fetcher: Optional[Callable] = None,
          live: bool = True, sleep_seconds: float = 0.0,
          progress: Optional[Callable[[str], None]] = None) -> dict:
    """Migrate the full collection. Idempotent: re-running after a partial run
    inserts nothing twice (instances keyed by id, decks by registry id,
    snapshots only on content change)."""
    note = progress or (lambda msg: None)
    data = load_redis_decks(r)
    decks_report = []
    status_changes = Counter()
    unresolved_names: list[str] = []

    # -- phase 1: decks ------------------------------------------------------
    for i, entry in enumerate(data["decks"], 1):
        name = entry.get("name") or entry.get("id")
        method, result, error = "redis_fallback", None, None
        source_id = entry.get("source_id")
        if live and entry.get("source") == "archidekt" and source_id is not None:
            try:
                result = _import_deck_live(str(source_id), factory, fetcher)
                method = "archidekt_live"
                if sleep_seconds:
                    time.sleep(sleep_seconds)
            except Exception as e:  # upstream gone/private/rate-limited -> fallback
                error = f"{type(e).__name__}: {e}"
                result = None
        if result is None:
            result = _import_deck_fallback(entry, make_name_resolver(r), factory)
            if error:
                result.setdefault("parse_flags", []).append(f"live_fetch_failed:{error}")
        unresolved_names.extend(result.get("unresolved") or [])
        decks_report.append({
            "deck_id": result["deck_id"], "name": name, "method": method,
            "changed": result.get("changed"), "unresolved": len(result.get("unresolved") or []),
            "parse_flags": result.get("parse_flags") or [],
        })
        note(f"[{i}/{len(data['decks'])}] {name}: {method}")

        # -- lifecycle status from Redis (wins over the folder rule) ---------
        redis_status = _redis_status_for(entry, data["status_overrides"])
        new_status, archived, flag = legacy_import.map_legacy_status(redis_status)
        if flag:
            decks_report[-1]["parse_flags"].append(flag)
        with session_scope(factory) as session:
            if repo.update_deck_status(session, result["deck_id"], new_status, archived):
                status_changes["archived" if archived else new_status] += 1

    # -- phase 2: instances (+ provenance events seeded from Redis state) ----
    pairs, stats = load_redis_instances(r)
    if stats["unresolved"]:
        raise RuntimeError(
            f"refusing to migrate instances: {len(stats['unresolved'])} unresolved "
            f"card names (run --dry-run); fix idx:card_name_to_oracle first"
        )
    inserted = skipped = 0
    with session_scope(factory) as session:
        existing_deck_ids = {d["id"] for d in repo.list_decks(session, include_archived=True)}
        known = repo.known_oracle_ids(session, [oid for _, oid in pairs])
        for rec, oid in pairs:
            if oid not in known:
                repo.upsert_card(session, {"oracle_id": oid, "name": rec.get("card_name", "")})
                known.add(oid)
        now = datetime.now(timezone.utc)
        for rec, oid in pairs:
            legacy_ref = rec.get("deck_id")
            deck_id = None
            if legacy_ref is not None:
                candidate = f"archidekt:{legacy_ref}"
                if candidate in existing_deck_ids:
                    deck_id = candidate
            inst = {
                "id": str(rec["id"]),
                "oracle_id": oid,
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
            if not repo.insert_instance(session, inst):
                skipped += 1
                continue
            inserted += 1
            # Provenance seeded from current state: when the copy was acquired,
            # where it currently lives, and the migration marker itself.
            if inst["acquired_at"] is not None:
                repo.insert_event(session, {
                    "instance_id": inst["id"], "ts": inst["acquired_at"],
                    "kind": "acquired",
                })
            if deck_id is not None:
                repo.insert_event(session, {
                    "instance_id": inst["id"], "ts": now,
                    "kind": "assigned", "to_deck": deck_id,
                })
            repo.insert_event(session, {
                "instance_id": inst["id"],
                "ts": _parse_ts(rec.get("created_at", "")) or now,
                "kind": "migrated", "to_deck": deck_id,
            })
        rebound = repo.rebind_instances(session, existing_deck_ids)
        for instance_id, deck_id in rebound:
            repo.insert_event(session, {
                "instance_id": instance_id, "kind": "assigned", "to_deck": deck_id,
            })

    return {
        "decks": decks_report,
        "deck_methods": dict(Counter(d["method"] for d in decks_report)),
        "status_changes": dict(status_changes),
        "unresolved_deck_card_names": unresolved_names,
        "instances": {
            "redis_records": stats["redis_records"],
            "inserted": inserted,
            "already_present": skipped,
            "rebound": len(rebound),
        },
    }


# ---------------------------------------------------------------------------
# Verify (reconciliation report, §9)
# ---------------------------------------------------------------------------

def verify(r, factory: sessionmaker) -> dict:
    """Redis vs Postgres, counts per key family. ``ok`` gates on the Slice 2
    acceptance: every resolvable Redis instance exists exactly once in
    card_instances (per (oracle_id, status)) and every Redis deck exists in
    Postgres. deck_cards quantities are reported per deck as information —
    live imports track Archidekt, which may legitimately differ from the
    cached Redis decklist."""
    data = load_redis_decks(r)
    pairs, stats = load_redis_instances(r)
    expected_instances = Counter(
        (oid, rec.get("ownership_status", "not_owned")) for rec, oid in pairs)

    with session_scope(factory) as session:
        pg_decks = {d["id"]: d for d in repo.list_decks(session, include_archived=True)}
        actual_instances = Counter(repo.instance_counts_by_oracle(session))
        event_counts = dict(Counter(repo.event_counts_by_kind(session)))
        pg_deck_qty = repo.deck_card_counts(session, list(pg_decks))

    wanted_ids = set()
    for entry in data["decks"]:
        rid = entry.get("registry_id") or (
            f"archidekt:{entry['source_id']}"
            if entry.get("source") == "archidekt" and entry.get("source_id") is not None
            else f"manual:{entry.get('id')}")
        wanted_ids.add(str(rid))
    missing_decks = sorted(wanted_ids - set(pg_decks))

    status_mismatches = []
    for entry in data["decks"]:
        rid = str(entry.get("registry_id") or f"archidekt:{entry.get('source_id')}")
        deck = pg_decks.get(rid)
        if deck is None:
            continue
        redis_status = _redis_status_for(entry, data["status_overrides"])
        new_status, archived, _flag = legacy_import.map_legacy_status(redis_status)
        if deck["status"] != new_status or (archived and deck["archived_at"] is None):
            status_mismatches.append({
                "deck_id": rid, "expected": new_status, "actual": deck["status"],
                "archived_expected": archived,
                "archived_actual": deck["archived_at"] is not None,
            })

    instance_keys = set(expected_instances) | set(actual_instances)
    instance_mismatches = [
        {"oracle_id": oid, "status": status,
         "redis": expected_instances.get((oid, status), 0),
         "postgres": actual_instances.get((oid, status), 0)}
        for oid, status in sorted(instance_keys)
        if expected_instances.get((oid, status), 0) != actual_instances.get((oid, status), 0)
    ]

    redis_qty = {
        str(e.get("registry_id") or f"archidekt:{e.get('source_id')}"): sum(
            int(c.get("quantity") or 1) for c in (e.get("cards") or []))
        for e in data["decks"]
    }
    deck_qty_diffs = [
        {"deck_id": rid, "redis_qty": qty, "postgres_qty": pg_deck_qty.get(rid, 0)}
        for rid, qty in sorted(redis_qty.items())
        if rid in pg_decks and pg_deck_qty.get(rid, 0) != qty
    ]

    report = {
        "families": {
            "decks": {
                "redis": len(data["decks"]),
                "postgres": len([did for did in pg_decks if did in wanted_ids]),
                "missing_in_postgres": missing_decks,
                "status_mismatches": status_mismatches,
            },
            "deck_status": {"redis_keys": len(data["status_overrides"])},
            "deck_cards": {
                "postgres_qty_total": sum(pg_deck_qty.get(rid, 0) for rid in wanted_ids),
                "redis_qty_total": sum(redis_qty.values()),
                "per_deck_diffs": deck_qty_diffs,
                "note": "informational: live imports track Archidekt, not the cached decklist",
            },
            "instances": {
                "redis_resolvable": sum(expected_instances.values()),
                "postgres": sum(actual_instances.values()),
                "unresolved_redis_records": len(stats["unresolved"]),
                "mismatches": instance_mismatches,
            },
            "instance_events": {"postgres_by_kind": event_counts},
        },
        "ok": not missing_decks and not status_mismatches and not instance_mismatches,
    }
    return report
