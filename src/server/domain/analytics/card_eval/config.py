"""
Card evaluation tuning knobs, all in one place (docs/CARD_EVAL.md).

The domain rubric (docs/REVIEW_SPEC.md and the oracle deck-review rubric) is being written
separately; to align the score with it, edit ONLY this file (or set CARD_EVAL_WEIGHTS to a JSON
object overriding any ScoringWeights field, e.g. '{"inclusion": 0.5, "synergy": 0.2}').

strength (0..100) = 100 * sum(w_i * c_i) / sum(w_i) over the components that have data, so a
missing signal (no EDHREC page, HF offline) re-normalises instead of dragging the score to 0.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from typing import Dict, Tuple


@dataclass(frozen=True)
class ScoringWeights:
    inclusion: float = 0.40        # EDHREC inclusion % with this commander
    synergy: float = 0.25          # EDHREC synergy score
    role_confidence: float = 0.20  # HF model confidence in the card's best role
    role_need: float = 0.15        # does the deck lack this role (1) or have plenty (0)
    # component scaling
    inclusion_full: float = 0.60       # inclusion at/above this -> component 1.0
    synergy_floor: float = -0.25       # synergy at/below this -> 0.0
    synergy_ceil: float = 0.50         # synergy at/above this -> 1.0
    # pool ranking: score = pool_synergy*synergy_c + pool_inclusion*inclusion_c (+ owned bonus)
    pool_synergy: float = 0.55
    pool_inclusion: float = 0.45
    physical_owned_bonus: float = 0.10  # candidate already in the collection (physical decks only)

    @classmethod
    def from_env(cls, env=None) -> "ScoringWeights":
        env = os.environ if env is None else env
        raw = env.get("CARD_EVAL_WEIGHTS")
        if not raw:
            return cls()
        try:
            data = json.loads(raw)
        except ValueError:
            return cls()
        names = {f.name for f in fields(cls)}
        clean = {k: float(v) for k, v in (data or {}).items() if k in names and isinstance(v, (int, float))}
        return cls(**clean)


# Roles the model chooses between (zero-shot labels) -> human-readable label sent to the model.
ROLE_LABELS: Dict[str, str] = {
    "ramp": "mana ramp or mana fixing",
    "card_advantage": "card draw or card advantage",
    "targeted_removal": "targeted removal or counterspell",
    "board_wipe": "board wipe or mass removal",
    "protection": "protection for your permanents or your commander",
    "tutor": "tutor that searches the library",
    "recursion": "graveyard recursion",
    "token_maker": "token generation",
    "threat": "creature or win condition threat",
    "engine": "synergy engine or value engine",
    "utility_land": "land with utility",
}

# EDHREC cardlist tag -> role prior (a card listed in that EDHREC slot "plays" that role).
EDHREC_TAG_ROLES: Dict[str, Tuple[str, ...]] = {
    "manaartifacts": ("ramp",),
    "lands": ("utility_land",),
    "utilitylands": ("utility_land",),
    "creatures": ("threat",),
}

# Which composition.classify_card rows map to our roles.
COMPOSITION_ROLES: Dict[str, str] = {
    "ramp": "ramp", "card_advantage": "card_advantage",
    "targeted_disruption": "targeted_removal", "mass_disruption": "board_wipe",
}

# How many roles a card gets, and the minimum model confidence for one to count.
MAX_ROLES = 3
MIN_ROLE_CONFIDENCE = 0.50
# A role is "plentiful" in the deck at this many cards (role_need component reaches 0).
ROLE_SATURATION = 10

# Candidate pool shaping.
POOL_SCAN_LIMIT = 80      # best EDHREC cards looked up on Scryfall for colour/legality filtering
POOL_SAME_ROLE = 12
POOL_OTHER_ROLE = 8
