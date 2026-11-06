"""Composable Brew card scoring.

score_card(card, context, config) -> ScoreResult(total, components).

Core components: commander_synergy, category_need, edhrec_inclusion,
edhrec_synergy. Further terms (power level, source bias) plug in through
`register_term` / the `extra_terms` argument without touching this module:
a term is `fn(card, context, config) -> float`, added to the total and shown in
the breakdown. All weights come from BrewScoringConfig.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.server.domain.draft import brew_power
from src.server.domain.draft.brew_scoring_config import BrewScoringConfig, SkeletonRange

Term = Callable[[Mapping[str, Any], "ScoreContext", BrewScoringConfig], float]

_TERMS: Dict[str, Term] = {}


def register_term(name: str, fn: Term) -> None:
    _TERMS[name] = fn


def unregister_term(name: str) -> None:
    _TERMS.pop(name, None)


def clear_terms() -> None:
    _TERMS.clear()


@dataclass
class ScoreContext:
    commander: str = ""
    themes: Sequence[str] = ()          # keywords describing what the commander/deck wants
    deck_counts: Mapping[str, int] = field(default_factory=dict)  # category -> cards already chosen
    skeleton: Mapping[str, SkeletonRange] = field(default_factory=dict)
    edhrec: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)  # lowercased name -> {inclusion, synergy}
    target_power: Optional[float] = None   # requested power 1-10; None disables the power term
    mv_sum: float = 0.0                    # mana value total of chosen non-land cards
    mv_n: int = 0                          # count of chosen non-land cards with a known MV
    game_changers: Optional[Sequence[str]] = None  # override of the checked-in snapshot


@dataclass
class ScoreResult:
    total: float
    components: Dict[str, float]


def _name_key(name: str) -> str:
    return (name or "").split(" // ")[0].strip().lower()


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def card_categories(card: Mapping[str, Any]) -> List[str]:
    """Skeleton categories a card belongs to: explicit `category`, or its tags (substring match)."""
    cats: List[str] = []
    if card.get("category"):
        cats.append(str(card["category"]).lower())
    cats.extend(str(t).lower() for t in card.get("tags") or [])
    return cats


def _matching(card: Mapping[str, Any], skeleton: Mapping[str, SkeletonRange]) -> List[str]:
    cats = card_categories(card)
    return [k for k in skeleton if any(k == c or k in c for c in cats)]


def count_categories(cards: Iterable[Mapping[str, Any]], skeleton: Optional[Mapping[str, SkeletonRange]] = None) -> Dict[str, int]:
    """Cards per category. With a skeleton, counts only its categories (a card counts once per match)."""
    counts: Dict[str, int] = {}
    for c in cards:
        keys = _matching(c, skeleton) if skeleton is not None else card_categories(c)
        for k in set(keys):
            counts[k] = counts.get(k, 0) + 1
    return counts


def _need(count: int, rng: SkeletonRange, cfg: BrewScoringConfig) -> float:
    """1.0 at/below the range floor, decaying linearly to 0 at the top, negative beyond."""
    if count >= rng.high:
        return -cfg.over_range_penalty * (count - rng.high + 1)
    if count <= rng.low:
        return 1.0
    return (rng.high - count) / float(rng.high - rng.low)


def _commander_synergy(card, ctx, cfg) -> float:
    hay = " ".join(str(card.get(k) or "") for k in ("oracle_text", "type_line", "name"))
    hay = (hay + " " + " ".join(card_categories(card))).lower()
    hits = sum(1 for t in ctx.themes if t and re.search(r"\b" + re.escape(t.lower()), hay))
    return cfg.commander_synergy * _clamp01(hits / float(max(1, cfg.synergy_saturation)))


def _category_need(card, ctx, cfg) -> float:
    keys = _matching(card, ctx.skeleton)
    if not keys:
        return 0.0
    return cfg.category_need * max(_need(int(ctx.deck_counts.get(k, 0)), ctx.skeleton[k], cfg) for k in keys)


def _edhrec(card, ctx) -> Tuple[Optional[float], Optional[float]]:
    e = ctx.edhrec.get(_name_key(card.get("name", "")))
    if not e:
        return None, None
    return e.get("inclusion"), e.get("synergy")


def _edhrec_inclusion(card, ctx, cfg) -> float:
    inc, _ = _edhrec(card, ctx)
    if not isinstance(inc, (int, float)):
        return 0.0
    return cfg.edhrec_inclusion * _clamp01(inc / cfg.inclusion_full)


def _edhrec_synergy(card, ctx, cfg) -> float:
    _, syn = _edhrec(card, ctx)
    if not isinstance(syn, (int, float)):
        return 0.0
    return cfg.edhrec_synergy * _clamp01((syn - cfg.synergy_floor) / (cfg.synergy_ceil - cfg.synergy_floor))


def _power_level(card, ctx, cfg) -> float:
    return brew_power.power_term(card, ctx, cfg)


_CORE: Dict[str, Callable] = {
    "commander_synergy": _commander_synergy,
    "category_need": _category_need,
    "edhrec_inclusion": _edhrec_inclusion,
    "edhrec_synergy": _edhrec_synergy,
    "power_level": _power_level,
}


def score_card(card: Mapping[str, Any], context: ScoreContext,
               config: Optional[BrewScoringConfig] = None,
               extra_terms: Optional[Mapping[str, Term]] = None) -> ScoreResult:
    cfg = config or BrewScoringConfig()
    comps: Dict[str, float] = {k: fn(card, context, cfg) for k, fn in _CORE.items()}
    terms = {**_TERMS, **(extra_terms or {})}
    for name, fn in terms.items():
        try:
            comps[name] = float(fn(card, context, cfg))
        except Exception:
            comps[name] = 0.0  # a broken plug-in term must not break selection
    return ScoreResult(total=sum(comps.values()), components=comps)


def select_cards(candidates: Sequence[Mapping[str, Any]], context: ScoreContext, limit: int,
                 config: Optional[BrewScoringConfig] = None,
                 extra_terms: Optional[Mapping[str, Term]] = None) -> List[Mapping[str, Any]]:
    """Greedy pick of up to `limit` unique cards, rescoring as category counts change.

    Hard guarantees on top of the soft score: a category at its range top is
    skipped while any alternative remains, and when remaining slots equal the
    outstanding range-floor deficit only deficit-category cards are taken.
    Ties break on name for determinism. `context.deck_counts` is not mutated.
    """
    cfg = config or BrewScoringConfig()
    counts = dict(context.deck_counts)
    mv_sum, mv_n = context.mv_sum, context.mv_n
    pool = {}
    for c in candidates:
        pool.setdefault(_name_key(c.get("name", "")), c)
    remaining = list(pool.values())
    picked: List[Mapping[str, Any]] = []
    sk = context.skeleton
    while remaining and len(picked) < limit:
        ctx = replace(context, deck_counts=counts, mv_sum=mv_sum, mv_n=mv_n)
        slots = limit - len(picked)
        deficit = {k: r.low - counts.get(k, 0) for k, r in sk.items() if counts.get(k, 0) < r.low}

        def capped(c):
            return any(counts.get(k, 0) >= sk[k].high for k in _matching(c, sk))

        pool_now = [c for c in remaining if not capped(c)] or remaining
        if deficit and slots <= sum(deficit.values()):
            pool_now = _floor_feasible(pool_now, deficit, slots, sk) or pool_now
        best = min(pool_now, key=lambda c: (-score_card(c, ctx, cfg, extra_terms).total, _name_key(c.get("name", ""))))
        picked.append(best)
        remaining.remove(best)
        for k in set(_matching(best, sk)):
            counts[k] = counts.get(k, 0) + 1
        mv = brew_power.mana_value(best)
        if mv is not None and not brew_power.is_land(best):
            mv_sum, mv_n = mv_sum + mv, mv_n + 1
    return picked



def _cover_size(deficit: Mapping[str, int], cards: Sequence[Mapping[str, Any]], sk, cap: int) -> int:
    """Greedy count of cards needed to clear `deficit` (cap+1 if it cannot be done within cap)."""
    need = {k: v for k, v in deficit.items() if v > 0}
    pool = list(cards)
    used = 0
    while need:
        if used >= cap or not pool:
            return cap + 1
        gain = lambda c: sum(1 for k in set(_matching(c, sk)) if k in need)
        best = max(pool, key=gain)
        if gain(best) == 0:
            return cap + 1
        for k in set(_matching(best, sk)):
            if k in need:
                need[k] -= 1
                if need[k] <= 0:
                    del need[k]
        pool.remove(best)
        used += 1
    return used


def _floor_feasible(cands, deficit, slots, sk):
    """Candidates that keep every outstanding floor fillable in the slots left after taking them."""
    ok = []
    for c in cands:
        hit = set(_matching(c, sk))
        after = {k: v - (1 if k in hit else 0) for k, v in deficit.items()}
        rest = [x for x in cands if x is not c]
        if _cover_size(after, rest, sk, slots - 1) <= slots - 1:
            ok.append(c)
    return ok
