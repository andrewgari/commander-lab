"""ToolContext: injected deps; the only way oracle tools reach data (spec §2.4)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence


@dataclass
class ToolContext:
    deck: Mapping[str, Any]
    journal: Optional[Mapping[str, Any]] = None
    metrics: Optional[Mapping[str, Any]] = None
    game_changers: Optional[Sequence[str]] = None
    scryfall_batch: Optional[Callable[[list], dict]] = None
    compute_metrics: Optional[Callable[[Mapping], Mapping]] = None
    bracket_eval: Optional[Callable[[Mapping[str, Any]], Any]] = None  # -> bracket_eval.BracketEvaluation (live inputs)
    sim: Optional[Any] = None  # SimRunner (OL-4); None = sim_run not offered
    allow_hypothetical: bool = False
    memo: dict = field(default_factory=dict)
