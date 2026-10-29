"""Read-only queries behind GET /api/v2/cards/search (docs/ui/card-search.md §5).

Ownership is always computed here from ``card_instances`` on every request; it
is never cached. ``not_owned`` placeholder copies never count as owned (same
rule as the inventory totals).
"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.server.domain.cards.search_query import SearchFilters, identity_matches
from src.server.infra.db.orm_models import CardInstanceRow, CardRow, PrintingRow

OWNED_STATUSES = ("in_collection", "in_deck", "in_mail")
FREE_STATUS = "in_collection"
_RARITY_RANK = {"common": 0, "uncommon": 1, "rare": 2, "mythic": 3}
MAX_LOCAL_ROWS = 5000


def owned_counts(session: Session, oracle_ids: Iterable[str]) -> dict:
    """{oracle_id: (owned_qty, free_qty)}; absent id == (0, 0)."""
    ids = sorted({o for o in oracle_ids if o})
    if not ids:
        return {}
    stmt = (select(CardInstanceRow.oracle_id, CardInstanceRow.status, func.count())
            .where(CardInstanceRow.oracle_id.in_(ids),
                   CardInstanceRow.status.in_(OWNED_STATUSES))
            .group_by(CardInstanceRow.oracle_id, CardInstanceRow.status))
    out: dict = {}
    for oid, status, n in session.execute(stmt).all():
        owned, free = out.get(oid, (0, 0))
        out[oid] = (owned + int(n), free + (int(n) if status == FREE_STATUS else 0))
    return out


def _facts(row: CardRow, rarity: str) -> dict:
    return {
        "oracle_id": row.oracle_id, "name": row.name, "mana_cost": row.mana_cost or "",
        "cmc": float(row.cmc or 0), "type_line": row.type_line or "",
        "color_identity": list(row.color_identity or []), "rarity": rarity,
        "oracle_text": row.oracle_text or "", "image": row.image_uri or "",
        "_keywords": list(row.keywords or []),
        "_legal": (row.legalities or {}).get("commander", ""),
    }


def get_card_facts(session: Session, oracle_id: str) -> Optional[dict]:
    row = session.get(CardRow, oracle_id)
    return _facts(row, "") if row else None


def _like(column, needle: str):
    esc = needle.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return func.lower(column).like(f"%{esc}%", escape="\\")


def _best_rarities(session: Session, oracle_ids: Sequence[str]) -> dict:
    """Rarity shown for a local row: the default printing's, else the highest."""
    out: dict = {}
    if not oracle_ids:
        return out
    stmt = select(PrintingRow.oracle_id, PrintingRow.rarity, PrintingRow.is_default)
    for oid, rarity, is_default in session.execute(
            stmt.where(PrintingRow.oracle_id.in_(list(oracle_ids)))).all():
        cur = out.get(oid)
        rank = (bool(is_default), _RARITY_RANK.get(rarity, -1))
        if cur is None or rank > cur[0]:
            out[oid] = (rank, rarity)
    return {oid: v[1] for oid, v in out.items()}


def local_candidates(session: Session, f: SearchFilters, *, owned_mode: str = "any",
                     pair_hints: Sequence = ()) -> list:
    """Cards matching the filters in the local ``cards`` mirror, ordered by name.

    Simple predicates run in SQL; the JSON columns (color identity, legality)
    are checked in Python so the same code serves SQLite and Postgres. The
    commander/pairing rules are applied by the caller.
    """
    owned_ids = select(CardInstanceRow.oracle_id).where(CardInstanceRow.status.in_(OWNED_STATUSES))
    stmt = select(CardRow)
    for tok in f.name_tokens:
        stmt = stmt.where(_like(CardRow.name, tok))
    if f.types:
        stmt = stmt.where(or_(*[_like(CardRow.type_line, t) for t in f.types]))
    for word in f.type_words:
        stmt = stmt.where(_like(CardRow.type_line, word))
    if f.mv:
        conds = []
        for chip in f.mv:
            conds.append(CardRow.cmc >= int(chip[:-1]) if chip.endswith("+")
                         else CardRow.cmc == int(chip))
        stmt = stmt.where(or_(*conds))
    if f.rarities:
        stmt = stmt.where(CardRow.oracle_id.in_(
            select(PrintingRow.oracle_id).where(PrintingRow.rarity.in_(list(f.rarities)))))
    for term in f.oracle_terms:
        stmt = stmt.where(_like(CardRow.oracle_text, term))
    if pair_hints:
        stmt = stmt.where(or_(*[_like(getattr(CardRow, col), sub) for col, sub in pair_hints]))
    if owned_mode == "owned":
        stmt = stmt.where(CardRow.oracle_id.in_(owned_ids))
    elif owned_mode == "not_owned":
        stmt = stmt.where(CardRow.oracle_id.not_in(owned_ids))
    stmt = stmt.order_by(func.lower(CardRow.name), CardRow.oracle_id).limit(MAX_LOCAL_ROWS)
    rows = []
    for row in session.execute(stmt).scalars():
        if (row.legalities or {}).get("commander", "legal") != "legal":
            continue
        if not identity_matches(row.color_identity, f.colors, f.ci_mode):
            continue
        rows.append(row)
    rarities = _best_rarities(session, [r.oracle_id for r in rows])
    return [_facts(r, rarities.get(r.oracle_id, "")) for r in rows]
