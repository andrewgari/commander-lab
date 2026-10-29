"""Tunables for Single Card Review. Every value is a heuristic from docs/single-card-review-spec.md §5."""
from __future__ import annotations

import os

# Debate (§3.2)
DEFAULT_ROUNDS = 2
MAX_ROUNDS = 3
MIN_ROUNDS = 2
ADVOCATE_TEMPERATURE = 0.3
JUDGE_TEMPERATURE = 0.0
CLAIMS_R1 = 3
REBUTTALS_R2 = 2
NEW_CLAIMS_R2 = 1
CLAIM_WORDS = 60

# Rubric prior and confidence (§2.2, §3.6)
PRIOR_CENTER = 50.0
PRIOR_SCALE = 8.0
JUDGE_CONF_MIN, JUDGE_CONF_MAX = 0.50, 0.95
CAP_EDHREC_MISSING = 0.70
CAP_JOURNAL_UNCONFIRMED = 0.65
CAP_FALLBACK = 0.60
CONF_FLOOR = 0.50
GATE_ILLEGAL_CONFIDENCE = 0.99

# Judge brief: if the deck lacks the card's role, a pure function change is not allowed (§3.5 step 4,
# approximated: the evaluation has role_need (0..1) rather than floor counts).
ROLE_NEED_FORCE_LIKE = 0.5

# Replacements (§4)
MIN_SUGGESTIONS = 3
MAX_SUGGESTIONS = 5
MIXED_SPLIT = (3, 2)  # like-for-like, function-change
FREE_AGENT_MARGIN = 10.0
REP_WEIGHTS = {"RoleFit": 0.30, "PillarFit": 0.25, "E_c": 0.15, "Own": 0.10}  # CurveFit/Budget: no data yet
PILLAR_SYNERGY_HI, PILLAR_SYNERGY_MID = 0.30, 0.10
PILLAR_GATE = 60
STAPLE_ROLES = {"ramp", "card_advantage", "targeted_removal", "board_wipe", "protection"}
RATIONALE_MAX = 200

BANDS = [(80, "core"), (65, "solid"), (50, "replaceable"), (35, "weak"), (0, "cut_candidate")]

TIERS = ["spirit", "role_math", "curve_mana", "table", "popularity"]
# Evidence-ID prefix -> tier the judge uses to re-tier a claim (§3.4.3 step 2).
PREFIX_TIER = {"J": "spirit", "A": "spirit", "X": "spirit", "R": "role_math", "M": "role_math",
               "K": "curve_mana", "T": "table", "E": "popularity", "H": "popularity", "S": "popularity",
               "C": "popularity", "P": "popularity"}


def rounds_from_env(default: int = DEFAULT_ROUNDS) -> int:
    try:
        n = int(os.environ.get("CARD_REVIEW_ROUNDS", default))
    except ValueError:
        n = default
    return max(MIN_ROUNDS, min(MAX_ROUNDS, n))
