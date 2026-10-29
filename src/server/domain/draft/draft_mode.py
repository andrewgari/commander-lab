"""Draft mode domain logic (ARCHITECTURE.md §7.8, §8 Slice 5): combine decks /
dismantle one to build another, staging-only until Commit.

Pure data-in / data-out: no FastAPI, no SQLAlchemy, no infra imports
(boundary rule 2). Persistence lives in infra/db/repositories.py; this module
owns the rules:

- create validation (combine needs >= 2 source decks, dismantle exactly 1)
- prefill (seed a combine draft's staging from its source decks' lists)
- staging edits (add/remove cards, stage/unstage instance moves)
- the commit PLAN: exactly which canonical rows change at Commit, validated
  against the instances.py ownership state machine. Commit applies the plan
  in ONE transaction: the new deck's `deck_cards` rows plus one
  `instance_events` row per applied move (kind=assigned|unassigned,
  from_deck/to_deck, draft_id) — that event stream IS the provenance (§4.2);
  the MOVED graph edge derives from it in Slice 6, not here.

Move semantics (one staged row per instance):
    assign     instance -> the draft's result deck. Legal from in_collection
               or in_deck (a donor pull out of a source/other deck).
    unassign   instance -> back to the collection. Legal from in_deck only.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable, List, Mapping, Optional

KIND_COMBINE = "combine"
KIND_DISMANTLE = "dismantle"
KINDS = (KIND_COMBINE, KIND_DISMANTLE)

STATUS_OPEN = "open"
STATUS_COMMITTED = "committed"
STATUS_ABANDONED = "abandoned"

ACTION_ASSIGN = "assign"
ACTION_UNASSIGN = "unassign"
ACTIONS = (ACTION_ASSIGN, ACTION_UNASSIGN)

BOARDS = ("commander", "main", "sideboard", "maybe", "considering")

# Ownership states a staged move may start from (instances.py state machine).
_ASSIGNABLE_FROM = {"in_collection", "in_deck"}
_UNASSIGNABLE_FROM = {"in_deck"}


class DraftError(ValueError):
    """Raised on any draft-rule violation (bad kind, closed draft, illegal
    move, commit validation failure). The API maps it to a 4xx."""


def new_draft_id() -> str:
    return f"draft-{uuid.uuid4().hex[:12]}"


def result_deck_id(draft_id: str) -> str:
    """The canonical decks.id a draft creates at Commit (draft:<draft_id>)."""
    return f"draft:{draft_id}"


def validate_create(kind: str, source_deck_ids: Iterable[str],
                    target_name: str) -> List[str]:
    """-> normalized [source_deck_id, ...]; raises DraftError on violations."""
    if kind not in KINDS:
        raise DraftError(f"kind must be one of {list(KINDS)}")
    ids = [str(d) for d in source_deck_ids or []]
    if len(set(ids)) != len(ids):
        raise DraftError("source deck ids must be distinct")
    if kind == KIND_COMBINE and len(ids) < 2:
        raise DraftError("a combine draft needs at least 2 source decks")
    if kind == KIND_DISMANTLE and len(ids) != 1:
        raise DraftError("a dismantle draft takes exactly 1 source deck")
    if not (target_name or "").strip():
        raise DraftError("target_name is required")
    return ids


def prefill_cards(source_lists: Mapping[str, List[dict]]) -> List[dict]:
    """Seed staging rows for a combine draft from its source decks' lists.

    Union across decks: qty is the max across sources (Commander is
    singleton), commander board wins over main, first category seen is kept.
    Purely a starting point — the user edits staging freely afterwards.
    """
    merged: Dict[str, dict] = {}
    for deck_id in source_lists:
        for row in source_lists[deck_id]:
            oid = row["oracle_id"]
            board = row.get("board") or "main"
            key = oid
            existing = merged.get(key)
            if existing is None:
                merged[key] = {
                    "oracle_id": oid,
                    "qty": int(row.get("qty") or 1),
                    "board": "commander" if board == "commander" else board,
                    "category": row.get("category"),
                }
            else:
                existing["qty"] = max(existing["qty"], int(row.get("qty") or 1))
                if board == "commander":
                    existing["board"] = "commander"
                if not existing.get("category") and row.get("category"):
                    existing["category"] = row["category"]
    return sorted(merged.values(), key=lambda r: (r["board"] != "commander", r["oracle_id"]))


def validate_staged_card(oracle_id: str, qty: int, board: str) -> None:
    if not oracle_id:
        raise DraftError("oracle_id is required")
    if board not in BOARDS:
        raise DraftError(f"board must be one of {list(BOARDS)}")
    if int(qty) < 1:
        raise DraftError("qty must be >= 1")


def validate_move(action: str, instance: Optional[dict]) -> None:
    """A move may be staged only from a legal CURRENT ownership state; the
    same check re-runs at Commit against the then-current state."""
    if action not in ACTIONS:
        raise DraftError(f"action must be one of {list(ACTIONS)}")
    if instance is None:
        raise DraftError("instance not found")
    status = instance.get("status")
    if action == ACTION_ASSIGN and status not in _ASSIGNABLE_FROM:
        raise DraftError(
            f"cannot assign an instance with status {status!r} "
            f"(must be one of {sorted(_ASSIGNABLE_FROM)})"
        )
    if action == ACTION_UNASSIGN and status not in _UNASSIGNABLE_FROM:
        raise DraftError(
            f"cannot unassign an instance with status {status!r} "
            f"(must be in_deck)"
        )


def plan_commit(
    draft: Mapping[str, Any],
    draft_cards: List[dict],
    moves: List[dict],
    instances_by_id: Mapping[str, dict],
) -> dict:
    """Validate an open draft and compute the exact canonical changes Commit
    will apply in one transaction. Raises DraftError on any violation — the
    repository applies nothing when this raises.

    Returns:
        deck:      decks row fields (id, name, status, source='draft', ...)
        deck_cards: deck_cards rows to insert
        events:    instance_events rows (kind/from_deck/to_deck/draft_id)
        updates:   card_instances updates [(instance_id, status, deck_id)]
    """
    if draft.get("status") != STATUS_OPEN:
        raise DraftError(f"draft is {draft.get('status')}, not open")
    if not draft_cards:
        raise DraftError("cannot commit an empty draft (no staged cards)")

    deck_id = result_deck_id(draft["id"])
    commander_rows = [c for c in draft_cards if c.get("board") == "commander"]
    commanders = [c.get("name") or c["oracle_id"] for c in commander_rows]

    updates: List[tuple] = []
    events: List[dict] = []
    seen: set = set()
    for move in moves:
        iid = move["instance_id"]
        if iid in seen:
            raise DraftError(f"instance {iid} is staged twice")
        seen.add(iid)
        inst = instances_by_id.get(iid)
        # Re-validate against the CURRENT state — the world may have changed
        # since the move was staged.
        validate_move(move["action"], inst)
        assert inst is not None  # validate_move raises on None
        from_deck = inst.get("deck_id")
        if move["action"] == ACTION_ASSIGN:
            updates.append((iid, "in_deck", deck_id))
            events.append({
                "instance_id": iid, "kind": "assigned",
                "from_deck": from_deck, "to_deck": deck_id,
                "draft_id": draft["id"],
            })
        else:
            updates.append((iid, "in_collection", None))
            events.append({
                "instance_id": iid, "kind": "unassigned",
                "from_deck": from_deck, "to_deck": None,
                "draft_id": draft["id"],
            })

    # Color identity of the result deck: union of its staged cards' identities.
    color_identity: set = set()
    for c in draft_cards:
        for col in c.get("color_identity") or []:
            color_identity.add(col)

    deck = {
        "id": deck_id,
        "name": draft.get("target_name") or draft.get("name"),
        "status": "have" if any(m["action"] == ACTION_ASSIGN for m in moves) else "virtual",
        "source": "draft",
        "commanders": commanders,
        "color_identity": sorted(color_identity),
        "archidekt_id": None,
        "fingerprint": "",
    }
    deck_cards_out = [
        {
            "oracle_id": c["oracle_id"],
            "qty": int(c["qty"]),
            "board": c["board"],
            "category": c.get("category"),
        }
        for c in draft_cards
    ]
    return {"deck": deck, "deck_cards": deck_cards_out, "updates": updates, "events": events}


def provenance_path(instance_id: str, events: List[dict],
                    deck_names: Optional[Mapping[str, str]] = None) -> dict:
    """The queryable provenance path for one physical copy ("this copy moved
    A→B"), built from its ordered instance_events (§4.2 — the events ARE the
    provenance; the graph edge derives from them in Slice 6)."""
    names = deck_names or {}
    steps = []
    for e in events:
        frm, to = e.get("from_deck"), e.get("to_deck")
        kind = e.get("kind") or ""
        steps.append({
            "ts": e.get("ts"),
            "kind": kind,
            "from_deck": frm,
            "from_deck_name": names.get(frm) if frm else None,
            "to_deck": to,
            "to_deck_name": names.get(to) if to else None,
            "draft_id": e.get("draft_id"),
            "summary": _step_summary(kind, frm, to, names),
        })
    return {"instance_id": instance_id, "steps": steps, "count": len(steps)}


def _step_summary(kind: str, frm: Optional[str], to: Optional[str],
                  names: Mapping[str, str]) -> str:
    def label(d: Optional[str]) -> str:
        if d is None:
            return "collection"
        return names.get(d) or d
    if kind == "assigned":
        return f"moved {label(frm)} → {label(to)}"
    if kind == "unassigned":
        return f"moved {label(frm)} → collection"
    return f"{kind} at {label(to) if to else label(frm)}"
