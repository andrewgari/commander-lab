"""Brew card-scoring configuration: every scoring weight and scaling knob lives here.

Override at runtime with BREW_SCORING_WEIGHTS, a JSON object whose keys are any
scalar BrewScoringConfig field (e.g. '{"category_need": 3.0}').

PLACEHOLDERS: the oracle spec task has not published final weights or a
skeleton in the repo yet. Values marked PLACEHOLDER are provisional defaults
and should be replaced when the spec lands; nothing else in the code needs to
change. `power_level` and `source_bias` are intentionally inert (empty/0.0):
later tasks fill them in through the additive-term hooks in brew_scoring.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from typing import Dict, Mapping, Optional


@dataclass(frozen=True)
class SkeletonRange:
    """Target card count for one category: [low, high] inclusive."""

    low: int
    high: int


# PLACEHOLDER skeleton (typical 99-card Commander shape); replace with the oracle spec.
DEFAULT_SKELETON: Mapping[str, SkeletonRange] = {
    "ramp": SkeletonRange(8, 12),
    "draw": SkeletonRange(8, 12),
    "removal": SkeletonRange(5, 9),
    "wipe": SkeletonRange(1, 4),
}


@dataclass(frozen=True)
class BrewScoringConfig:
    # --- component weights (PLACEHOLDER values) ---
    commander_synergy: float = 1.0
    category_need: float = 2.0
    edhrec_inclusion: float = 1.0
    edhrec_synergy: float = 0.5

    # --- scaling (each signal is normalised to 0..1 before its weight) ---
    inclusion_full: float = 0.60    # EDHREC inclusion at/above this -> 1.0
    synergy_floor: float = -0.25    # EDHREC synergy at/below this -> 0.0
    synergy_ceil: float = 0.50      # EDHREC synergy at/above this -> 1.0
    synergy_saturation: int = 3     # theme hits needed for full commander synergy
    over_range_penalty: float = 1.0  # need per card above the range top (x category_need)

    # --- placeholder blocks filled by later tasks ---
    source_bias: float = 0.0        # weight of the owned/deck-source bias term
    power_level: Mapping[str, float] = field(default_factory=dict)  # signal -> weight

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "BrewScoringConfig":
        env = os.environ if env is None else env
        raw = env.get("BREW_SCORING_WEIGHTS")
        if not raw:
            return cls()
        try:
            data = json.loads(raw)
        except ValueError:
            return cls()
        if not isinstance(data, dict):
            return cls()
        scalar = {f.name for f in fields(cls) if f.name != "power_level"}
        clean = {k: v for k, v in data.items()
                 if k in scalar and isinstance(v, (int, float)) and not isinstance(v, bool)}
        if isinstance(data.get("power_level"), dict):
            clean["power_level"] = {str(k): float(v) for k, v in data["power_level"].items()
                                    if isinstance(v, (int, float))}
        return cls(**clean)

    def skeleton(self) -> Dict[str, SkeletonRange]:
        return dict(DEFAULT_SKELETON)
