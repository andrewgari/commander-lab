"""Shared offline fixtures for the Pass C / Pass D tests (not a test module)."""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Callable

from src.server.domain.analytics.candidates import CandidatePool
from src.server.domain.analytics.review_pipeline import a_brief, b_analysis, c_recommend, config, context, llm
from src.server.domain.intake.user_intent import UserDeckIntent
from src.server.domain.profile import player_profile as pp
from tests import review_pipeline_helpers as h

CFG = config.PipelineConfig.from_env({
    "REVIEW_MODEL_A": "anthropic:gen-a", "REVIEW_MODEL_B": "anthropic:gen-b", "REVIEW_MODEL_C": "anthropic:gen-c",
    "REVIEW_MODEL_CRITIC": "google:critic"})

POOL_CARDS = [  # rank order = pool id (pc-N)
    ("Mind Stone", "Artifact", ["ramp"], 1.0, True, "{T}: Add {C}.", False, ["U", "R"]),
    ("Thought Vessel", "Artifact", ["ramp"], 1.5, False, "{T}: Add {C}. You have no maximum hand size.", False, ["U", "R"]),
    ("Rhystic Study", "Enchantment", ["draw"], 30.0, False, "Whenever an opponent casts a spell, you may draw a card unless that player pays {1}.", False, ["U"]),
    ("Cyclonic Rift", "Instant", ["wipes"], 2.0, False, "Return target nonland permanent you don't control to its owner's hand.", False, ["U"]),
    ("Jeska's Will", "Instant", ["ramp"], 3.0, False, "Choose one. Add {R} for each card in target opponent's hand.", True, ["R"]),
    ("Goblin Bombardment", "Enchantment", ["draw"], 4.0, True, "Sacrifice a creature: This deals 1 damage to any target.", False, ["R"]),
]


def oid(i: int) -> str:
    return f"11111111-1111-4111-8111-{i:012d}"


def pool(*, cards=POOL_CARDS, ownership_known=True, notes=()) -> CandidatePool:
    rows = []
    for i, (name, tl, roles, price, owned, text, gc, ci) in enumerate(cards, 1):
        rows.append({"name": name, "oracle_id": oid(i), "type_line": tl, "cmc": 2.0, "price_usd": price, "game_changer": gc,
                     "oracle_text": text, "color_identity": ci, "legalities": {"commander": "legal"}, "roles": roles, "themes": [],
                     "sources": ["edhrec"], "edhrec": {"inclusion": 0.4, "synergy": 0.1, "lists": []},
                     "owned": owned, "owned_copies": 1 if owned else 0, "owned_available": 1 if owned else 0, "owned_in_decks": [],
                     "pet_card_of_player": False, "rank": i})
    p = CandidatePool(cards=rows, notes=list(notes) + ([] if ownership_known else ["Ownership unavailable: no card is flagged owned."]))
    return p


def pack_with_identity() -> dict:
    p = copy.deepcopy(h.pack())
    for c in p["cards"]:
        if c["name"] == "Lara":
            c["color_identity"] = ["U", "R"]
    return p


def make_ctx(*, rejected=(), target_bracket: int | None = 3, profile: pp.PlayerProfile | None = None):
    prof = profile or h.profile()
    for cut, add in rejected:
        prof.feedback.append(pp.FeedbackEntry("d1", "r0", cut, add, "rejected", "", "2026-01-01T00:00:00Z"))
    intent = UserDeckIntent(target_bracket=target_bracket)
    return context.ReviewContext.build(deck_id="d1", deck_name="Test Deck", fact_pack=pack_with_identity(), deck_cards=h.DECK_CARDS,
                                       commanders=["Lara"], profile=prof, intent=intent)


def make_brief(ctx) -> dict:
    return a_brief.finalize(copy.deepcopy(h.BRIEF_REPLY), ctx.resolver)


def finding_doc(ctx, fid="p-plan_synergy-1", claim="Mana Crypt costs life each upkeep and is a Game Changer that pushes the list to bracket 3.",
                cards=("Mana Crypt",), keys=("bracket.minimum_bracket",), sev="high", conf=0.8) -> dict:
    refs = [{"name": ctx.resolver.resolve(c), "oracle_id": ctx.resolver.oracle_id(ctx.resolver.resolve(c))} for c in cards]
    return {"id": fid, "axis": fid.split("-")[1], "claim": claim, "evidence": {"cards": refs, "fact_keys": list(keys)},
            "severity": sev, "confidence": conf}


def analysis(ctx, problems=None, strengths=None) -> b_analysis.AnalysisResult:
    problems = [finding_doc(ctx)] if problems is None else problems
    strengths = [finding_doc(ctx, "s-plan_synergy-1", "Skullclamp turns each Goblin token from Hordeling Outburst into two cards.",
                             ("Skullclamp", "Hordeling Outburst"), ("roles.draw.count",), "high", 0.85)] if strengths is None else strengths
    res = b_analysis.AnalysisResult(strengths=list(strengths), problems=list(problems))
    for f in [*strengths, *problems]:
        res.sidecar[f["id"]] = {"fix_direction": "swap in cheaper mana rocks", "depends_on_questions": []}
    axes = {}
    for f in [*strengths, *problems]:
        axes.setdefault(f["axis"], []).append(f["id"])
    res.axes = [{"axis": a, "label": a, "score": 6, "one_line": "x", "finding_ids": ids, "no_significant_problems": False, "dropped": 0}
                for a, ids in axes.items()]
    return res


def swap_in(cut="Mana Crypt", add_id="pc-1", alt="pc-2", resolves="p-plan_synergy-1", tradeoff=None, bracket="lowers") -> dict:
    return {"cut": cut, "add_id": add_id, "alternative_id": alt, "resolves_finding_id": resolves, "bracket_effect": bracket,
            "tradeoff": tradeoff or f"Cutting {cut} gives up two extra mana on turn one, but Mind Stone costs no life and drops the Game Changer count."}


def recommend_reply(swaps: list[dict], top=None, cuts=()) -> dict:
    n = len(swaps)
    ranks = top if top is not None else list(range(1, min(3, n) + 1))
    return {"swaps": swaps, "top_changes": {"ranks": ranks, "why_these_first": "Swap 1 removes the life payment and the Game Changer in one move." if swaps else ""},
            "cuts_to_consider": list(cuts)}


class Handlers:
    """A scripted LLMClient: handlers by label prefix; each handler is a callable(request) -> reply or a FIFO list.
    Unscripted labels raise, so a test proves a call did NOT happen simply by not scripting it."""

    def __init__(self, **by_prefix: Any):
        self.by_prefix = by_prefix
        self.requests: list[llm.LLMRequest] = []
        self.fifo = {k: list(v) for k, v in by_prefix.items() if isinstance(v, list)}

    def complete(self, request: llm.LLMRequest) -> llm.LLMResponse:
        self.requests.append(request)
        matching = [(p, h) for p, h in self.by_prefix.items() if request.label == p or request.label.startswith(p + ":")]
        for prefix, handler in sorted(matching, key=lambda ph: -len(ph[0])):          # most specific prefix wins
            if True:
                reply = (self.fifo[prefix].pop(0) if self.fifo[prefix] else None) if isinstance(handler, list) else handler(request)
                if reply is None:
                    raise AssertionError(f"scripted replies for {prefix!r} exhausted (label {request.label!r})")
                text = reply if isinstance(reply, str) else json.dumps(reply)
                return llm.LLMResponse(text=text, usage=llm.Usage(tokens_in=500, tokens_out=100, cost_usd=0.01), model=request.model, latency_ms=5)
        raise AssertionError(f"no scripted handler for call {request.label!r} (model {request.model})")

    def labels(self) -> list[str]:
        return [r.label for r in self.requests]

    def calls(self, prefix: str) -> list[llm.LLMRequest]:
        return [r for r in self.requests if r.label.startswith(prefix)]


def items_in(request: llm.LLMRequest) -> dict[str, str]:
    """item id -> TEXT UNDER REVIEW, parsed from a critic request."""
    body = request.messages[0].content
    out = {}
    for m in re.finditer(r"### ITEM (\S+) \(\w+\)\nTEXT UNDER REVIEW:\n(.*?)\nDOSSIER:", body, re.S):
        out[m.group(1)] = m.group(2)
    return out


def critic(flags: dict[str, tuple[str, str, str]] | Callable[[str, str], tuple[str, str, str] | None] | None = None):
    """Critic handler: ok for every item except those in ``flags`` ({item_id: (category, quote, reason)}) or flagged by a callable(item_id, text)."""
    def handler(request):
        verdicts = []
        for iid, text in items_in(request).items():
            f = flags(iid, text) if callable(flags) else (flags or {}).get(iid)
            if f:
                verdicts.append({"item_id": iid, "verdict": "flag", "category": f[0], "quote": f[1], "reason": f[2]})
            else:
                verdicts.append({"item_id": iid, "verdict": "ok", "category": "none", "quote": "", "reason": "checked against the dossier"})
        return {"verdicts": verdicts}
    return handler


def finding_in(claim="Mana Crypt costs life each upkeep.", cards=("Mana Crypt",), keys=("bracket.minimum_bracket",), severity="high",
               confidence=0.8, deps=(), fix="swap in cheaper mana rocks") -> dict:
    """A model-facing finding (schemas._FINDING_IN), as Pass B or a regeneration returns it."""
    return {"claim": claim, "evidence": {"cards": list(cards), "fact_keys": list(keys)}, "severity": severity,
            "confidence": confidence, "depends_on_questions": list(deps), "fix_direction": fix}


def top_regen_handler(request):
    """Valid reply to a top_changes regeneration: the first min(3, n) swaps in the prompt's list."""
    n = len(re.findall(r"^\d+\. sw-", request.messages[0].content, re.M))
    return {"ranks": list(range(1, min(3, n) + 1)), "why_these_first": "These come first because they fix the highest-severity problem cheaply."}


def critic_once(flags: dict[str, tuple[str, str, str]]):
    """Flags each item in ``flags`` the FIRST time the critic sees it, then says ok (models a generator that fixes it)."""
    seen: set[str] = set()

    def f(iid, text):
        if iid in flags and iid not in seen:
            seen.add(iid)
            return flags[iid]
        return None
    return critic(f)
