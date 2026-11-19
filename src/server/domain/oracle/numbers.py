"""Numbers guard input: every number a tool returned is allowed in the prose."""
from __future__ import annotations

from typing import Iterable, Mapping, Optional, Set

from ..analytics.review_doc import allowed_numbers
from .types import ToolOutcome


def allowed_numbers_from(outputs: Iterable[ToolOutcome], extra: Optional[Mapping] = None) -> Set[str]:
    return allowed_numbers({"tools": [o.result for o in outputs if o.ok], **dict(extra or {})})
