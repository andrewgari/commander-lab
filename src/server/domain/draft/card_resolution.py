"""Single card-name resolution point for the Brew pipeline.

Contract
--------
``resolve_card(value, r)`` returns a ``ResolvedCard`` whose ``name`` is a real
card name, or ``None`` when the value cannot be resolved. Callers MUST drop a
``None`` result from their candidate list (and record it in a
``ResolutionReport``); an unresolved value is never emitted to output.

* A UUID-shaped value (Scryfall oracle_id / instance id) is looked up in the
  ``card:{oracle_id}`` catalog record. No record / no name -> ``None``.
* Any other non-blank string is taken to already be a card name.
* A *name* that is UUID-shaped is never accepted as a name.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Tuple

import src.server.domain.inventory.cards as card_store

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedCard:
    name: str
    oracle_id: Optional[str] = None


@dataclass
class ResolutionReport:
    """Dropped raw values plus how many deck slots they leave empty."""

    dropped: List[str] = field(default_factory=list)

    def drop(self, value: Any) -> None:
        if str(value) in self.dropped:
            return
        self.dropped.append(str(value))
        log.warning("brew: dropped unresolvable card id/name %r", value)

    @property
    def missing_slots(self) -> int:
        """Hook for a downstream fill step: slots to replace."""
        return len(self.dropped)


def looks_like_id(value: Any) -> bool:
    return isinstance(value, str) and card_store.is_oracle_id(value.strip())


def _catalog_name(r: Any, oracle_id: str) -> Optional[str]:
    if r is None:
        return None
    try:
        raw = r.get(f"card:{oracle_id}")
        rec = json.loads(raw) if raw else None
    except Exception:  # redis down / corrupt record: unresolved, not a crash
        return None
    name = rec.get("name") if isinstance(rec, dict) else None
    if isinstance(name, str) and name.strip() and not looks_like_id(name):
        return name.strip()
    return None


def resolve_card(value: Any, r: Any = None) -> Optional[ResolvedCard]:
    """Resolve an id-or-name to a real card name, or None (see module doc)."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if looks_like_id(value):
        name = _catalog_name(r, value)
        return ResolvedCard(name=name, oracle_id=value) if name else None
    return ResolvedCard(name=value)


def resolve_all(values: Iterable[Any], r: Any = None) -> Tuple[List[ResolvedCard], ResolutionReport]:
    report = ResolutionReport()
    out: List[ResolvedCard] = []
    for v in values:
        rc = resolve_card(v, r)
        if rc is None:
            report.drop(v)
        else:
            out.append(rc)
    return out, report
