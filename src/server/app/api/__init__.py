"""Slice 1+2 typed API (mounted at /api/v2): imports, decks, inventory,
profile.

Thin routers only: parse request -> call infra services/repos -> serialize
pydantic responses (AGENT_RULES.md §6). Business logic lives in domain/,
persistence in infra/db/repositories.py.
"""
from __future__ import annotations

from fastapi import APIRouter

from src.server.app.api import decks, imports, inventory, profile

router = APIRouter(prefix="/api/v2", tags=["v2"])
router.include_router(imports.router)
router.include_router(decks.router)
router.include_router(inventory.router)
router.include_router(profile.router)
