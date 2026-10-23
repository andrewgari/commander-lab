"""Review context + evidence packet (spec §3.1). Nothing outside the packet can be cited by an advocate."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from analytics.card_eval import EvaluationResult
from analytics.edhrec_inclusion import name_key

from . import config as cfg


@dataclass
class ReviewContext:
    deck_name: str = ""
    deck_kind: str = "digital"
    commander_identity: Optional[Set[str]] = None  # None = unknown, colour-identity filter skipped
    thesis: str = ""
    pillars: List[dict] = field(default_factory=list)  # [{"name","description"}]
    pet_cards: List[str] = field(default_factory=list)
    bracket: Optional[str] = None
    journal_confirmed: bool = False  # thesis or pillars present


def band(strength: float) -> str:
    return next(name for floor, name in cfg.BANDS if strength >= floor)


def p_keep(strength: float) -> float:
    """Rubric prior (§2.2): logistic around 50."""
    return 1.0 / (1.0 + math.exp(-(strength - cfg.PRIOR_CENTER) / cfg.PRIOR_SCALE))


def _fmt(x: Optional[float], spec: str) -> str:
    return "n/a" if x is None else format(x, spec)


def build_packet(ev: EvaluationResult, ctx: ReviewContext) -> Dict[str, str]:
    """ID -> pre-formatted string. Numbers are copied, never computed by the models."""
    c, ed, sb = ev.card, ev.edhrec, ev.strength_breakdown
    p: Dict[str, str] = {}
    p["C1"] = (f"{c.name}; {c.type_line or 'type unknown'}; colour identity {c.color_identity or 'colourless'}; "
               f"Commander legal: {'yes' if c.legal else 'NO'}; oracle text: {c.oracle_text or 'n/a'}")
    p["C2"] = "Commander(s): " + " + ".join(ev.commanders)
    p["J1"] = ctx.thesis if ctx.journal_confirmed and ctx.thesis else "unconfirmed"
    if ctx.pillars:
        p["J2"] = "Pillars: " + "; ".join(f"{x.get('name')}: {x.get('description') or ''}".strip() for x in ctx.pillars)
    if ctx.bracket:
        p["J3"] = f"Bracket target: {ctx.bracket}"
    if ed.status == "ok" and ed.inclusion is not None:
        p["E1"] = (f"{ed.num_decks:,} decks = {ed.inclusion * 100:.1f}% of {ed.commander_slug} decks "
                   f"(EDHREC, {ed.decks_sampled:,} sampled)" if ed.num_decks is not None and ed.decks_sampled
                   else f"{ed.inclusion * 100:.1f}% of {ed.commander_slug} decks (EDHREC)")
        p["E2"] = f"EDHREC synergy {_fmt(ed.synergy, '+.2f')} with {ed.commander_slug}"
    else:
        p["E1"] = f"EDHREC has no inclusion data for this card here (status: {ed.status})"
    if ev.model.status == "ok" and ev.model.scores:
        top = sorted(ev.model.scores.items(), key=lambda kv: -kv[1])[:3]
        p["H1"] = "Model role labels: " + ", ".join(f"{k} {v:.2f}" for k, v in top)
    p["R1"] = ("Roles: " + ", ".join(f"{s.role} ({s.source}, {s.confidence:.2f})" for s in ev.roles)
               ) if ev.roles else "Roles: none identified"
    if sb.role_need is not None:
        p["R2"] = f"Role need {sb.role_need:.2f} (1.00 = the deck lacks this role, 0.00 = the role is saturated)"
    p["S1"] = (f"Strength {ev.strength:.1f}/100, band {band(ev.strength).replace('_', ' ')}; "
               f"components: inclusion {_fmt(sb.inclusion, '.2f')}, synergy {_fmt(sb.synergy, '.2f')}, "
               f"role confidence {_fmt(sb.role_confidence, '.2f')}, role need {_fmt(sb.role_need, '.2f')}")
    p["P1"] = f"{ev.deck_kind} deck"
    return p


_ID_RE = re.compile(r"\b(?:RM|KP)-\d+\.\d+\b|\b[A-Z]{1,2}\d+\b")
_NUM_RE = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> Set[str]:
    """Normalised numeric tokens in free text, ignoring evidence/claim IDs."""
    out = set()
    for m in _NUM_RE.findall(_ID_RE.sub(" ", text or "")):
        out.add(m.lstrip("+").replace(",", "").rstrip("."))
    return out


def packet_numbers(packet: Dict[str, str]) -> Set[str]:
    nums: Set[str] = set()
    for v in packet.values():
        nums |= numbers_in(v)
    return nums


def unknown_numbers(text: str, allowed: Set[str]) -> Set[str]:
    return {n for n in numbers_in(text) if n not in allowed}


def tier_of(evidence: List[str]) -> str:
    """Highest tier among the cited IDs (§3.4.3 step 2)."""
    tiers = [cfg.PREFIX_TIER.get(e[:1], "popularity") for e in evidence] or ["popularity"]
    return min(tiers, key=cfg.TIERS.index)


def is_pet(ev_card_name: str, ctx: ReviewContext) -> bool:
    return name_key(ev_card_name) in {name_key(n) for n in ctx.pet_cards}
