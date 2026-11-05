"""
Abstract repository and cache interfaces.

Pure contracts: no Redis/FastAPI imports, no side effects on import, so any
module can depend on them without circular-import risk. Concrete backends
(e.g. the Redis-backed stores such as ``analytics.composition_store``) can
implement these in follow-up work; nothing here is wired into existing code.

Design decisions
- ``abc.ABC`` (explicit, enforced at instantiation) rather than ``Protocol``.
- Synchronous, matching the existing sync ``redis.Redis`` stores.
- Misses are values, not exceptions: ``get`` returns ``None``; ``delete``
  returns ``False``. ``update`` raises ``NotFoundError`` on a missing id and
  ``create`` raises ``ConflictError`` on a duplicate id, so backends never
  leak driver-specific exceptions.
- TTLs are whole seconds; ``None`` means "no expiry".
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, Optional, Sequence, TypeVar

EntityT = TypeVar("EntityT")
IdT = TypeVar("IdT")
ValueT = TypeVar("ValueT")


class RepositoryError(Exception):
    """Base class for repository errors."""


class NotFoundError(RepositoryError):
    """The requested entity does not exist."""


class ConflictError(RepositoryError):
    """An entity with the same identity already exists."""


class Repository(ABC, Generic[EntityT, IdT]):
    """CRUD access to a single kind of persisted entity."""

    @abstractmethod
    def create(self, entity: EntityT) -> EntityT:
        """Persist a new entity and return the stored value.

        Raises ConflictError if an entity with the same id already exists.
        """

    @abstractmethod
    def get(self, entity_id: IdT) -> Optional[EntityT]:
        """Return the entity with this id, or None if absent."""

    @abstractmethod
    def list(self) -> Sequence[EntityT]:
        """Return all stored entities (order is implementation-defined)."""

    @abstractmethod
    def update(self, entity_id: IdT, entity: EntityT) -> EntityT:
        """Replace the entity stored under ``entity_id``; return the new value.

        Raises NotFoundError if no such entity exists.
        """

    @abstractmethod
    def delete(self, entity_id: IdT) -> bool:
        """Remove the entity. Return True if removed, False if it was absent.

        Idempotent: deleting a missing id is not an error.
        """


class Cache(ABC, Generic[ValueT]):
    """Key/value cache with optional expiry."""

    @abstractmethod
    def get(self, key: str) -> Optional[ValueT]:
        """Return the cached value, or None on a miss or expired entry."""

    @abstractmethod
    def put(self, key: str, value: ValueT, ttl_seconds: Optional[int] = None) -> None:
        """Store ``value`` under ``key``, overwriting any existing value.

        ``ttl_seconds`` is the lifetime in whole seconds; None means no expiry.
        """

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Evict ``key``. Return True if it was present, False otherwise."""
