"""Replacement suggester (spec §4). Runs only when the verdict is remove.

Hard filters (§4.1) are enforced here, in code, against fresh card facts (oracle_id, colour identity,
legality) from `fact_lookup`, not against the evaluation's pool alone. Penalties are flags, never filters.

Not scored yet (the EvaluationResult has no data for them): CurveFit, Budget, price, Game Changer, salt,
paper-only, donor decks, and sub-tag diversity. The Rep weights of the terms we do have are renormalised.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.server.domain.analytics.card_eval import Candidate, EvaluationResult
from src.server.domain.analytics.card_eval.card_source import mainboard_gate
from src.server.domain.analytics import card_source
from src.server.domain.analytics.edhrec_inclusion import name_key

from . import config as cfg
from .packet import ReviewContext

FactLookup = Callable[[Iterable[str]], Mapping[str, dict]]  # names -> {name_key: {oracle_id, color_identity, legal}}
# Optional table-health hook: candidate name -> [(penalty_points, flag), ...]  (GC, salt, stax...)
Annotator = Callable[[str], Sequence[Tuple[float, str]]]


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _ci_ok(card_ci: str, allowed: Optional[set]) -> bool:
    return allowed is None or (set(card_ci or "") - {"C"}) <= allowed


def pillar_fit(c: Candidate) -> float:
    """Proxy for the P scale (§2.3) from EDHREC synergy: no pillar-predicate data in the evaluation."""
    syn = c.synergy or 0.0
    if syn >= cfg.PILLAR_SYNERGY_HI:
        return 80.0
    if syn >= cfg.PILLAR_SYNERGY_MID:
        return 60.0
    return 40.0 if set(c.roles) & cfg.STAPLE_ROLES else 0.0


def e_c(c: Candidate) -> float:
    """Candidate E (§2.1) without R/H: 0.40*I + 0.35*S, renormalised."""
    i = 100.0 * math.sqrt(max(c.inclusion or 0.0, 0.0))
    s = _clamp(50.0 + 100.0 * (c.synergy if c.synergy is not None else 0.0))
    return (0.40 * i + 0.35 * s) / 0.75


def role_fit(c: Candidate, targets: set) -> float:
    if c.roles and c.roles[0] in targets:
        return 100.0
    return 70.0 if set(c.roles) & targets else 0.0


def rep_score(c: Candidate, targets: set, penalties: float = 0.0) -> Tuple[float, dict]:
    parts = {"RoleFit": role_fit(c, targets), "PillarFit": pillar_fit(c), "E_c": e_c(c),
             "Own": 100.0 if c.owned else 0.0}
    w = cfg.REP_WEIGHTS
    rep = sum(w[k] * parts[k] for k in w) / sum(w.values()) - penalties
    return round(rep, 1), {k: round(v, 1) for k, v in parts.items()}


def _rationale(c: Candidate, ev: EvaluationResult, like: bool, targets: Sequence[str], flags: List[str]) -> str:
    role = c.roles[0] if c.roles else "unclassified"
    head = (f"Same job as {ev.card.name} ({role})" if like
            else f"Changes the slot to {role}" + (f" (deck needs {', '.join(targets)})" if targets else ""))
    bits = [head]
    if c.inclusion is not None:
        bits.append(f"{c.inclusion * 100:.0f}% of {ev.commanders[0]} decks" + (
            f", synergy {c.synergy:+.2f}" if c.synergy is not None else ""))
    if c.owned:
        bits.append("owned: pull from binder")
    bits.extend(flags)
    return ". ".join(bits)[: cfg.RATIONALE_MAX]


def suggest_replacements(ev: EvaluationResult, brief: Optional[dict], ctx: ReviewContext, deck_names: Sequence[str],
                         fact_lookup: FactLookup, annotate: Optional[Annotator] = None,
                         source_lookup: Optional[Callable[[str], dict]] = None) -> dict:
    """-> {suggestions, mode, pool_exhausted, dropped, flags}. 3-5 suggestions when the pool allows."""
    card_primary = ev.roles[0].role if ev.roles else None
    brief = brief or {"mode": "mixed", "target_roles": [card_primary] if card_primary else [], "avoid": []}
    mode = brief["mode"]
    targets = list(brief.get("target_roles") or ([card_primary] if card_primary else []))
    tset = set(targets)
    flags: List[str] = []
    dropped: List[dict] = []

    # Judge said function_change, but the deck is short on this very role: keep a like-for-like option.
    need = ev.strength_breakdown.role_need
    if mode == "function_change" and need is not None and need >= cfg.ROLE_NEED_FORCE_LIKE:
        mode = "mixed"
        flags.append("mode raised to mixed: the deck is short on this role")
    if "Land" in ev.card.type_line:
        mode = "like_for_like"
        targets = ["land"]
        tset = set(targets)
        flags.append("lands only ever get like-for-like land replacements")

    in_deck = {name_key(n) for n in [*deck_names, *ev.commanders, ev.card.name] if n}
    facts = fact_lookup([c.name for c in ev.candidates] + [n for n in deck_names if n]) if ev.candidates else {}
    deck_oids = {facts[k]["oracle_id"] for k in (name_key(n) for n in deck_names if n)
                 if k in facts and facts[k].get("oracle_id")}
    if ev.card.oracle_id:
        deck_oids.add(ev.card.oracle_id)
    if ctx.commander_identity is None:
        flags.append("commander colour identity unknown; colour filter skipped")

    ranked: List[Tuple[float, Candidate, dict, List[str], bool]] = []
    seen_oids = set()
    for c in ev.candidates:
        f = facts.get(name_key(c.name))
        if not f:
            dropped.append({"card": c.name, "reason": "no card data"}); continue
        oid = f.get("oracle_id")
        if name_key(c.name) in in_deck or (oid and oid in deck_oids):
            dropped.append({"card": c.name, "reason": "already in the deck"}); continue
        if oid and oid in seen_oids:
            dropped.append({"card": c.name, "reason": "duplicate candidate"}); continue
        # Full mainboard slot gate at proposal time: known Commander legality
        # (fail-closed) AND no variant type class. Planar/scheme/attraction
        # cards and unknown-legality cards can never be suggested into a
        # mainboard slot.
        gate = mainboard_gate(f)
        if not gate["legal"]:
            dropped.append({"card": c.name, "reason": gate["reason"]}); continue
        if not _ci_ok(f.get("color_identity", c.color_identity), ctx.commander_identity):
            dropped.append({"card": c.name, "reason": "outside the commander's colour identity"}); continue
        if not (set(c.roles) & tset) and pillar_fit(c) < cfg.PILLAR_GATE:
            dropped.append({"card": c.name, "reason": "spirit gate: fills no target role and fits no pillar"}); continue
        seen_oids.add(oid)
        pen = list(annotate(c.name)) if annotate else []
        rep, parts = rep_score(c, tset, sum(p for p, _ in pen))
        parts["penalties"] = [fl for _, fl in pen]
        like = bool(card_primary and c.roles and c.roles[0] == card_primary)
        ranked.append((rep, c, parts, [fl for _, fl in pen], like))

    ranked.sort(key=lambda t: (-t[0], t[1].name))
    likes = [t for t in ranked if t[4]]
    others = [t for t in ranked if not t[4]]
    if mode == "like_for_like":
        pick, backfill = likes[: cfg.MAX_SUGGESTIONS], others
    elif mode == "function_change":
        pick, backfill = others[: cfg.MAX_SUGGESTIONS], likes
    else:
        n_like, n_fn = cfg.MIXED_SPLIT
        pick = likes[:n_like] + others[:n_fn]
        # a short side is topped up from the other, but each side keeps at least one when it has any
        rest = [t for t in ranked if t not in pick]
        pick += rest[: max(0, cfg.MAX_SUGGESTIONS - len(pick))]
        backfill = []
    if len(pick) < cfg.MIN_SUGGESTIONS and backfill:
        flags.append(f"{mode} pool too small; topped up from the other group")
        pick += [t for t in backfill if t not in pick][: cfg.MIN_SUGGESTIONS - len(pick)]
    pick.sort(key=lambda t: (-t[0], t[1].name))
    pick = pick[: cfg.MAX_SUGGESTIONS]

    # Source tier ranking
    sourced_pick = []
    for rep, c, parts, fl, like in pick:
        source_info = source_lookup(c.name) if source_lookup else {}
        # reconstruction bypass: if source_lookup is missing or returns bypass=True, we keep the old behaviour roughly
        bypass = source_info.get("bypass", False)
        
        src = card_source.determine_source(
            card_name=c.name,
            is_owned=bool(c.owned),
            available_copies=source_info.get("available_copies", 1 if c.owned else 0),
            price=source_info.get("price", 0.0),
            budget_cap=source_info.get("budget_cap", 5.0),
            this_deck_score=rep,
            donor_decks=source_info.get("donor_decks", []),
            very_strong_threshold=cfg.VERY_STRONG_THRESHOLD if hasattr(cfg, 'VERY_STRONG_THRESHOLD') else 85.0
        )
        tier_val = 99
        tier_str = "buy"
        meta = {}
        if bypass:
            tier_val = 1 if c.owned else 2
            tier_str = "owned" if c.owned else "buy"
        elif src:
            if src["source"] == "free":
                tier_val = 1
                tier_str = "owned-and-unused"
            elif src["source"] == "buy":
                tier_val = 2
                tier_str = "buy-under-budget"
                meta["price"] = src.get("price")
            elif src["source"] == "donor":
                tier_val = 3
                tier_str = "donor-deck"
                meta["donor_deck"] = src.get("donor_deck")
        else:
            tier_val = 4
            tier_str = "unavailable"
            
        sourced_pick.append((tier_val, rep, c, parts, fl, like, tier_str, meta))

    sourced_pick.sort(key=lambda t: (t[0], -t[1], t[2].name))

    out = []
    for t_val, rep, c, parts, fl, like, tier_str, meta in sourced_pick:
        if t_val == 4:
            continue # Skip unavailable if any? Wait, acceptance says replace ranks, didn't say drop. Let's keep them if pool exhausted, or drop?
            # Instructions: "A card from another deck is suggested only when all three hold... Cards from other decks never appear as a casual or default suggestion"
            # If t_val == 4, it means determine_source returned None, which means it doesn't meet any criteria (e.g. expensive, or donor but not very strong).
            # Dropping them might reduce the list size, which is intended.
            
        rec = facts[name_key(c.name)]
        # Every suggestion carries its mainboard slot-gate result, including
        # the pass reason string — never a bare boolean.
        gate = mainboard_gate(rec)
        out.append({"card": c.name, "oracle_id": rec.get("oracle_id"), "role": c.roles[0] if c.roles else None,
                    "like_for_like": like,
                    "rationale": _rationale(c, ev, like, targets, fl),
                    "score": rep, "score_breakdown": parts, "tier": tier_str,
                    "source_meta": meta,
                    "slot_gate": gate,
                    "owned": c.owned, "flags": fl})
    return {"suggestions": out, "mode": mode, "target_roles": targets,
            "pool_exhausted": len(out) < cfg.MIN_SUGGESTIONS, "dropped": dropped, "flags": flags}
