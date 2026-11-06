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


# Curated fast-mana names (lowercase front face). Heuristic + tags add to this.
FAST_MANA_NAMES = frozenset(n.lower() for n in (
    "Sol Ring", "Mana Crypt", "Mana Vault", "Black Lotus", "Chrome Mox", "Mox Diamond",
    "Mox Opal", "Mox Amber", "Jeweled Lotus", "Lotus Petal", "Grim Monolith", "Basalt Monolith",
    "Ancient Tomb", "Lion's Eye Diamond", "Simian Spirit Guide", "Elvish Spirit Guide",
    "Mox Tantalite", "Lotus Bloom", "Dockside Extortionist",
))

# Curated infinite/win-combo pieces (lowercase). Tags containing "combo" also count.
COMBO_PIECE_NAMES = frozenset(n.lower() for n in (
    "Thassa's Oracle", "Demonic Consultation", "Tainted Pact", "Laboratory Maniac",
    "Jace, Wielder of Mysteries", "Isochron Scepter", "Dramatic Reversal", "Splinter Twin",
    "Kiki-Jiki, Mirror Breaker", "Pestermite", "Deceiver Exarch", "Zealous Conscripts",
    "Heliod, Sun-Crowned", "Walking Ballista", "Underworld Breach", "Brain Freeze",
    "Food Chain", "Exquisite Blood", "Sanguine Bond", "Niv-Mizzet, Parun", "Ad Nauseam",
))


@dataclass(frozen=True)
class PowerConfig:
    """Power-level targeting + estimation knobs (PLACEHOLDER values until the oracle spec lands).

    Requested power is 1..10 (low/mid/high alias to the values in `aliases`).
    Per-signal targeting: term = weight * (x - pivot) / max(pivot, 1 - pivot), where
    x = (target - 1) / 9. A signal is welcomed above its pivot and discouraged below it.
    """

    min_level: float = 1.0
    max_level: float = 10.0
    default_level: float = 6.0
    aliases: Mapping[str, float] = field(default_factory=lambda: {"low": 3.0, "mid": 6.0, "high": 8.5})

    # --- per-card targeting weights (max +/- contribution) ---
    w_fast_mana: float = 2.0
    w_tutor: float = 1.5
    w_combo: float = 1.5
    w_game_changer: float = 1.5
    # pivot: normalised power (0..1) where the signal turns from penalty to reward
    pivot_fast_mana: float = 0.6
    pivot_tutor: float = 0.55
    pivot_combo: float = 0.65
    pivot_game_changer: float = 0.5

    # --- curve: target average mana value falls linearly as power rises ---
    w_curve: float = 0.5
    curve_avg_low: float = 3.4      # target avg MV at min power
    curve_avg_high: float = 2.6     # target avg MV at max power
    curve_tolerance: float = 3.0    # MV distance for the full curve penalty

    # --- estimate_power_level ---
    est_base: float = 3.0
    est_fast_mana: float = 0.6
    est_fast_mana_cap: int = 4
    est_tutor: float = 0.3
    est_tutor_cap: int = 5
    est_combo: float = 0.7
    est_combo_cap: int = 3
    est_game_changer: float = 0.6
    est_game_changer_cap: int = 5
    est_curve_per_mv: float = 1.5   # points per MV below the reference average (clamped +/- est_curve_cap)
    est_curve_ref_avg: float = 3.0
    est_curve_cap: float = 1.0


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
    source_bias: float = 0.25       # fraction of the median candidate score gap granted to source cards; 0 = off
    source_bias_max: float = 0.5    # hard ceiling on that fraction
    source_unit_fallback: float = 0.01  # gap unit when tied / standalone (tie-break only)
    power_level: Mapping[str, float] = field(default_factory=dict)  # legacy/inert; see `power`
    power: PowerConfig = field(default_factory=PowerConfig)

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
        scalar = {f.name for f in fields(cls) if f.name not in ("power_level", "power")}
        clean = {k: v for k, v in data.items()
                 if k in scalar and isinstance(v, (int, float)) and not isinstance(v, bool)}
        if isinstance(data.get("power_level"), dict):
            clean["power_level"] = {str(k): float(v) for k, v in data["power_level"].items()
                                    if isinstance(v, (int, float))}
        pw = data.get("power")
        if isinstance(pw, dict):
            allowed = {f.name: f.type for f in fields(PowerConfig) if f.name != "aliases"}
            pclean = {k: v for k, v in pw.items() if k in allowed and isinstance(v, (int, float))
                      and not isinstance(v, bool)}
            for k in [k for k in pclean if k.endswith("_cap") and k.startswith("est_")]:
                pclean[k] = int(pclean[k]) if k != "est_curve_cap" else float(pclean[k])
            if pclean.get("curve_tolerance", 1) <= 0:
                del pclean["curve_tolerance"]
            lo = pclean.get("min_level", PowerConfig.min_level)
            hi = pclean.get("max_level", PowerConfig.max_level)
            if hi <= lo:
                pclean.pop("min_level", None)
                pclean.pop("max_level", None)
            clean["power"] = PowerConfig(**pclean)
        # Reject overrides that would make a normalisation divide by zero / invert.
        if "inclusion_full" in clean and clean["inclusion_full"] <= 0:
            del clean["inclusion_full"]
        floor = clean.get("synergy_floor", cls.synergy_floor)
        ceil = clean.get("synergy_ceil", cls.synergy_ceil)
        if ceil <= floor:
            clean.pop("synergy_floor", None)
            clean.pop("synergy_ceil", None)
        if clean.get("synergy_saturation", 1) < 1:
            del clean["synergy_saturation"]
        return cls(**clean)

    def skeleton(self) -> Dict[str, SkeletonRange]:
        return dict(DEFAULT_SKELETON)
