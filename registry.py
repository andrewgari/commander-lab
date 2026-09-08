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
    return decks[existing_idx] if existing_idx is not None else decks[-1]


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
            deck_registry_id=registry_id_of(target),
            deck_name=target["name"],
            decklist=target.get("cards", []),
        )

    return {"deck": target, "auto_bind": bind_report}
