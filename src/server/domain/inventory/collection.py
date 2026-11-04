"""Archidekt collection import — turn the remote collection into owned instances.

One physical copy = one instance (instances.py is the ownership source of
truth), so a collection row with quantity 3 becomes 3 `in_collection`
instances of that exact printing.

Two rules make re-import safe to run any time:

1. Idempotent by printing signature. Before creating anything, existing
   instances matching (card_name, set, collector_number, finish, language)
   are counted — including ones already `in_deck`, since a card sleeved in a
   physical deck is still a copy you own and Archidekt counts it in the
   collection. Only the shortfall is created, so importing twice creates
   nothing the second time.
2. Never destructive. Copies present locally but absent remotely are
   reported as `surplus` for the user to look at; nothing is deleted or
   transitioned out, exactly like the physical-deck resync diff
   (docs/PHYSICAL_RESYNC_ADJUDICATION.md).

`not_owned` wishlist placeholders for the same card are promoted to
`in_collection` before new instances are created, mirroring
instances.auto_bind_physical — an imported collection resolves a wishlist
entry rather than leaving it dangling next to a real copy.
"""
import json
import os
from datetime import datetime, timezone
from typing import Optional

import src.server.domain.inventory.instances as instance_store
from src.server.infra.providers import archidekt_collection

COLLECTION_SYNC_KEY = "collection_sync:archidekt"

# Statuses that mean "this copy physically exists (or is on its way)", i.e.
# it should count against the remote collection quantity.
OWNED_STATUSES = ("in_collection", "in_deck", "in_mail")


class CollectionImportError(ValueError):
    """Raised for an unusable collection import request."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def printing_signature(record: dict) -> tuple:
    """Identity of a physical printing, for matching remote rows to local
    instances. Condition is deliberately NOT part of the signature: Archidekt
    condition edits shouldn't fork an existing copy into a second instance.
    """
    return (
        (record.get("card_name") or "").strip(),
        (record.get("set") or "").strip().upper(),
        (record.get("collector_number") or "").strip(),
        record.get("finish") or "nonfoil",
        (record.get("language") or "EN").strip().upper(),
    )


def _index_local_by_signature(r, card_names) -> dict:
    """{signature: [instance, ...]} for every owned instance of the given card
    names. Queried per card name through the instances index rather than
    scanning all instances, so a 10k-card collection stays index-driven."""
    index = {}
    for card_name in card_names:
        for inst in instance_store.list_instances(r, card_name=card_name):
            if inst.get("ownership_status") not in OWNED_STATUSES:
                continue
            index.setdefault(printing_signature(inst), []).append(inst)
    return index


def _promote_placeholder(r, card_name: str) -> Optional[dict]:
    """Promote one `not_owned` placeholder of `card_name` to in_collection,
    returning it, or None when the card has no placeholder."""
    placeholders = instance_store.list_instances(
        r, card_name=card_name, ownership_status="not_owned"
    )
    if not placeholders:
        return None
    return instance_store.transition_status(r, placeholders[0]["id"], "in_collection")


def reconcile(r, rows: list, dry_run: bool = False) -> dict:
    """Bring local instances in line with `rows` (normalized collection rows
    from providers.archidekt_collection.fetch_collection).

    Returns a report:
        {
          "rows": int,                 # remote rows considered
          "remote_copies": int,        # sum of remote quantities
          "created": int,              # new in_collection instances
          "promoted": int,             # not_owned placeholders promoted
          "already_present": int,      # copies that already existed locally
          "surplus": [                 # local-only copies, NOT touched
              {"card_name", "set", "collector_number", "finish",
               "language", "count"}
          ],
          "created_instances": [instance_id, ...],
          "dry_run": bool,
        }

    dry_run=True computes the same report without writing anything — used by
    the API's preview mode so the user can see the diff before committing.
    """
    # Collapse duplicate remote rows for the same printing (Archidekt can
    # split a printing across rows by condition, which the signature ignores).
    wanted = {}
    for row in rows:
        if not row.get("card_name") or row.get("quantity", 0) <= 0:
            continue
        sig = printing_signature(row)
        entry = wanted.setdefault(sig, {"row": row, "quantity": 0})
        entry["quantity"] += row["quantity"]

    card_names = {sig[0] for sig in wanted}
    local = _index_local_by_signature(r, card_names)

    report = {
        "rows": len(rows),
        "remote_copies": sum(e["quantity"] for e in wanted.values()),
        "created": 0,
        "promoted": 0,
        "already_present": 0,
        "surplus": [],
        "created_instances": [],
        "dry_run": dry_run,
    }

    for sig, entry in wanted.items():
        row = entry["row"]
        want = entry["quantity"]
        have = len(local.get(sig, []))
        report["already_present"] += min(have, want)

        shortfall = want - have
        if shortfall <= 0:
            continue

        if dry_run:
            report["created"] += shortfall
            continue

        for _ in range(shortfall):
            promoted = _promote_placeholder(r, row["card_name"])
            if promoted:
                instance_store.update_instance_fields(
                    r,
                    promoted["id"],
                    set=row.get("set", ""),
                    set_name=row.get("set_name", ""),
                    collector_number=row.get("collector_number", ""),
                    finish=row.get("finish", "nonfoil"),
                    condition=row.get("condition", "NM"),
                    language=row.get("language", "EN"),
                    archidekt_uid=row.get("archidekt_uid", ""),
                    date_acquired=row.get("date_acquired", ""),
                    price_paid=row.get("price_paid", 0.0),
                    source="archidekt_collection",
                )
                report["promoted"] += 1
                report["created_instances"].append(promoted["id"])
                continue

            created = instance_store.create_instance(
                r,
                card_name=row["card_name"],
                ownership_status="in_collection",
                set=row.get("set", ""),
                set_name=row.get("set_name", ""),
                collector_number=row.get("collector_number", ""),
                finish=row.get("finish", "nonfoil"),
                condition=row.get("condition", "NM"),
                language=row.get("language", "EN"),
                archidekt_uid=row.get("archidekt_uid", ""),
                date_acquired=row.get("date_acquired", ""),
                price_paid=row.get("price_paid", 0.0),
                source="archidekt_collection",
            )
            report["created"] += 1
            report["created_instances"].append(created["id"])

    # Local-only copies: reported, never removed.
    for sig, insts in local.items():
        want = wanted.get(sig, {}).get("quantity", 0)
        extra = len(insts) - want
        if extra > 0:
            report["surplus"].append({
                "card_name": sig[0],
                "set": sig[1],
                "collector_number": sig[2],
                "finish": sig[3],
                "language": sig[4],
                "count": extra,
            })

    report["surplus"].sort(key=lambda s: (s["card_name"], s["set"], s["collector_number"]))
    return report


def last_sync(r) -> Optional[dict]:
    raw = r.get(COLLECTION_SYNC_KEY)
    return json.loads(raw) if raw else None


def _record_sync(r, username: str, report: dict) -> dict:
    record = {
        "provider": "archidekt",
        "username": username,
        "synced_at": _now(),
        "report": report,
    }
    r.set(COLLECTION_SYNC_KEY, json.dumps(record))
    return record


def import_collection(
    r,
    username: Optional[str] = None,
    dry_run: bool = True,
    fetcher=None,
) -> dict:
    """Fetch the Archidekt collection for `username` and reconcile it into
    instances. Returns {"username", "report", "synced_at"} — the sync record
    is persisted under `collection_sync:archidekt` (skipped on dry runs, which
    are previews and must not look like a completed sync).

    `fetcher` overrides the fetch callable in tests; it receives the username
    and returns normalized rows.
    """
    username = (username or os.getenv("ARCHIDEKT_USERNAME", "")).strip()
    if not username:
        raise CollectionImportError(
            "no Archidekt username given and ARCHIDEKT_USERNAME is not set"
        )

    fetch = fetcher or (lambda u: archidekt_collection.fetch_collection(username=u))
    rows = fetch(username)

    report = reconcile(r, rows, dry_run=dry_run)

    if dry_run:
        return {"username": username, "report": report, "synced_at": None}

    record = _record_sync(r, username, report)
    return {"username": username, "report": report, "synced_at": record["synced_at"]}
