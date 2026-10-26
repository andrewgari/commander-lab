"""Shared offline fixtures for the review-pipeline tests (not a test module)."""
from __future__ import annotations

import json
from typing import Any

from src.server.domain.analytics.review_pipeline import llm
from src.server.domain.profile import player_profile as pp

OID = {n: f"00000000-0000-4000-8000-{i:012d}" for i, n in enumerate(
    ["Lara", "Sol Ring", "Skullclamp", "Arcane Signet", "Mana Crypt", "Island", "Hordeling Outburst"], start=1)}


def card(name, cmc, type_line, price, text, *, edhrec=None):
    return {"name": name, "cmc": cmc, "type_line": type_line, "colors": [], "color_identity": [],
            "legalities": {"commander": "legal"}, "price": price, "salt": {"status": "unavailable", "reason": "no source"},
            "edhrec": edhrec or {"status": "unavailable", "reason": "n/a"}, "tags": {"status": "unavailable", "reason": "n/a"},
            "oracle_text": text, "oracle_id": OID[name], "game_changer": False, "source": "scryfall", "fetched_at": "t"}


def pack() -> dict:
    cards = [
        card("Lara", 3.0, "Legendary Creature — Human", "2.00", "Haste"),
        card("Sol Ring", 1.0, "Artifact", "1.50", "{T}: Add {C}{C}.", edhrec={"num_decks": 80, "potential_decks": 100, "synergy": 0.1}),
        card("Skullclamp", 1.0, "Artifact — Equipment", "4.00", "Equipped creature gets +1/-1. Whenever equipped creature dies, draw two cards."),
        card("Arcane Signet", 2.0, "Artifact", "1.00", "{T}: Add one mana of any color in your commander's color identity."),
        card("Mana Crypt", 0.0, "Artifact", "150.00", "At the beginning of your upkeep, flip a coin."),
        card("Island", 0.0, "Basic Land — Island", "0.10", "({T}: Add {U}.)"),
        card("Hordeling Outburst", 3.0, "Sorcery", "0.50", "Create three 1/1 red Goblin creature tokens."),
    ]
    return {
        "schema": "fact_pack/2", "generated_at": "t",
        "deck": {"commanders": ["Lara"], "color_identity": ["U", "R"], "card_count": 7},
        "curve": {"source": "scryfall", "average_mv": 1.5, "nonland_cards": 5,
                  "buckets": [{"mv": 0, "count": 1, "label": "0"}, {"mv": 1, "count": 2, "label": "1"}]},
        "mana": {"source": "scryfall", "lands": 1, "mdfc_spell_lands": 0,
                 "pips_vs_sources": [{"color": "U", "pips": 0, "sources": 1, "recommended_sources": 0}],
                 "norms": {"lands": {"target": 38, "source": "x"}}},
        "roles": {"roles": [{"role": "ramp", "count": 2, "cards": ["Arcane Signet", "Sol Ring"], "target_min": 9, "target_max": 11},
                            {"role": "draw", "count": 1, "cards": ["Skullclamp"], "target_min": 11, "target_max": 13}]},
        "bracket": {"minimum_bracket": 3, "reasons": [{"rule": "game_changers", "floor": 3, "cards": ["Mana Crypt"]}],
                    "game_changers": ["Mana Crypt"], "notes": []},
        "combos": {"complete": [], "near_misses": []},
        "edhrec": {"commander": {"name": "Lara", "num_decks": 100, "rank": 5}, "present": [{"name": "Sol Ring", "inclusion": 0.8, "synergy": 0.1, "num_decks": 80}],
                   "high_synergy_absent": [{"name": "Cyclonic Rift", "inclusion": 0.5, "synergy": 0.4}], "deviation_score": 0.7},
        "salt": {"status": "unavailable", "reason": "no source"},
        "synergy": {"clusters": [], "density": 0.0, "orphans": ["Mana Crypt"]},
        "charts": {"curve": {}}, "cards": cards,
    }


DECK = {"commanders": [{"name": "Lara"}], "cards": [{"name": n, "quantity": 1} for n in OID]}
DECK_CARDS = [(n, 1) for n in OID]

BRIEF_REPLY = {
    "what_it_does": "A goblin-token deck that draws off Skullclamp. No owner intent recorded; read from the list.",
    "central_concept": {"summary": "Skullclamp turns Goblin tokens into cards.", "key_cards": ["Skullclamp", "Hordeling Outburst"]},
    "win_paths": [{"path": "Go wide with tokens", "basis": "inferred_from_list", "key_cards": ["Hordeling Outburst"]}],
    "success_criteria": ["Skullclamp active by turn 3"],
    "intentional_quirks": [{"quirk": "One basic land", "cards": ["Island"], "why_deliberate": "Fast mana replaces lands"}],
    "power_target": {"stated": False, "bracket": None, "note": "no target given"},
    "questions_for_owner": [{"question": "What bracket is the target?", "why_asked": "Mana Crypt is a Game Changer"}],
}


def finding(claim="Skullclamp draws two for each Goblin token that dies, refilling the hand.", cards=("Skullclamp",),
            keys=("roles.draw.count",), severity="high", confidence=0.8, deps=(), fix=""):
    return {"claim": claim, "evidence": {"cards": list(cards), "fact_keys": list(keys)}, "severity": severity,
            "confidence": confidence, "depends_on_questions": list(deps), "fix_direction": fix}


def analysis(score=6, strengths=(), problems=(), none=False, line="roles.draw.count decides it"):
    return {"axis_score": {"score": score, "one_line": line}, "no_significant_problems": none,
            "strengths": list(strengths), "problems": list(problems)}


class ScriptedClient:
    """Replies by request label; each label has a FIFO of replies (dict -> json, str -> raw, Exception -> raised)."""

    def __init__(self, replies: dict[str, list[Any]] | None = None, default: Any = None, model_echo="fake-model"):
        self.replies = {k: list(v) for k, v in (replies or {}).items()}
        self.default = default
        self.requests: list[llm.LLMRequest] = []
        self.model_echo = model_echo

    def complete(self, request: llm.LLMRequest) -> llm.LLMResponse:
        self.requests.append(request)
        queue = self.replies.get(request.label)
        r = queue.pop(0) if queue else self.default
        if isinstance(r, Exception):
            raise r
        if r is None:
            raise AssertionError(f"no scripted reply for {request.label!r}")
        text = r if isinstance(r, str) else json.dumps(r)
        return llm.LLMResponse(text=text, usage=llm.Usage(tokens_in=1000, tokens_out=200, cached_tokens=400, cost_usd=0.01),
                               model=self.model_echo, latency_ms=12)


def profile() -> pp.PlayerProfile:
    return pp.profile_from_seed({"name": "tester", "builder_identity": {"text": "Builds more than pilots.", "tags": ["wacky"]},
                             "pet_cards": ["Skullclamp"], "review_preferences": {"tone": "direct", "skip": ["padding"]}})
