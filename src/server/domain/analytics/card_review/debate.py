"""Adversarial debate: Remove advocate vs Keep advocate for 2-3 rounds, then a Judge (spec §3).

Everything the models say is validated in code before it counts: claims must cite packet IDs, may only
use numbers that appear in the packet, and are re-tiered by the evidence they cite. The judge's verdict
is validated (§3.5) and its confidence is combined with the rubric prior deterministically (§3.6).
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from . import config as cfg
from .llm import DebateLLM
from .packet import ReviewContext, band, p_keep, packet_numbers, tier_of, unknown_numbers
from .prompts import JUDGE_PROMPT, KEEP_PROMPT, REMOVE_PROMPT

Event = Callable[[dict], None]
SIDES = {"remove": ("RM", REMOVE_PROMPT), "keep": ("KP", KEEP_PROMPT)}
MODES = {"like_for_like", "function_change", "mixed"}
_REASON_IDS = re.compile(r"\[([A-Z]{1,2}\d+(?:\s*,\s*[A-Z]{1,2}\d+)*)\]")


def render(template: str, **vars) -> str:
    for k, v in vars.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template


def extract_json(text: str) -> Optional[dict]:
    """Parse a model reply that should be a single JSON object (tolerates fences and stray prose)."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    for cand in (t, t[t.find("{"): t.rfind("}") + 1] if "{" in t else ""):
        try:
            v = json.loads(cand)
        except (ValueError, TypeError):
            continue
        if isinstance(v, dict):
            return v
    return None


@dataclass
class DebateConfig:
    rounds: int = cfg.DEFAULT_ROUNDS
    advocate_temperature: float = cfg.ADVOCATE_TEMPERATURE
    judge_temperature: float = cfg.JUDGE_TEMPERATURE

    def __post_init__(self):
        self.rounds = max(cfg.MIN_ROUNDS, min(cfg.MAX_ROUNDS, int(self.rounds)))


# ---------------------------------------------------------------------------------------------
# Advocate turns
# ---------------------------------------------------------------------------------------------

def _empty_turn(side: str, rnd: int, why: str) -> dict:
    return {"side": side, "round": rnd, "claims": [], "rebuttals": [], "concede": False,
            "discarded": [{"id": f"{SIDES[side][0]}-{rnd}.*", "why": why}]}


def _valid_evidence(ev, packet) -> List[str]:
    return [e for e in (ev if isinstance(ev, list) else []) if isinstance(e, str) and e in packet]


def validate_turn(raw: dict, side: str, rnd: int, packet: Dict[str, str], allowed_nums: set,
                  opp_claim_ids: set, prior_evidence: set) -> dict:
    prefix = SIDES[side][0]
    discarded: List[dict] = []
    claims: List[dict] = []
    rebuttals: List[dict] = []
    max_claims = cfg.CLAIMS_R1 if rnd == 1 else cfg.NEW_CLAIMS_R2 if rnd == 2 else 1

    def check(item, label) -> Optional[dict]:
        text = str(item.get("text") or "").strip()
        evid = _valid_evidence(item.get("evidence"), packet)
        if not text:
            discarded.append({"id": label, "why": "empty"}); return None
        if not evid:
            discarded.append({"id": label, "why": "cites no valid packet ID"}); return None
        if len(text.split()) > cfg.CLAIM_WORDS:
            discarded.append({"id": label, "why": f"over {cfg.CLAIM_WORDS} words"}); return None
        bad = unknown_numbers(text, allowed_nums)
        if bad:
            discarded.append({"id": label, "why": f"number(s) not in packet: {sorted(bad)}"}); return None
        if rnd == 3 and not set(evid) <= prior_evidence:
            discarded.append({"id": label, "why": "new evidence in closing round"}); return None
        return {"text": text, "evidence": evid}

    for i, item in enumerate(raw.get("claims") or [], 1):
        if not isinstance(item, dict):
            continue
        cid = f"{prefix}-{rnd}.{len(claims) + 1}"
        if len(claims) >= max_claims:
            discarded.append({"id": f"{prefix}-{rnd}.{i}", "why": "over the round's claim limit"}); continue
        ok = check(item, cid)
        if ok:
            claims.append({"id": cid, "tier": tier_of(ok["evidence"]), "claimed_tier": item.get("tier"), **ok})
    if rnd == 2:
        for i, item in enumerate(raw.get("rebuttals") or [], 1):
            if not isinstance(item, dict):
                continue
            if len(rebuttals) >= cfg.REBUTTALS_R2:
                discarded.append({"id": f"{prefix}-R{rnd}.{i}", "why": "over the rebuttal limit"}); continue
            if item.get("target") not in opp_claim_ids:
                discarded.append({"id": f"{prefix}-R{rnd}.{i}", "why": "rebuts an unknown claim"}); continue
            ok = check(item, f"{prefix}-R{rnd}.{i}")
            if ok:
                rebuttals.append({"target": item["target"], **ok})
    elif raw.get("rebuttals"):
        discarded.append({"id": f"{prefix}-R{rnd}.*", "why": "rebuttals only allowed in round 2"})
    return {"side": side, "round": rnd, "claims": claims, "rebuttals": rebuttals,
            "concede": bool(raw.get("concede")), "discarded": discarded}


def _call_json(llm: DebateLLM, role: str, system: str, user: str, temperature: float) -> Optional[dict]:
    """One call plus one 'valid JSON only' retry (§3.5 step 1). None if both replies are unusable."""
    for attempt in range(2):
        msg = user if attempt == 0 else user + "\n\nReturn valid JSON only."
        parsed = extract_json(llm.complete(role, system, msg, temperature=temperature))
        if parsed is not None:
            return parsed
    return None


def _run_turn(llm, side, rnd, rounds, packet, nums, transcript, ctx, ev_name, dcfg) -> dict:
    system = render(SIDES[side][1], card_name=ev_name, commander=packet.get("C2", "").replace("Commander(s): ", ""),
                    deck_name=ctx.deck_name, deck_kind=ctx.deck_kind, round=rnd, max_rounds=rounds)
    seen = [] if rnd == 1 else [t for t in transcript if t["round"] < rnd]  # round 1 openings are blind
    user = json.dumps({"packet": packet, "transcript": seen, "round": f"{rnd}/{rounds}"})
    raw = _call_json(llm, side, system, user, dcfg.advocate_temperature)
    if raw is None:
        return _empty_turn(side, rnd, "model returned invalid JSON twice")
    opp = "keep" if side == "remove" else "remove"
    opp_ids = {c["id"] for t in transcript if t["side"] == opp and t["round"] < rnd for c in t["claims"]}
    prior_ev = {e for t in transcript if t["side"] == side for c in t["claims"] for e in c["evidence"]}
    return validate_turn(raw, side, rnd, packet, nums, opp_ids, prior_ev)


def run_debate(llm: DebateLLM, packet: Dict[str, str], ctx: ReviewContext, card_name: str,
               dcfg: Optional[DebateConfig] = None, on_event: Optional[Event] = None) -> List[dict]:
    """Returns the validated transcript (list of turns). Stops early when a side concedes."""
    dcfg = dcfg or DebateConfig()
    emit = on_event or (lambda e: None)
    nums = packet_numbers(packet)
    transcript: List[dict] = []
    for rnd in range(1, dcfg.rounds + 1):
        emit({"stage": "round_start", "round": rnd, "of": dcfg.rounds})
        args = (rnd, dcfg.rounds, packet, nums, list(transcript), ctx, card_name, dcfg)
        with ThreadPoolExecutor(max_workers=2) as pool:  # both advocates see the same prior transcript
            futs = {s: pool.submit(_run_turn, llm, s, *args) for s in ("remove", "keep")}
            turns = {s: f.result() for s, f in futs.items()}
        for s in ("remove", "keep"):
            transcript.append(turns[s])
            emit({"stage": "turn", "side": s, "round": rnd, "turn": turns[s]})
        if any(t["concede"] for t in turns.values()):
            emit({"stage": "concession", "round": rnd})
            break
    return transcript


# ---------------------------------------------------------------------------------------------
# Judge
# ---------------------------------------------------------------------------------------------

def _reason_ok(reason: str, packet: Dict[str, str], nums: set) -> bool:
    m = _REASON_IDS.search(reason or "")
    if not m:
        return False
    ids = [x.strip() for x in m.group(1).split(",")]
    return all(i in packet for i in ids) and not unknown_numbers(_REASON_IDS.sub("", reason), nums)


def validate_verdict(raw: dict, packet: Dict[str, str], transcript: List[dict]) -> Optional[dict]:
    """§3.5. None when the verdict can't be trusted (caller retries once, then falls back)."""
    verdict = str(raw.get("verdict") or "").lower()
    if verdict not in ("keep", "remove"):
        return None
    try:
        jc = float(raw.get("judge_confidence"))
    except (TypeError, ValueError):
        return None
    nums = packet_numbers(packet)
    reasons = [x for x in (raw.get("key_reasons") or []) if isinstance(x, str) and _reason_ok(x, packet, nums)]
    if len(reasons) < 2:
        return None
    known = {c["id"] for t in transcript for c in t["claims"]}
    winning = [w for w in (raw.get("winning_claims") or []) if w in known]
    tier = raw.get("deciding_tier")
    brief = raw.get("replacement_brief") if verdict == "remove" else None
    if isinstance(brief, dict):
        mode = brief.get("mode") if brief.get("mode") in MODES else "mixed"
        brief = {"mode": mode,
                 "target_roles": [x for x in (brief.get("target_roles") or []) if isinstance(x, str)],
                 "avoid": [x for x in (brief.get("avoid") or []) if isinstance(x, str)]}
    else:
        brief = None
    return {"verdict": verdict, "judge_confidence": max(cfg.JUDGE_CONF_MIN, min(cfg.JUDGE_CONF_MAX, jc)),
            "key_reasons": reasons[:4],
            "deciding_tier": tier if tier in (*cfg.TIERS, "rubric_tiebreak") else "rubric_tiebreak",
            "winning_claims": winning,
            "discarded": [d for d in (raw.get("discarded") or []) if isinstance(d, dict)],
            "replacement_brief": brief, "fallback": False}


def fallback_verdict(ev, packet: Dict[str, str]) -> dict:
    """Rubric-lean verdict used when the judge fails twice (§3.5 step 2)."""
    verdict = "keep" if ev.strength >= cfg.PRIOR_CENTER else "remove"
    sb = ev.strength_breakdown
    comps = sorted(((k, v) for k, v in sb.model_dump().items() if v is not None), key=lambda kv: -kv[1])[:3]
    reasons = [f"Rubric component {k.replace('_', ' ')} scores {v:.2f}. [S1]" for k, v in comps] or [
        f"Rubric strength is {ev.strength:.1f}/100. [S1]"]
    prior = p_keep(ev.strength)
    return {"verdict": verdict, "judge_confidence": min(prior if verdict == "keep" else 1 - prior, cfg.CAP_FALLBACK),
            "key_reasons": reasons, "deciding_tier": "rubric_tiebreak", "winning_claims": [], "discarded": [],
            "replacement_brief": None, "fallback": True}


def run_judge(llm: DebateLLM, ev, packet: Dict[str, str], transcript: List[dict], ctx: ReviewContext,
              dcfg: Optional[DebateConfig] = None) -> dict:
    dcfg = dcfg or DebateConfig()
    system = render(JUDGE_PROMPT, card_name=ev.card.name, commander=" + ".join(ev.commanders))
    conceded = [t["side"] for t in transcript if t["concede"]]
    user = json.dumps({"packet": packet, "transcript": transcript, "conceded": conceded})
    v = None
    for attempt in range(2):
        raw = extract_json(llm.complete("judge", system, user if attempt == 0 else user + "\n\nReturn valid JSON only.",
                                        temperature=dcfg.judge_temperature))
        v = validate_verdict(raw, packet, transcript) if raw else None
        if v:
            break
    if v is None:
        return fallback_verdict(ev, packet)
    if len(set(conceded)) == 1:  # a concession ends the debate in favour of the other side
        winner = "keep" if conceded[0] == "remove" else "remove"
        if v["verdict"] != winner:
            v.update(verdict=winner, deciding_tier="concession", replacement_brief=None)
            v["key_reasons"] = v["key_reasons"] + [f"The {conceded[0]} advocate conceded the debate."]
    return v


def final_confidence(verdict: str, strength: float, judge_conf: float, *, edhrec_missing: bool,
                     journal_unconfirmed: bool, fallback: bool) -> float:
    """§3.6: 0.5*prior + 0.5*judge, then caps, then the 0.50 floor."""
    pk = p_keep(strength)
    conf = 0.5 * (pk if verdict == "keep" else 1 - pk) + 0.5 * judge_conf
    for on, cap in ((edhrec_missing, cfg.CAP_EDHREC_MISSING), (journal_unconfirmed, cfg.CAP_JOURNAL_UNCONFIRMED),
                    (fallback, cfg.CAP_FALLBACK)):
        if on:
            conf = min(conf, cap)
    return round(max(conf, cfg.CONF_FLOOR), 2)
