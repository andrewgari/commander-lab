"""Inventory endpoint over the Postgres collection (Slice 2)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.server.app.api import schemas
from src.server.app.api.deps import db_session
from src.server.infra.db import repositories as repo

router = APIRouter()


@router.get("/inventory", response_model=schemas.InventoryResponse)
def inventory(q: str = "", session: Session = Depends(db_session)):
    """Per-card collection rows (owned copies by status + deck bindings).

    ``q`` filters card names (case-insensitive substring). ``not_owned``
    placeholder copies are reported per row but never summed into totals.
    """
    rows = repo.inventory_rows(session, query=q.strip())
    totals = schemas.InventoryTotals(
        cards=len(rows),
        owned=sum(r["owned"] for r in rows),
        in_collection=sum(r["in_collection"] for r in rows),
        in_deck=sum(r["in_deck"] for r in rows),
        in_mail=sum(r["in_mail"] for r in rows),
    )
    return schemas.InventoryResponse(
        inventory=[schemas.InventoryRow(**r) for r in rows], totals=totals
    )
