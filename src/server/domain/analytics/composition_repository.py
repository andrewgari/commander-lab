"""Repository-interface adapter over the composition preset store.

Route handlers (via composition_service) talk to ``Repository`` methods instead
of calling the Redis client / store functions directly. Redis stays the default
backend; ``analytics.composition_pg_store`` shares the same function API, so any
module exposing it can be passed as ``store``.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from src.server.domain.analytics import composition_store
from src.server.domain.analytics.composition_models import CompositionValidationError
from src.server.domain.analytics.repository_interfaces import NotFoundError, Repository


class CompositionPresetRepository(Repository[dict, str]):
    def __init__(self, client: Any, store: Any = composition_store):
        self._r = client
        self._store = store

    def create(self, entity: dict) -> dict:
        return self._store.create_preset(self._r, entity)

    def get(self, entity_id: str) -> Optional[dict]:
        return self._store.get_preset(self._r, entity_id)

    def list(self) -> Sequence[dict]:
        return self._store.list_presets(self._r)

    def update(self, entity_id: str, entity: dict) -> dict:
        try:
            return self._store.update_preset(self._r, entity_id, entity)
        except CompositionValidationError:
            raise
        except ValueError as exc:
            raise NotFoundError(entity_id) from exc

    def delete(self, entity_id: str) -> bool:
        return bool(self._store.delete_preset(self._r, entity_id))


def get_preset_repository(client: Any) -> CompositionPresetRepository:
    return CompositionPresetRepository(client)
