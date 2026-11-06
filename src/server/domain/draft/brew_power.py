"""Brew power-level targeting: request parsing, card signals, scoring term, estimate.

Signals per card: fast_mana, tutor, combo, game_changer, mana value. All weights,
pivots and thresholds live in brew_scoring_config.PowerConfig (PLACEHOLDER values
until the oracle spec lands). This module imports no scoring code so
brew_scoring can use `power_term` as a core component without a cycle.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from src.server.domain.draft.brew_scoring_config import (
    COMBO_PIECE_NAMES, FAST_MANA_NAMES, BrewScoringConfig, PowerConfig,
)

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "analytics", "data")
SIGNALS = ("fast_mana", "tutor", "combo", "game_changer")


def _key(name: Any) -> str:
    return str(name or "").replace(" // ", " / ").split(" / ")[0].strip().lower()


@lru_cache(maxsize=None)
def _load_names(filename: str, field: str) -> frozenset:
    try:
        with open(os.path.join(_DATA, filename), encoding="utf-8") as f:
            return frozenset(_key(n) for n in json.load(f).get(field, []))
    except (OSError, ValueError):
        return frozenset()


def game_changer_names() -> frozenset:
    """Checked-in official Game Changers snapshot (offline-safe)."""
    return _load_names("game_changers.json", "cards")


def tutor_names() -> frozenset:
    return _load_names("bracket_cards.json", "tutors")


# --------------------------------------------------------------------------
# Request parsing
# --------------------------------------------------------------------------

def parse_power_level(value: Any, cfg: Optional[PowerConfig] = None) -> float:
    """Requested power -> float in [min_level, max_level]. Absent -> default.

    Accepts the aliases in cfg.aliases (low/mid/high) or a number / numeric string.
    Raises ValueError when out of range or unparseable.
    """
    cfg = cfg or PowerConfig()
    if value is None or (isinstance(value, str) and not value.strip()):
        return float(cfg.default_level)
    if isinstance(value, bool):
        raise ValueError("power_level must be a number or one of " + ", ".join(sorted(cfg.aliases)))
    if isinstance(value, str):
        s = value.strip().lower()
        if s in cfg.aliases:
            return float(cfg.aliases[s])
        try:
            num = float(s)
        except ValueError:
            raise ValueError(
                f"power_level must be a number {cfg.min_level:g}-{cfg.max_level:g} or one of "
                f"{sorted(cfg.aliases)} (got {value!r})") from None
    elif isinstance(value, (int, float)):
        num = float(value)
    else:
        raise ValueError(f"power_level must be a number or string (got {type(value).__name__})")
    if not (cfg.min_level <= num <= cfg.max_level) or num != num:
        raise ValueError(f"power_level must be between {cfg.min_level:g} and {cfg.max_level:g} (got {value!r})")
    return num


# --------------------------------------------------------------------------
# Signal classification
# --------------------------------------------------------------------------

def _tags(card: Mapping[str, Any]) -> str:
    return " ".join(str(t).lower().replace("_", " ").replace("-", " ") for t in card.get("tags") or [])


def mana_value(card: Mapping[str, Any]) -> Optional[float]:
    v = card.get("cmc", card.get("mana_value"))
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def is_land(card: Mapping[str, Any]) -> bool:
    return "land" in str(card.get("type_line") or card.get("type") or "").lower()


def card_signals(card: Mapping[str, Any], game_changers: Optional[Iterable[str]] = None) -> Dict[str, bool]:
    """Boolean power signals for one card, from name lists, tags and oracle text."""
    k = _key(card.get("name"))
    tags = _tags(card)
    text = str(card.get("oracle_text") or "").lower()
    tl = str(card.get("type_line") or card.get("type") or "").lower()
    mv = mana_value(card)
    gcs = game_changer_names() if game_changers is None else frozenset(_key(n) for n in game_changers)

    fast = k in FAST_MANA_NAMES or "fast mana" in tags
    if not fast and "artifact" in tl and mv is not None and mv <= 1 and re.search(r"add \{|adds? .*mana", text):
        fast = True
    tutor = (k in tutor_names() or "tutor" in tags
             or bool(re.search(r"search your library for a(?:n)? (?!basic land|land)\w*[^.]*card", text)
                     and "land" not in tl and (mv is None or mv <= 3)))
    combo = k in COMBO_PIECE_NAMES or "combo" in tags or "infinite" in tags
    return {"fast_mana": fast, "tutor": tutor, "combo": combo, "game_changer": bool(k) and k in gcs}


# --------------------------------------------------------------------------
# Additive scoring term
# --------------------------------------------------------------------------

def _norm(power: float, p: PowerConfig) -> float:
    return (power - p.min_level) / (p.max_level - p.min_level)


def target_avg_mv(power: float, p: PowerConfig) -> float:
    x = _norm(power, p)
    return p.curve_avg_low + (p.curve_avg_high - p.curve_avg_low) * x


def power_components(card: Mapping[str, Any], ctx: Any, cfg: BrewScoringConfig) -> Dict[str, float]:
    """Per-signal contributions (+ curve) for a card; {} when no target power is set."""
    target = getattr(ctx, "target_power", None)
    if target is None:
        return {}
    p = cfg.power
    x = _norm(target, p)
    sig = card_signals(card, getattr(ctx, "game_changers", None))
    out: Dict[str, float] = {}
    for name, w, pivot in (("fast_mana", p.w_fast_mana, p.pivot_fast_mana), ("tutor", p.w_tutor, p.pivot_tutor),
                           ("combo", p.w_combo, p.pivot_combo),
                           ("game_changer", p.w_game_changer, p.pivot_game_changer)):
        if sig[name]:
            out[name] = w * (x - pivot) / max(pivot, 1 - pivot)
    mv = mana_value(card)
    if mv is not None and not is_land(card) and p.w_curve:
        n = int(getattr(ctx, "mv_n", 0))
        new_avg = (float(getattr(ctx, "mv_sum", 0.0)) + mv) / (n + 1)
        dist = abs(new_avg - target_avg_mv(target, p))
        out["curve"] = p.w_curve * (1 - 2 * min(1.0, dist / p.curve_tolerance))
    return out


def power_term(card: Mapping[str, Any], ctx: Any, cfg: BrewScoringConfig) -> float:
    return sum(power_components(card, ctx, cfg).values())


# --------------------------------------------------------------------------
# Deck estimate
# --------------------------------------------------------------------------

def estimate_power_level(deck: Any, config: Optional[BrewScoringConfig] = None,
                         game_changers: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Estimate deck power (1-10) from signal counts and the average curve.

    `deck` is a list of card dicts or a draft dict with `cards`. Card dicts
    should carry tags/oracle_text/type_line/cmc where known; bare names still
    match the name lists. Returns {"power": float, "breakdown": {...}}.
    """
    p = (config or BrewScoringConfig()).power
    cards: Sequence[Mapping[str, Any]] = (deck.get("cards") or []) if isinstance(deck, Mapping) else list(deck or [])
    counts = {s: 0 for s in SIGNALS}
    mvs = []
    for c in cards:
        q = c.get("quantity", 1)
        qty = q if isinstance(q, int) and not isinstance(q, bool) and q >= 0 else 1
        if qty == 0:
            continue
        sig = card_signals(c, game_changers)
        for s in SIGNALS:
            if sig[s]:
                counts[s] += qty
        mv = mana_value(c)
        if mv is not None and not is_land(c):
            mvs.extend([mv] * qty)
    avg = sum(mvs) / len(mvs) if mvs else None
    caps = {"fast_mana": p.est_fast_mana_cap, "tutor": p.est_tutor_cap,
            "combo": p.est_combo_cap, "game_changer": p.est_game_changer_cap}
    per = {"fast_mana": p.est_fast_mana, "tutor": p.est_tutor, "combo": p.est_combo,
           "game_changer": p.est_game_changer}
    contrib = {s: round(min(counts[s], caps[s]) * per[s], 3) for s in SIGNALS}
    curve = 0.0
    if avg is not None:
        curve = max(-p.est_curve_cap, min(p.est_curve_cap, (p.est_curve_ref_avg - avg) * p.est_curve_per_mv))
    raw = p.est_base + sum(contrib.values()) + curve
    power = round(max(p.min_level, min(p.max_level, raw)), 2)
    return {"power": power, "breakdown": {
        "base": p.est_base, "counts": counts, "contributions": contrib,
        "avg_mana_value": None if avg is None else round(avg, 3),
        "curve": round(curve, 3), "raw": round(raw, 3),
    }}
