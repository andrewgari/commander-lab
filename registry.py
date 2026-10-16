"""
Deck registry — the source-of-truth list of decks Commander Lab knows about,
across both providers (Archidekt full-account sync, Moxfield/Archidekt
single-deck imports).

Storage: reuses the existing `decks` Redis key (JSON array), same key
sync.py has always written. Each deck dict now additionally carries:
    source        "archidekt" | "moxfield"
    source_id     provider-native id (string)
    registry_id   f"{source}:{source_id}" — stable, provider-agnostic key
                  used as instances.deck_id for decks that don't have an
                  Archidekt integer id (e.g. Moxfield-native imports).
    cards         [{"name": str, "quantity": int}, ...] mainboard cardlist,
                  used only to auto-bind physical-deck instances.
    status        "physical" | "digital" | "retired" | "testing"

Lifecycle status values (renamed from the old physical/virtual/retired/test
vocabulary — same field, same mechanism, just relabeled):
    physical  — deck is physically assembled; every card is bound 1:1 to an
                owned card Instance (see instances.py). Immutable in the
                sense that instances currently in_deck for this deck cannot
                be moved out while it stays physical (enforced in
                instances.transition_status via the is_deck_physical check).
    digital   — exists only as a decklist (was "virtual").
    retired   — no longer played/maintained.
    testing   — being brewed/tested (was "test").
"""
import json
from typing import Optional

import instances as instance_store

VALID_STATUSES = {"physical", "digital", "retired", "testing"}

DECKS_KEY = "decks"


def _load_decks(r) -> list:
    raw = r.get(DECKS_KEY)
    return json.loads(raw) if raw else []


def _save_decks(r, decks: list) -> None:
    r.set(DECKS_KEY, json.dumps(decks))


def registry_id_of(deck: dict) -> str:
    if deck.get("registry_id"):
        return deck["registry_id"]
    return f"{deck.get('source', 'archidekt')}:{deck.get('source_id', deck.get('id'))}"


def list_decks(r) -> list:
    return _load_decks(r)


def find_deck(r, registry_id: str) -> Optional[dict]:
    for deck in _load_decks(r):
        if registry_id_of(deck) == registry_id or str(deck.get("id")) == registry_id:
            return deck
    return None


def is_deck_physical(r, registry_id: str) -> bool:
    deck = find_deck(r, registry_id)
    return bool(deck) and deck.get("status") == "physical"


def upsert_deck(r, normalized: dict, default_status: str = "testing") -> dict:
    """Insert or update a deck from a normalized provider payload
    (see providers/__init__.py NORMALIZED_DECK_SHAPE). Preserves an
    existing deck's status/id on re-import; new decks get default_status.
    """
    reg_id = f"{normalized['source']}:{normalized['source_id']}"
    decks = _load_decks(r)

    existing = None
    existing_idx = None
    for idx, d in enumerate(decks):
        if registry_id_of(d) == reg_id:
            existing, existing_idx = d, idx
            break

    deck_id = existing.get("id") if existing else (
        int(normalized["source_id"]) if normalized["source"] == "archidekt" and normalized["source_id"].isdigit()
        else reg_id
    )

    saved_status = r.get(f"deck_status:{deck_id}")
    status = saved_status or (existing.get("status") if existing else default_status)
    if status not in VALID_STATUSES:
        status = default_status

    deck_obj = {
        "id": deck_id,
        "name": normalized["name"],
        "color": normalized["color"],
        "commanders": normalized["commanders"],
        "commander_uids": normalized["commander_uids"],
        "folder": normalized.get("folder", "Imported"),
        "status": status,
        "description": normalized.get("description", ""),
        "source": normalized["source"],
        "source_id": normalized["source_id"],
        "registry_id": reg_id,
        "cards": normalized.get("cards", []),
        "url": normalized.get("url", ""),
    }
    if normalized["source"] == "moxfield":
        deck_obj["moxfield_id"] = normalized["source_id"]

    if existing_idx is not None:
        # Preserve any Archidekt-account-sync-only fields (e.g. description
        # already set) unless the new payload provides a better value.
        merged = dict(existing or {})
        merged.update(deck_obj)
        decks[existing_idx] = merged
    else:
        decks.append(deck_obj)

    _save_decks(r, decks)
    r.set(f"deck_status:{deck_id}", status)

    result_deck = decks[existing_idx] if existing_idx is not None else decks[-1]
    instance_store.ensure_wishlist_instances(r, registry_id_of(result_deck), result_deck.get("cards", []))

    return result_deck


def add_card_to_decklist(r, registry_id: str, card_name: str) -> dict:
    """Append `card_name` (quantity 1) to a deck's local decklist if it isn't
    already present. Used by the manual add-card path (POST
    /api/decks/{id}/cards) — layers manual adds on top of a synced decklist
    without duplicating an existing entry. Returns the updated deck dict.
    """
    decks = _load_decks(r)
    target = None
    for d in decks:
        if registry_id_of(d) == registry_id or str(d.get("id")) == registry_id:
            target = d
            break
    if not target:
        raise ValueError(f"deck not found: {registry_id}")

    cards = target.setdefault("cards", [])
    already_present = any(c.get("name") == card_name for c in cards)
    if not already_present:
        cards.append({"name": card_name, "quantity": 1})
        _save_decks(r, decks)

    return target


def set_status(r, registry_id: str, new_status: str) -> dict:
    if new_status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {new_status}")

    decks = _load_decks(r)
    target = None
    for d in decks:
        if registry_id_of(d) == registry_id or str(d.get("id")) == registry_id:
            target = d
            break
    if not target:
        raise ValueError(f"deck not found: {registry_id}")

    was_physical = target.get("status") == "physical"
    target["status"] = new_status
    _save_decks(r, decks)
    r.set(f"deck_status:{target['id']}", new_status)

    bind_report = None
    if new_status == "physical" and not was_physical:
        bind_report = instance_store.auto_bind_physical(
            r,
            deck_registry_id=target["id"],
            deck_name=target["name"],
            decklist=target.get("cards", []),
        )

    return {"deck": target, "auto_bind": bind_report}


DEFAULT_DECOMMISSION_TAG = "removed"


class DecommissionError(ValueError):
    """Deck cannot be decommissioned (e.g. not a physical deck)."""


def decommission_deck(r, registry_id: str, tag: str = DEFAULT_DECOMMISSION_TAG) -> dict:
    """Decommission a PHYSICAL deck in one Redis MULTI/EXEC transaction:

      1. reject unless deck status == "physical" (DecommissionError)
      2. drop `tag` from the deck's `tags` list (deck-level status tag)
      3. move the deck to the "retired" category (status = "retired")
      4. unbind every instance bound in_deck to this deck -> in_collection
         (cards stay owned; nothing is deleted)

    Idempotent: a deck already decommissioned (has `decommissioned_at`) returns
    `already_decommissioned=True` with no writes. Raises ValueError if the deck
    is not found.
    """
    decks = _load_decks(r)
    target = next(
        (d for d in decks if registry_id_of(d) == registry_id or str(d.get("id")) == registry_id),
        None,
    )
    if not target:
        raise ValueError(f"deck not found: {registry_id}")

    if target.get("decommissioned_at"):
        return {
            "deck": target,
            "already_decommissioned": True,
            "tag_removed": False,
            "unassigned_count": 0,
            "unassigned_instance_ids": [],
        }

    if target.get("status") != "physical":
        raise DecommissionError(
            f"only physical decks can be decommissioned (deck status is {target.get('status')!r})"
        )

    reg_id = registry_id_of(target)
    bound = {}
    for key in {str(reg_id), str(target.get("id"))}:
        for inst in instance_store.list_instances(r, deck_id=key):
            if inst.get("ownership_status") == "in_deck":
                bound[inst["id"]] = inst

    now = instance_store._now()
    tags = list(target.get("tags") or [])
    tag_removed = tag in tags
    target["tags"] = [t for t in tags if t != tag]
    target["status"] = "retired"
    target["decommissioned_at"] = now

    pipe = r.pipeline()
    for inst in bound.values():
        instance_store.queue_unassign_from_deck(pipe, inst, now)
    pipe.set(DECKS_KEY, json.dumps(decks))
    pipe.set(f"deck_status:{target['id']}", "retired")
    pipe.execute()

    return {
        "deck": target,
        "already_decommissioned": False,
        "tag_removed": tag_removed,
        "unassigned_count": len(bound),
        "unassigned_instance_ids": sorted(bound),
    }
