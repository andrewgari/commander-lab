"""Flag-gated repository over deck source links (Phase 1b cutover).

Identity is the link itself: ``(provider, external_id)`` — never name or
commander — so there is no merge/dedupe logic.

Backend selection (``deck_links_repository``):
- ``PG_CUTOVER`` on  -> PostgreSQL ``deck_links`` table (reads + writes).
- ``PG_CUTOVER`` off -> Redis registry ``decks`` key, **read-only**. Redis is
  the fallback during the cutover window; it is never written here and no
  Redis key is ever deleted.

Local-owned data (physical status, manual tags, journals, ideas, reviews) is
not part of this repository and is never touched by it.
"""
from __future__ import annotations

import json
from typing import Any, Optional, Sequence

from sqlalchemy import select

from analytics.repository_interfaces import ConflictError, NotFoundError, Repository
from db.config import cutover_enabled
from db.orm_models import DeckLinkRow

LinkId = tuple[str, str]  # (provider, external_id)


class ReadOnlyError(Exception):
    """Raised when a write is attempted against the read-only Redis fallback."""


def _row_to_dict(row: DeckLinkRow) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "deck_id": row.deck_id,
        "provider": row.provider,
        "external_id": row.external_id,
        "url": row.url,
        "folder_id": row.folder_id,
        "name_at_source": row.name_at_source,
        "commander_at_source": row.commander_at_source,
        "match_method": row.match_method,
        "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
        "upstream_missing_at": row.upstream_missing_at.isoformat() if row.upstream_missing_at else None,
        "raw": row.raw,
    }


class PgDeckLinksRepository(Repository[dict, LinkId]):
    """PostgreSQL backend over ``deck_links`` (active when PG_CUTOVER is on)."""

    def __init__(self, session: Any):
        self._s = session

    def _get_row(self, link_id: LinkId) -> Optional[DeckLinkRow]:
        provider, external_id = link_id
        return self._s.execute(
            select(DeckLinkRow).where(DeckLinkRow.provider == provider,
                                      DeckLinkRow.external_id == external_id)
        ).scalar_one_or_none()

    def create(self, entity: dict) -> dict:
        link_id = (entity["provider"], entity["external_id"])
        if self._get_row(link_id) is not None:
            raise ConflictError(str(link_id))
        row = DeckLinkRow(**{k: entity.get(k) for k in (
            "user_id", "deck_id", "provider", "external_id", "url", "folder_id",
            "name_at_source", "commander_at_source", "match_method",
            "last_synced_at", "upstream_missing_at", "raw")})
        self._s.add(row)
        self._s.flush()
        return _row_to_dict(row)

    def get(self, entity_id: LinkId) -> Optional[dict]:
        row = self._get_row(entity_id)
        return _row_to_dict(row) if row is not None else None

    def list(self) -> Sequence[dict]:
        rows = self._s.execute(select(DeckLinkRow)).scalars().all()
        return [_row_to_dict(r) for r in rows]

    def list_for_deck(self, deck_id: str) -> Sequence[dict]:
        rows = self._s.execute(
            select(DeckLinkRow).where(DeckLinkRow.deck_id == deck_id)).scalars().all()
        return [_row_to_dict(r) for r in rows]

    def update(self, entity_id: LinkId, entity: dict) -> dict:
        """Overwrite upstream-owned columns only. Local-owned data (status,
        tags, journals, reviews) lives elsewhere and is never touched here.
        Upstream deletion is a tombstone: set ``upstream_missing_at``, never
        delete the row."""
        row = self._get_row(entity_id)
        if row is None:
            raise NotFoundError(str(entity_id))
        for k in ("url", "folder_id", "name_at_source", "commander_at_source",
                  "match_method", "last_synced_at", "upstream_missing_at", "raw",
                  "deck_id", "user_id"):
            if k in entity:
                setattr(row, k, entity[k])
        self._s.flush()
        return _row_to_dict(row)

    def delete(self, entity_id: LinkId) -> bool:
        row = self._get_row(entity_id)
        if row is None:
            return False
        self._s.delete(row)
        self._s.flush()
        return True


class RedisDeckLinksRepository(Repository[dict, LinkId]):
    """Read-only Redis fallback over the registry ``decks`` key.

    Used while PG_CUTOVER is off. All writes raise ReadOnlyError: during the
    cutover window Redis is read-only and keys are never deleted.
    """

    def __init__(self, client: Any):
        self._r = client

    def _links(self) -> list[dict]:
        raw = self._r.get("decks")
        decks = json.loads(raw) if raw else []
        out = []
        for deck in decks if isinstance(decks, list) else []:
            if not isinstance(deck, dict):
                continue
            for provider, entry in (deck.get("links") or {}).items():
                if isinstance(entry, dict):
                    out.append({
                        "deck_id": deck.get("registry_id"),
                        "provider": provider,
                        "external_id": entry.get("deck_id") or entry.get("identifier"),
                        "url": entry.get("identifier") or deck.get("url", ""),
                        "folder_id": deck.get("parent_folder_id"),
                        "name_at_source": deck.get("name", ""),
                        "commander_at_source": ", ".join(deck.get("commanders") or []),
                        "raw": entry,
                    })
        return out

    def create(self, entity: dict) -> dict:
        raise ReadOnlyError("Redis is read-only during the cutover window")

    def get(self, entity_id: LinkId) -> Optional[dict]:
        provider, external_id = entity_id
        for link in self._links():
            if link["provider"] == provider and link["external_id"] == external_id:
                return link
        return None

    def list(self) -> Sequence[dict]:
        return self._links()

    def update(self, entity_id: LinkId, entity: dict) -> dict:
        raise ReadOnlyError("Redis is read-only during the cutover window")

    def delete(self, entity_id: LinkId) -> bool:
        raise ReadOnlyError("Redis is read-only during the cutover window")


def deck_links_repository(redis_client: Any = None, session: Any = None,
                          env: Optional[dict] = None) -> Repository[dict, LinkId]:
    """Choose the backend from the PG_CUTOVER flag.

    Flag on  -> PgDeckLinksRepository (requires ``session``).
    Flag off -> RedisDeckLinksRepository read-only fallback (requires ``redis_client``).
    """
    if cutover_enabled(env):
        if session is None:
            raise RuntimeError("PG_CUTOVER is on but no database session was provided")
        return PgDeckLinksRepository(session)
    if redis_client is None:
        raise RuntimeError("PG_CUTOVER is off but no Redis client was provided")
    return RedisDeckLinksRepository(redis_client)
