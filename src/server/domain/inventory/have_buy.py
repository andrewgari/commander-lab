"""Have/buy split for a deck — pure math, no I/O (boundary rule 1).

Inputs are plain dicts handed over by the caller (repository rows at the API
edge, Redis-shaped records in the parity harness):

    deck_cards: [{"oracle_id", "name", "qty", "board", "category"?}, ...]
    instances:  [{"id", "oracle_id", "status", "deck_id"}, ...]   (ALL owned
                copies — this function filters by status/deck itself)

Vocabulary matches the legacy inventory read model (inventory_view.py):
    in_deck / in_collection -> "have"
    in_mail                 -> "pending"  (owned, but not playable yet)
    shortfall               -> "buy"

Which copies count as "have" for deck D, per card:
    1. copies in_deck for D (physically in it), then
    2. copies in_collection (the unassigned pool), then
    3. copies in_mail are NOT have — they surface as `pending`.
Buy = max(0, required - have). A single copy is never double-counted: each
instance id satisfies at most one board slot for one deck per call.

Only the playable boards count toward the requirement: commander + main.
Maybe/considering/sideboard are pools, not requirements (they are reported
separately by the caller, never in the split).

The math is deterministic (AGENT_RULES §5) and oracle_id-keyed, matching the
Postgres schema; the name-based fallback lives upstream in
reconciliation.py and is intentionally not duplicated here.
"""
from __future__ import annotations

REQUIREMENT_BOARDS = ("commander", "main")

# Old-UI status vocabulary (inventory_view.STATUS_LABELS) — keep aligned.
STATUS_LABELS = {
    "in_deck": "have",
    "in_collection": "have",
    "in_mail": "pending",
    "not_owned": "possible",
}


def _new_card_row(name, oracle_id, board, category):
    return {
        "oracle_id": oracle_id,
        "name": name,
        "board": board,
        "category": category,
        "required": 0,
        "have": 0,
        "have_in_deck": 0,
        "have_from_collection": 0,
        "pending": 0,
        "buy": 0,
    }


def compute_have_buy(deck_cards: list, instances: list, deck_id) -> dict:
    """Split one deck's requirements into have / pending / buy.

    Returns {"cards": [per-card rows...], "totals": {...}}. Per-card rows are
    sorted by name for a stable UI/diff order.
    """
    cards: dict = {}
    for dc in deck_cards or []:
        if dc.get("board") not in REQUIREMENT_BOARDS:
            continue
        key = dc["oracle_id"]
        row = cards.setdefault(key, _new_card_row(
            dc.get("name") or "", key, dc.get("board"), dc.get("category")))
        row["required"] += int(dc.get("qty") or 1)

    # Bucket copies: bound to this deck first, then the free pool, then mail.
    in_deck: dict = {}
    in_collection: dict = {}
    in_mail: dict = {}
    for inst in instances or []:
        status = inst.get("status")
        oid = inst.get("oracle_id")
        if not oid:
            continue
        if status == "in_deck" and inst.get("deck_id") == deck_id:
            in_deck[oid] = in_deck.get(oid, 0) + 1
        elif status == "in_collection":
            in_collection[oid] = in_collection.get(oid, 0) + 1
        elif status == "in_mail":
            in_mail[oid] = in_mail.get(oid, 0) + 1

    totals = {"required": 0, "have": 0, "pending": 0, "buy": 0,
              "have_in_deck": 0, "have_from_collection": 0}
    for row in cards.values():
        oid = row["oracle_id"]
        bound = in_deck.get(oid, 0)
        from_pool = max(0, min(row["required"] - bound, in_collection.get(oid, 0)))
        have = bound + from_pool
        row["have_in_deck"] = bound
        row["have_from_collection"] = from_pool
        row["have"] = have
        row["pending"] = in_mail.get(oid, 0)
        row["buy"] = max(0, row["required"] - have)
        totals["required"] += row["required"]
        totals["have"] += have
        totals["pending"] += row["pending"]
        totals["buy"] += row["buy"]
        totals["have_in_deck"] += bound
        totals["have_from_collection"] += from_pool

    return {
        "cards": sorted(cards.values(), key=lambda r: (r["board"] != "commander", r["name"])),
        "totals": totals,
    }
