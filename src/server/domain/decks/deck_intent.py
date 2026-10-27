"""Deck-local intent (ARCHITECTURE.md §4.2, STA-9 discovery §2, STA-11).

One owner-stated `UserDeckIntent` payload per deck, persisted in Postgres
(the only source of truth — AGENT_RULES rule 1). This module owns the rules;
persistence lives in `infra/db/repositories.py`.

Rules (discovery source-of-truth contract items 8-9, SRC-07, AT-06):

- Stated-only storage: only what the owner actually wrote is stored. Defaults
  are never backfilled into the stored record, so an unstated field stays
  visibly empty and an unstated deck stays `unstated` — it never inherits
  another deck's taste or the general player profile.
- Deck-local: one record per deck id. There is no cross-deck read, merge, or
  inference anywhere in this module, so two decks' stated goals cannot bleed
  into each other by construction.
- Provenance: everything written through this path is owner-stated
  (`provenance="stated"`). Imported text or inferred suggestions must be
  labeled separately and must never be stored through this path.
- Validation reuses the canonical `user_intent.validate_intent` business
  rules (strict_coherence off: this is a statement of direction, not a
  build-order feasibility check).

Pure domain: no FastAPI, no SQLAlchemy, no infra imports (rule 2).
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Mapping, Optional

from src.server.domain.intake import user_intent

PROVENANCE_STATED = "stated"

# Top-level UserDeckIntent sections an owner may state for a deck. Mirrors the
# known keys in user_intent.resolve_intent_defaults; "metadata" stays a
# catch-all so no owner input is ever lost.
SECTION_KEYS = (
    "deck_vision",
    "target_power_level",
    "budget_constraints",
    "preferred_win_conditions",
    "archetype_preferences",
    "deck_genre",
    "pet_cards",
    "novelties",
    "target_bracket",
    "freeform_notes",
    "metadata",
)


class DeckIntentError(ValueError):
    """Raised on any intent-rule violation; carries the full error list."""

    def __init__(self, message: str, errors: Optional[List[str]] = None):
        super().__init__(message)
        self.errors = errors or [message]


def is_unstated(payload: Optional[Mapping[str, Any]]) -> bool:
    """True when the owner has stated nothing for the deck. An empty record
    is a valid, honest state (rule 4): it renders as `unstated`, never as a
    defaulted profile."""
    if not payload:
        return True
    return all(
        v in (None, "", [], {}) for k, v in payload.items() if k != "metadata"
    ) and not (payload.get("metadata") or {})


def validate_stated(payload: Mapping[str, Any], r: Any = None) -> Dict[str, Any]:
    """Validate a stated intent payload and return a sparse normalized copy.

    Only known sections (plus `metadata` for anything else) are kept; missing
    sections stay absent — no default backfill. Raises DeckIntentError with
    the user_intent error list on invalid input.
    """
    if not isinstance(payload, Mapping):
        raise DeckIntentError("Intent must be a JSON object (dict).")
    ok, errors = user_intent.validate_intent(
        dict(payload), strict_coherence=False, r=r
    )
    if not ok:
        raise DeckIntentError("Invalid deck intent: " + "; ".join(errors), errors)
    out: Dict[str, Any] = {}
    extra: Dict[str, Any] = {}
    for key, value in payload.items():
        if key in SECTION_KEYS and key != "metadata":
            out[key] = copy.deepcopy(value)
        elif key != "metadata":
            extra[key] = copy.deepcopy(value)
    metadata = dict(payload.get("metadata") or {})
    metadata.update(extra)
    if metadata:
        out["metadata"] = metadata
    return out


def merge_sections(
    stored: Optional[Mapping[str, Any]], patch: Mapping[str, Any], r: Any = None
) -> Dict[str, Any]:
    """Merge whole top-level sections of `patch` into the stored payload.

    A section present in `patch` replaces the stored section; a section set to
    None is removed (the owner un-states it). Unknown top-level keys land in
    `metadata`, never dropped. The merged result is re-validated.
    """
    if not isinstance(patch, Mapping):
        raise DeckIntentError("Intent patch must be a JSON object (dict).")
    merged: Dict[str, Any] = copy.deepcopy(dict(stored or {}))
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = copy.deepcopy(value)
    return validate_stated(merged, r=r)
