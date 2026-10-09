"""
Card instance registry — the ownership source of truth for Commander Lab.

Every physical (or in-transit) copy of a card is a unique `instance` record,
never a bare quantity counter. Oracle-level card data (name, type, oracle text)
lives separately in `card_meta:{card_name}`, refreshed by sync.py from
Archidekt/Scryfall. See docs/CARD_DATABASE.md for the full design rationale.

All reads/writes to `instance:*` and its secondary indexes should go through
this module so the indexes never drift from the underlying records.
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Union

DeckId = Union[int, str]

OWNERSHIP_STATUSES = {"not_owned", "in_mail", "in_collection", "in_deck"}
FINISHES = {"nonfoil", "foil", "etched"}
CONDITIONS = {"NM", "LP", "MP", "HP", "DMG"}

# Allowed ownership_status transitions: from -> set of valid destinations
ALLOWED_TRANSITIONS = {
    "not_owned": {"in_mail", "in_collection"},
    "in_mail": {"in_collection", "not_owned"},
    "in_collection": {"in_deck", "not_owned"},
    "in_deck": {"in_collection", "not_owned"},
}


class InstanceError(ValueError):
    """Raised for invalid instance data or illegal state transitions."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _instance_key(instance_id: str) -> str:
    return f"instance:{instance_id}"


def _idx_card_key(card_name: str) -> str:
    return f"idx:instances_by_card:{card_name}"


def _idx_status_key(status: str) -> str:
    return f"idx:instances_by_status:{status}"


def _idx_deck_key(deck_id: "DeckId") -> str:
    return f"idx:instances_by_deck:{deck_id}"


def _idx_set_key(set_code: str) -> str:
    return f"idx:instances_by_set:{set_code}"


def _idx_pending_removal_key(registry_id: "DeckId") -> str:
    """Index key for instances flagged with pending_removal for a given deck."""
    return f"idx:instances_pending_removal:{registry_id}"


def create_instance(
    r,
    card_name: str,
    ownership_status: str = "not_owned",
    set: str = "",
    set_name: str = "",
    collector_number: str = "",
    finish: str = "nonfoil",
    condition: str = "NM",
    language: str = "EN",
    deck_id: Optional[DeckId] = None,
    deck_name: str = "",
    considered_for_deck: Optional[str] = None,
    price_paid: float = 0.0,
    source: str = "",
    date_ordered: str = "",
    date_acquired: str = "",
    archidekt_uid: str = "",
    notes: str = "",
) -> dict:
    """Create and persist a new unique card instance. Returns the stored record."""
    if not card_name:
        raise InstanceError("card_name is required")
    if ownership_status not in OWNERSHIP_STATUSES:
        raise InstanceError(f"invalid ownership_status: {ownership_status}")
    if finish not in FINISHES:
        raise InstanceError(f"invalid finish: {finish}")
    if condition not in CONDITIONS:
        raise InstanceError(f"invalid condition: {condition}")
    if ownership_status == "in_deck" and not deck_id:
        raise InstanceError("deck_id is required when ownership_status is in_deck")
    if ownership_status != "in_deck" and deck_id:
        raise InstanceError("deck_id must be null unless ownership_status is in_deck")

    instance_id = str(uuid.uuid4())
    now = _now()
    record = {
        "id": instance_id,
        "card_name": card_name,
        "set": set,
        "set_name": set_name,
        "collector_number": collector_number,
        "finish": finish,
        "condition": condition,
        "language": language,
        "ownership_status": ownership_status,
        "deck_id": deck_id,
        "deck_name": deck_name if deck_id else "",
        "considered_for_deck": considered_for_deck,
        "price_paid": price_paid,
        "source": source,
        "date_ordered": date_ordered,
        "date_acquired": date_acquired,
        "archidekt_uid": archidekt_uid,
        "notes": notes,
        "created_at": now,
        "updated_at": now,
        "history": [{"status": ownership_status, "at": now}],
    }

    pipe = r.pipeline()
    pipe.set(_instance_key(instance_id), json.dumps(record))
    pipe.sadd(_idx_card_key(card_name), instance_id)
    pipe.sadd(_idx_status_key(ownership_status), instance_id)
    if deck_id:
        pipe.sadd(_idx_deck_key(deck_id), instance_id)
    if set:
        pipe.sadd(_idx_set_key(set), instance_id)
    pipe.execute()

    return record


def get_instance(r, instance_id: str) -> Optional[dict]:
    val = r.get(_instance_key(instance_id))
    return json.loads(val) if val else None


def _save_instance(r, record: dict):
    r.set(_instance_key(record["id"]), json.dumps(record))


def transition_status(
    r,
    instance_id: str,
    new_status: str,
    deck_id: Optional[DeckId] = None,
    deck_name: str = "",
    _allow_physical_lock_bypass: bool = False,
) -> dict:
    """Move an instance to a new ownership_status, validating the transition
    and keeping deck_id / secondary indexes consistent.

    An instance currently bound to a deck whose registry status is
    "physical" cannot be moved out of in_deck (or reassigned to a different
    deck) through this normal path — that deck's cardlist is the physical
    inventory record and edits must go through the explicit deck-unlock flow
    in registry.py, not ad-hoc instance moves. `_allow_physical_lock_bypass`
    is set only by that flow.
    """
    record = get_instance(r, instance_id)
    if not record:
        raise InstanceError(f"instance not found: {instance_id}")

    current = record["ownership_status"]
    if new_status not in OWNERSHIP_STATUSES:
        raise InstanceError(f"invalid ownership_status: {new_status}")
    if new_status != current and new_status not in ALLOWED_TRANSITIONS.get(current, set()):
        raise InstanceError(f"illegal transition: {current} -> {new_status}")
    if new_status == "in_deck" and not deck_id:
        raise InstanceError("deck_id is required when transitioning to in_deck")

    if not _allow_physical_lock_bypass and current == "in_deck" and record.get("deck_id"):
        import registry  # local import: avoid a circular import at module load time

        moving_out = new_status != current
        reassigning_deck = new_status == "in_deck" and deck_id is not None and deck_id != record.get("deck_id")
        if registry.is_deck_physical(r, str(record["deck_id"])) and (moving_out or reassigning_deck):
            raise InstanceError(
                f"instance is bound to physical deck {record.get('deck_name')!r}; "
                "unassign it via the deck's unlock flow, not a direct instance transition"
            )

    old_deck_id = record.get("deck_id")

    pipe = r.pipeline()

    # Update status index membership
    if new_status != current:
        pipe.srem(_idx_status_key(current), instance_id)
        pipe.sadd(_idx_status_key(new_status), instance_id)

    # Update deck index membership: clear old deck link unless staying in_deck
    if new_status != "in_deck":
        if old_deck_id:
            pipe.srem(_idx_deck_key(old_deck_id), instance_id)
        record["deck_id"] = None
        record["deck_name"] = ""
    else:
        if old_deck_id and old_deck_id != deck_id:
            pipe.srem(_idx_deck_key(old_deck_id), instance_id)
        pipe.sadd(_idx_deck_key(deck_id), instance_id)
        record["deck_id"] = deck_id
        record["deck_name"] = deck_name

    now = _now()
    record["ownership_status"] = new_status
    record["updated_at"] = now
    record.setdefault("history", []).append({"status": new_status, "at": now})

    pipe.set(_instance_key(instance_id), json.dumps(record))
    pipe.execute()

    return record


def update_instance_fields(r, instance_id: str, **fields) -> dict:
    """Update non-status fields on an instance (set, condition, notes, price, etc).
    Use transition_status for ownership_status/deck_id changes."""
    if "ownership_status" in fields or "deck_id" in fields:
        raise InstanceError("use transition_status to change ownership_status/deck_id")

    record = get_instance(r, instance_id)
    if not record:
        raise InstanceError(f"instance not found: {instance_id}")

    if "finish" in fields and fields["finish"] not in FINISHES:
        raise InstanceError(f"invalid finish: {fields['finish']}")
    if "condition" in fields and fields["condition"] not in CONDITIONS:
        raise InstanceError(f"invalid condition: {fields['condition']}")

    old_set = record.get("set", "")
    editable = {
        "set", "set_name", "collector_number", "finish", "condition", "language",
        "considered_for_deck", "price_paid", "source", "date_ordered",
        "date_acquired", "archidekt_uid", "notes",
    }
    for key, value in fields.items():
        if key in editable:
            record[key] = value

    record["updated_at"] = _now()

    pipe = r.pipeline()
    new_set = record.get("set", "")
    if new_set != old_set:
        if old_set:
            pipe.srem(_idx_set_key(old_set), instance_id)
        if new_set:
            pipe.sadd(_idx_set_key(new_set), instance_id)
    pipe.set(_instance_key(instance_id), json.dumps(record))
    pipe.execute()

    return record


def delete_instance(r, instance_id: str) -> bool:
    record = get_instance(r, instance_id)
    if not record:
        return False

    pipe = r.pipeline()
    pipe.delete(_instance_key(instance_id))
    pipe.srem(_idx_card_key(record["card_name"]), instance_id)
    pipe.srem(_idx_status_key(record["ownership_status"]), instance_id)
    if record.get("deck_id"):
        pipe.srem(_idx_deck_key(record["deck_id"]), instance_id)
    if record.get("set"):
        pipe.srem(_idx_set_key(record["set"]), instance_id)
    pending = record.get("pending_removal")
    if pending:
        reg = pending.get("deck_registry_id") or pending.get("registry_id")
        if reg:
            pipe.srem(_idx_pending_removal_key(str(reg)), instance_id)
    pipe.execute()
    return True


def list_instances(
    r,
    card_name: Optional[str] = None,
    ownership_status: Optional[str] = None,
    deck_id: Optional[DeckId] = None,
    set: Optional[str] = None,
    q: Optional[str] = None,
) -> list:
    """List instances, optionally intersecting filters via the secondary indexes
    instead of scanning all instance keys.

    `q` is a free-text search applied after index lookup: a case-insensitive
    substring match against card_name, set_name, deck_name, and notes — for the
    inventory UI's search box, distinct from the exact-match `card_name` filter.
    """
    index_sets = []
    if card_name:
        index_sets.append(_idx_card_key(card_name))
    if ownership_status:
        index_sets.append(_idx_status_key(ownership_status))
    if deck_id:
        index_sets.append(_idx_deck_key(deck_id))
    if set:
        index_sets.append(_idx_set_key(set))

    if index_sets:
        ids = r.sinter(*index_sets) if len(index_sets) > 1 else r.smembers(index_sets[0])
    else:
        ids = [k.split(":", 1)[1] for k in r.keys("instance:*")]

    instances = []
    for instance_id in ids:
        record = get_instance(r, instance_id)
        if record:
            instances.append(record)

    if q:
        needle = q.strip().lower()
        if needle:
            def _matches(rec: dict) -> bool:
                haystacks = (
                    rec.get("card_name") or "",
                    rec.get("set_name") or "",
                    rec.get("deck_name") or "",
                    rec.get("notes") or "",
                )
                return any(needle in str(h).lower() for h in haystacks)

            instances = [rec for rec in instances if _matches(rec)]

    instances.sort(key=lambda x: (x["card_name"], x.get("set", ""), x.get("created_at", "")))
    return instances


def list_deck_instances(r, deck_ids, ownership_status: Optional[str] = None) -> list:
    """Instances bound to any of `deck_ids` (see registry.deck_instance_ids:
    one deck can be indexed under both its raw id and its registry_id).
    Deduplicated by instance id, sorted like list_instances."""
    seen = {}
    for did in deck_ids:
        for rec in list_instances(r, deck_id=str(did), ownership_status=ownership_status):
            seen.setdefault(rec["id"], rec)
    out = list(seen.values())
    out.sort(key=lambda x: (x["card_name"], x.get("set", ""), x.get("created_at", "")))
    return out


def load_all_instances(r) -> list:
    """Every instance record, fetched in one MGET round trip."""
    keys = list(r.keys("instance:*"))
    if not keys:
        return []
    if hasattr(r, "mget"):
        values = r.mget(keys)
    else:  # minimal test fakes
        values = [r.get(k) for k in keys]
    return [json.loads(v) for v in values if v]


def set_pending_removal(
    r,
    instance_id: str,
    registry_id: str,
    reason: str = "resync_removed",
) -> Optional[dict]:
    """Set the pending_removal marker on an instance and update the secondary index.

    Args:
        r: Redis connection
        instance_id: ID of the instance to flag
        registry_id: Deck registry ID that triggered the flag
        reason: Why the instance was flagged (default: 'resync_removed')

    Returns:
        The updated instance record, or None if instance doesn't exist.
        Does NOT re-timestamp if already flagged with same registry_id and reason.
    """
    record = get_instance(r, instance_id)
    if not record:
        return None

    existing = record.get("pending_removal")
    existing_reg = existing.get("deck_registry_id") or existing.get("registry_id") if existing else None
    if (
        existing
        and existing_reg == str(registry_id)
        and existing.get("reason") == reason
    ):
        # Already flagged for this exact case — idempotent, don't re-timestamp
        return record

    pipe = r.pipeline()
    if existing_reg:
        pipe.srem(_idx_pending_removal_key(str(existing_reg)), instance_id)

    now = _now()
    record["pending_removal"] = {
        "reason": reason,
        "detected_at": now,
        "deck_registry_id": str(registry_id),
        "registry_id": str(registry_id),
    }
    record["updated_at"] = now

    pipe.sadd(_idx_pending_removal_key(str(registry_id)), instance_id)
    pipe.set(_instance_key(instance_id), json.dumps(record))
    pipe.execute()

    return record


def clear_pending_removal(r, instance_id: str) -> Optional[dict]:
    """Clear the pending_removal marker from an instance (called when the
    user adjudicates a resync-flagged card via keep/toss). Returns the
    updated instance record, or None if the instance doesn't exist.
    """
    record = get_instance(r, instance_id)
    if not record:
        return None

    existing = record.get("pending_removal")
    if not existing:
        return record

    pipe = r.pipeline()
    reg = existing.get("deck_registry_id") or existing.get("registry_id")
    if reg:
        pipe.srem(_idx_pending_removal_key(str(reg)), instance_id)

    del record["pending_removal"]
    record["updated_at"] = _now()
    pipe.set(_instance_key(instance_id), json.dumps(record))
    pipe.execute()
    return record


def flag_resync_removed(r, registry_id: str, current_decklist_card_names: set) -> list:
    """Flag instances bound (in_deck) to `registry_id` whose card_name is
    NOT in `current_decklist_card_names` (the new remote decklist) with a
    `pending_removal` marker. This is the physical-deck resync diff:
    ownership_status and deck_id are NOT touched — the instance stays fully
    bound until the human adjudicates.

    Idempotent: if an instance already has a pending_removal marker for this
    same deck_registry_id and reason, we don't update the timestamp.

    Returns the list of instance records that were newly flagged (or already
    flagged for this registry_id).
    """
    flagged = []
    now = _now()
    bound_instances = list_instances(r, deck_id=registry_id, ownership_status="in_deck")

    for inst in bound_instances:
        card_name = inst.get("card_name")
        if card_name in current_decklist_card_names:
            # Still on the remote decklist — nothing to flag
            continue

        existing_marker = inst.get("pending_removal")
        existing_reg = existing_marker.get("deck_registry_id") or existing_marker.get("registry_id") if existing_marker else None
        if existing_marker and existing_reg == str(registry_id) and existing_marker.get("reason") == "resync_removed":
            # Already flagged for this exact resync — don't re-timestamp
            flagged.append(inst)
            continue

        # Set new pending_removal marker
        inst["pending_removal"] = {
            "reason": "resync_removed",
            "detected_at": now,
            "deck_registry_id": str(registry_id),
            "registry_id": str(registry_id),
        }
        inst["updated_at"] = now
        pipe = r.pipeline()
        if existing_reg:
            pipe.srem(_idx_pending_removal_key(str(existing_reg)), inst["id"])
        pipe.sadd(_idx_pending_removal_key(str(registry_id)), inst["id"])
        pipe.set(_instance_key(inst["id"]), json.dumps(inst))
        pipe.execute()
        flagged.append(inst)

    return flagged


def list_pending_removal(r, registry_id: str) -> list:
    """List all instances flagged with pending_removal for a given deck.

    Returns list of instance records sorted by card_name.
    """
    ids = r.smembers(_idx_pending_removal_key(str(registry_id)))
    instances = []
    seen = set()
    for instance_id in ids:
        record = get_instance(r, instance_id)
        if record and record.get("pending_removal"):
            instances.append(record)
            seen.add(record["id"])

    # Also check bound instances in case any were flagged without the index
    bound = list_instances(r, deck_id=registry_id)
    for inst in bound:
        if inst.get("pending_removal") and inst["id"] not in seen:
            instances.append(inst)
            seen.add(inst["id"])

    instances.sort(key=lambda x: x.get("card_name", ""))
    return instances


def resolve_resync_removal(
    r,
    instance_id: str,
    action: str,
    deck_id: Optional[Union[int, str]] = None,
) -> Optional[dict]:
    """Resolve a pending_removal instance via 'keep'/'save' or 'toss'.

    Args:
        r: Redis connection
        instance_id: ID of the instance to resolve
        action: 'keep' / 'save' (save to inventory, transitions to in_collection)
                or 'toss' (mark not_owned)
        deck_id: Optional deck identifier or registry ID to validate against.
                 If provided, ensures the instance is either bound to this deck
                 or was flagged for removal by this deck.

    Returns:
        The updated instance record.

    Raises:
        InstanceError: If instance not found, has no pending_removal marker,
                       deck_id mismatch, or action is invalid.
    """
    record = get_instance(r, instance_id)
    if not record:
        raise InstanceError(f"instance not found: {instance_id}")

    pending = record.get("pending_removal")
    if not pending:
        raise InstanceError(f"instance {instance_id} has no pending_removal marker")

    if deck_id is not None:
        target_str = str(deck_id)
        inst_deck = str(record.get("deck_id")) if record.get("deck_id") else None
        pending_reg = str(pending.get("deck_registry_id") or pending.get("registry_id")) if pending else None
        if inst_deck != target_str and pending_reg != target_str:
            raise InstanceError(f"instance {instance_id} does not belong to deck {deck_id}")

    norm_action = action.lower().strip()
    if norm_action in ("keep", "save"):
        new_status = "in_collection"
    elif norm_action == "toss":
        new_status = "not_owned"
    else:
        raise InstanceError(f"invalid action: {action!r}, must be 'keep', 'save', or 'toss'")

    transition_status(
        r,
        instance_id,
        new_status=new_status,
        _allow_physical_lock_bypass=True,
    )

    updated = clear_pending_removal(r, instance_id)
    return updated


def find_unbound_remote_cards(
    r,
    registry_id: Union[int, str, list],
    current_decklist: Union[list, tuple],
) -> list:
    """Identify cards in the remote decklist that have no instance bound to this deck.

    Read-only computation — does NOT create any new instances. Used to surface
    "newly added" cards during a physical deck resync so the UI can prompt the
    user to either bind existing owned copies or create new instances.

    Args:
        r: Redis connection
        registry_id: Deck registry ID (string or int, coerced to string)
        current_decklist: List of dicts with at least {"name": str, "quantity": int}.
            These are the cards currently in the remote decklist.

    Returns:
        List of dicts representing unbound cards:
            {
                "name": str,           # card name
                "quantity": int,       # how many are in the remote decklist
                "bound_count": int,    # how many instances are already bound to this deck
                "shortfall": int,      # quantity - bound_count (always > 0)
            }
        Sorted by card name. Only includes cards where shortfall > 0.
    """
    # Accept one id or several (registry.deck_instance_ids): a deck's
    # instances may be indexed under its raw id and/or its registry_id.
    ids = [str(x) for x in registry_id] if isinstance(registry_id, (list, tuple, set)) else [str(registry_id)]

    bound_instances = list_deck_instances(r, ids)
    bound_by_card: dict[str, int] = {}
    for inst in bound_instances:
        card_name = inst.get("card_name")
        if card_name:
            bound_by_card[card_name] = bound_by_card.get(card_name, 0) + 1

    unbound = []
    for card in current_decklist:
        name = card.get("name")
        quantity = card.get("quantity", 1)
        if not name or quantity <= 0:
            continue

        bound_count = bound_by_card.get(name, 0)
        if bound_count < quantity:
            unbound.append({
                "name": name,
                "quantity": quantity,
                "bound_count": bound_count,
                "shortfall": quantity - bound_count,
            })

    unbound.sort(key=lambda x: x["name"])
    return unbound


def compute_resync_additions(r, registry_id: str, current_decklist_card_names: set) -> list:
    """Compute which cards in `current_decklist_card_names` (the new remote
    decklist) are NOT currently bound as in_deck instances for this deck.
    These are informational only — no instances are created, just a list of
    card names returned for the UI to display as "remote added these cards".

    This is the complement of `flag_resync_removed`: that flags what the
    remote removed, this surfaces what the remote added.
    """
    bound_instances = list_instances(r, deck_id=registry_id, ownership_status="in_deck")
    bound_card_names = {inst.get("card_name") for inst in bound_instances}

    additions = []
    for card_name in current_decklist_card_names:
        if card_name not in bound_card_names:
            additions.append(card_name)

    return sorted(additions)


def card_rollup(r, card_name: str) -> dict:
    """Ownership summary for one card: counts by status and per-deck breakdown."""
    instances = list_instances(r, card_name=card_name)
    counts = {s: 0 for s in OWNERSHIP_STATUSES}
    by_deck = {}
    for inst in instances:
        counts[inst["ownership_status"]] += 1
        if inst["ownership_status"] == "in_deck" and inst.get("deck_id"):
            key = inst.get("deck_name") or str(inst["deck_id"])
            by_deck[key] = by_deck.get(key, 0) + 1

    return {
        "card_name": card_name,
        "total_instances": len(instances),
        "counts": counts,
        "owned": counts["in_mail"] + counts["in_collection"] + counts["in_deck"],
        "by_deck": by_deck,
        "instances": instances,
    }


def ensure_wishlist_instances(r, registry_id: "DeckId", decklist: list) -> list:
    """Called on every deck import/re-import (any lifecycle status), from
    registry.upsert_deck. For every card in `decklist` (list of
    {"name", "quantity"}) that currently has ZERO instances of any
    ownership_status, create exactly one `not_owned` instance with
    `considered_for_deck=registry_id` so the card shows up in /inventory as
    a wishlist placeholder immediately.

    Cards that already have at least one instance (owned, in-mail, or
    already considered for another deck) are left completely untouched —
    this only fills gaps and is safe to call on every re-import: an
    already-owned deck's cards all have instances already, so re-running
    this creates nothing new.

    Returns the list of newly created placeholder instance records.
    """
    created = []
    for line in decklist:
        card_name = line.get("name")
        quantity = line.get("quantity", 1)
        if not card_name or quantity <= 0:
            continue

        if list_instances(r, card_name=card_name):
            continue

        new_inst = create_instance(
            r,
            card_name=card_name,
            ownership_status="not_owned",
            considered_for_deck=str(registry_id),
        )
        created.append(new_inst)

    return created


def auto_bind_physical(r, deck_registry_id: str, deck_name: str, decklist: list) -> dict:
    """Called when a deck transitions to status="physical". For every card in
    `decklist` (list of {"name", "quantity"}), ensure exactly `quantity`
    instances are bound in_deck to this deck: reuse existing in_collection
    copies of that card first, then promote existing `not_owned` placeholder
    instances of that card (e.g. ones created by `ensure_wishlist_instances`
    on an earlier digital/testing import) by name match, then create new
    in_collection->in_deck instances for any remaining shortfall. Never
    removes/moves instances that already belong to a different deck.

    `deck_registry_id` is stored as the instance's deck_id — for
    Archidekt-native decks this is the same int id decks have always used;
    for Moxfield/registry-only decks it's the "source:source_id" string, so
    deck_id is treated as an opaque string key end-to-end here (instances.py
    itself never assumes it's numeric).

    Returns a report: {"card_name": {"bound": n, "created": n}, ...} plus a
    top-level "shortfalls": [{"card_name", "missing"}] for cards where even
    after creating new in_collection instances we still could not reach the
    required quantity (should not normally happen since creation always
    succeeds, but flagged for visibility/review).
    """
    report = {"decks": deck_name, "cards": {}, "shortfalls": []}

    for line in decklist:
        card_name = line.get("name")
        quantity = line.get("quantity", 1)
        if not card_name or quantity <= 0:
            continue

        already_bound = [
            inst for inst in list_instances(r, card_name=card_name, deck_id=deck_registry_id)
            if inst.get("ownership_status") == "in_deck"
        ]
        bound_count = len(already_bound)
        created_count = 0

        if bound_count < quantity:
            available = [
                inst for inst in list_instances(r, card_name=card_name, ownership_status="in_collection")
            ]
            for inst in available:
                if bound_count >= quantity:
                    break
                transition_status(r, inst["id"], "in_deck", deck_id=deck_registry_id, deck_name=deck_name)
                bound_count += 1

        if bound_count < quantity:
            # Promote not_owned wishlist placeholders (e.g. created by
            # ensure_wishlist_instances on an earlier digital/testing
            # import) instead of leaving them dangling and creating a
            # brand-new duplicate instance.
            placeholders = [
                inst for inst in list_instances(r, card_name=card_name, ownership_status="not_owned")
            ]
            for inst in placeholders:
                if bound_count >= quantity:
                    break
                transition_status(r, inst["id"], "in_collection")
                transition_status(r, inst["id"], "in_deck", deck_id=deck_registry_id, deck_name=deck_name)
                bound_count += 1

        while bound_count < quantity:
            new_inst = create_instance(
                r,
                card_name=card_name,
                ownership_status="in_collection",
                notes="auto-created on physical deck bind",
            )
            transition_status(r, new_inst["id"], "in_deck", deck_id=deck_registry_id, deck_name=deck_name)
            bound_count += 1
            created_count += 1

        report["cards"][card_name] = {"bound": bound_count, "created": created_count}
        if bound_count < quantity:
            report["shortfalls"].append({"card_name": card_name, "missing": quantity - bound_count})

    return report
