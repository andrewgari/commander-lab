"""Per-card merit signals (spec t_d29ed3c9 §3). Pure functions, no I/O.

Each signal is in [0, 1] or ``None`` when its input is missing (``combine`` drops
and re-normalises). Commander Salt data is not used anywhere in this module.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Collection, Iterable, Mapping, Optional, Sequence

PRIOR_A = 50.0  # pseudo-count for the bracket-lift shrinkage prior
# Fallback until MERIT-4 fits them. Neutral on purpose: B (banned) scores 0.
DEFAULT_COMBO_TAG_WEIGHTS: Mapping[str, float] = {
    "E": 1.0, "C": 1.0, "O": 1.0, "P": 1.0, "S": 1.0, "R": 1.0, "B": 0.0,
}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1.0 - 1e-6)
    return math.log(p / (1.0 - p))


@dataclass(frozen=True)
class Signal:
    """A signal value plus whether it relied on a censored (imputed) count."""
    value: Optional[float]
    censored: bool = False


def inclusion(value: Optional[float], full: float = 0.60) -> Optional[float]:
    """c_I: EDHREC inclusion fraction scaled so ``full`` and above -> 1."""
    if value is None or full <= 0:
        return None
    return _clamp01(value / full)


def synergy(value: Optional[float], floor: float = -0.25, ceil: float = 0.50) -> Optional[float]:
    """c_S: EDHREC synergy scaled floor -> 0, ceil -> 1."""
    if value is None or ceil <= floor:
        return None
    return _clamp01((value - floor) / (ceil - floor))


def global_rank(rank: Optional[int]) -> Optional[float]:
    """c_R: Scryfall edhrec_rank, 1 -> 1.0, 25000 -> 0.0 (log scale)."""
    if rank is None or rank <= 0:
        return None
    return _clamp01(1.0 - math.log10(rank) / math.log10(25000))


@dataclass(frozen=True)
class BracketPage:
    """One EDHREC bracket page: ``n`` decks with the card (None = not listed),
    ``total`` decks on the page, ``min_listed`` lowest inclusion fraction listed."""
    n: Optional[float]
    total: float
    min_listed: Optional[float] = None


def _side(pages: Iterable[BracketPage]):
    """Return (n, N, imputed, any_present) for one side. None n => (None) unusable."""
    n = total = 0.0
    imputed = False
    present = False
    for p in pages:
        if p.total <= 0:
            continue
        total += p.total
        if p.n is not None:
            n += p.n
            present = True
        elif p.min_listed is not None:
            n += 0.5 * p.min_listed * p.total
            imputed = True
        else:
            return None
    if total <= 0:
        return None
    return n, total, imputed, present


def bracket_lift(hi: Sequence[BracketPage], lo: Sequence[BracketPage], p0: Optional[float],
                 a: float = PRIOR_A) -> Signal:
    """c_L: logit(p_hi) - logit(p_lo), shrunk toward base inclusion ``p0``.

    hi = optimized + cedh pages, lo = exhibition + core + upgraded pages. A page
    where the card is unlisted is censored: n = 0.5 * lowest listed inclusion * N
    and the result is flagged. If the card is absent on every page of both sides
    the signal is missing.
    """
    if p0 is None:
        return Signal(None)
    h, l = _side(hi), _side(lo)
    if h is None or l is None:
        return Signal(None)
    if not h[3] and not l[3]:
        return Signal(None)
    p_hi = (h[0] + a * p0) / (h[1] + a)
    p_lo = (l[0] + a * p0) / (l[1] + a)
    lift = _logit(p_hi) - _logit(p_lo)
    return Signal(_clamp01(0.5 + lift / 4.0), censored=h[2] or l[2])


def combo(card: str, deck: Collection[str], variants: Optional[Iterable[tuple]],
          p99_popularity: float, tag_weights: Optional[Mapping[str, float]] = None) -> Optional[float]:
    """c_C: best complete combo this card is a piece of.

    ``variants`` is an iterable of ``(pieces, bracket_tag, popularity)`` where
    ``pieces`` includes the commander. ``deck`` must include the commander. A
    combo counts only when every piece is in the deck (one-away adds nothing).
    ``variants=None`` (no Spellbook data) -> missing; no complete combo -> 0.0.
    """
    if variants is None:
        return None
    tw = DEFAULT_COMBO_TAG_WEIGHTS if tag_weights is None else tag_weights
    denom = math.log1p(max(p99_popularity, 0.0))
    if denom <= 0:
        return None  # normalisation unavailable: drop the signal, don't score it 0
    deck_set = set(deck)
    best = 0.0
    for pieces, tag, popularity in variants:
        pieces = set(pieces)
        if card not in pieces or not pieces <= deck_set:
            continue
        v = tw.get(tag, 0.0) * math.log1p(max(popularity or 0.0, 0.0)) / denom
        best = max(best, v)
    return _clamp01(best)
