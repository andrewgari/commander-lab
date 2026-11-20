"""Combine merit signals (spec t_d29ed3c9 §3). Pure, no I/O."""
from __future__ import annotations

from typing import Mapping, Optional

KEYS = ("I", "S", "L", "C", "R")
# Defaults until MERIT-4 replaces them with calibrated values (pass ``weights=``).
DEFAULT_WEIGHTS: Mapping[str, float] = {"I": 0.30, "S": 0.25, "L": 0.20, "C": 0.10, "R": 0.15}
E_SHARE = 0.65              # E's share of strength
ROLE_CONFIDENCE_W = 0.20    # role_confidence + role_need keep their current 0.35
ROLE_NEED_W = 0.15


def merit_e(signals: Mapping[str, Optional[float]],
            weights: Optional[Mapping[str, float]] = None) -> Optional[float]:
    """E = weighted mean over {I,S,L,C,R}. Missing/None or zero-weight signals are
    dropped and the rest re-normalised. Returns None if nothing is left."""
    w = DEFAULT_WEIGHTS if weights is None else weights
    num = den = 0.0
    for k in KEYS:
        v = signals.get(k)
        wk = w.get(k, 0.0)
        if v is None or wk <= 0:
            continue
        num += wk * min(1.0, max(0.0, v))
        den += wk
    return num / den if den else None


def strength(e: Optional[float], role_confidence: Optional[float] = None,
             role_need: Optional[float] = None) -> float:
    """0..100. E takes 0.65; role_confidence 0.20 + role_need 0.15; missing parts
    are dropped and the rest re-normalised. Nothing at all -> 50.0."""
    parts = ((E_SHARE, e), (ROLE_CONFIDENCE_W, role_confidence), (ROLE_NEED_W, role_need))
    live = [(w, v) for w, v in parts if v is not None]
    total = sum(w for w, _ in live)
    if not total:
        return 50.0
    return round(100.0 * sum(w * v for w, v in live) / total, 1)
