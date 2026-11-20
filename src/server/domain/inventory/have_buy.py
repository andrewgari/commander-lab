"""Have/buy split per deck (ARCHITECTURE.md §5: Slice-1 headline view).

Pure math over plain dicts — deck_cards ⋈ card_instances. No FastAPI, no
SQLAlchemy, no infra imports.

Semantics (matching the legacy UI's per-copy status vocabulary):
- Scope: the deck to build = ``commander`` + ``main`` boards. maybe/
  considering/sideboard are not part of the split.
- ``bound``: copies with status in_deck for THIS deck.
- ``free``: copies with status in_collection (unassigned owned pool).
- ``mailed``: copies with status in_mail (bought, not here yet -> "pending").
- ``elsewhere``: copies in_deck for OTHER decks (owned but committed;
  reported for information, never counted as available).
- ``not_owned`` rows are wishlist placeholders: excluded from the math; the
  buy count is derived, not read from placeholders.

Per decklist card (qty = need):
    have    = min(need, bound + free)
    pending = min(need - have, mailed)
    buy     = need - have - pending
"""
from __future__ import annotations

BUY_SCOPE_BOARDS = ("commander", "main")


def _count_instances(instances: list, deck_id: str) -> dict:
    """oracle_id -> {bound, free, mailed, elsewhere} copy counts."""
    counts: dict = {}
    for inst in instances:
        c = counts.setdefault(
            inst["oracle_id"], {"bound": 0, "free": 0, "mailed": 0, "elsewhere": 0}
        )
        status = inst.get("status")
        if status == "in_deck":
            if inst.get("deck_id") == deck_id:
                c["bound"] += 1
            else:
                c["elsewhere"] += 1
        elif status == "in_collection":
            c["free"] += 1
        elif status == "in_mail":
            c["mailed"] += 1
    return counts


def compute_have_buy(deck_cards: list, instances: list, deck_id: str) -> dict:
    """Have/buy report for one deck.

    deck_cards: rows with oracle_id, name, qty, board (+ optional display
    fields). instances: rows with oracle_id, status, deck_id covering at
    least every oracle on the deck (pass the full collection when in doubt).
    """
    counts = _count_instances(instances, deck_id)
    lines = []
    totals = {"need": 0, "have": 0, "pending": 0, "buy": 0}
    for card in deck_cards:
        if card["board"] not in BUY_SCOPE_BOARDS:
            continue
        need = int(card["qty"])
        c = counts.get(card["oracle_id"], {"bound": 0, "free": 0, "mailed": 0, "elsewhere": 0})
        have = min(need, c["bound"] + c["free"])
        pending = min(need - have, c["mailed"])
        buy = need - have - pending
        lines.append({
            "oracle_id": card["oracle_id"],
            "name": card.get("name", ""),
            "board": card["board"],
            "category": card.get("category"),
            "need": need,
            "have": have,
            "pending": pending,
            "buy": buy,
            "elsewhere": c["elsewhere"],
        })
        totals["need"] += need
        totals["have"] += have
        totals["pending"] += pending
        totals["buy"] += buy
    lines.sort(key=lambda l: (l["board"] != "commander", l["name"].lower()))
    return {
        "deck_id": deck_id,
        "totals": {
            **totals,
            "complete": totals["buy"] == 0 and totals["pending"] == 0,
            "have_pct": round(100 * totals["have"] / totals["need"]) if totals["need"] else None,
        },
        "lines": lines,
    }
