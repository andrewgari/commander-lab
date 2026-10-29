"""JSON Schemas for what the models must return (Pass A brief, Pass B analysis).

These are the *model-facing* shapes. Ids, axis names and card refs (name + oracle_id) are added by
code when the output is merged into ``review_document.v1`` (``schemas/review_document.schema.json``);
the finding that reaches the document matches that schema exactly (a test pins it).
"""
from __future__ import annotations

SEVERITIES = ["low", "med", "high", "critical"]

# The review_document `finding` fields the pipeline produces (CONTRACT §5).
FINDING_FIELDS_IN_DOC = ("id", "axis", "claim", "evidence", "severity", "confidence")

_str = {"type": "string", "minLength": 1}
_names = {"type": "array", "items": {"type": "string", "minLength": 1}}

BRIEF_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["what_it_does", "central_concept", "win_paths", "success_criteria", "intentional_quirks",
                 "power_target", "questions_for_owner"],
    "properties": {
        "what_it_does": {**_str, "description": "2-4 sentences: what this deck is trying to do and how."},
        "central_concept": {
            "type": "object", "additionalProperties": False, "required": ["summary", "key_cards"],
            "properties": {"summary": {**_str, "description": "The one idea the deck is built around, one sentence."},
                           "key_cards": {**_names, "description": "Deck cards that carry the concept, exact names."}}},
        "win_paths": {
            "type": "array", "minItems": 1, "maxItems": 5,
            "items": {"type": "object", "additionalProperties": False, "required": ["path", "basis", "key_cards"],
                      "properties": {"path": _str, "basis": {"enum": ["stated", "inferred_from_list"]},
                                     "key_cards": _names}}},
        "success_criteria": {"type": "array", "maxItems": 6, "items": _str},
        "intentional_quirks": {
            "type": "array", "maxItems": 6,
            "items": {"type": "object", "additionalProperties": False, "required": ["quirk", "cards", "why_deliberate"],
                      "properties": {"quirk": _str, "cards": _names, "why_deliberate": _str}}},
        "power_target": {
            "type": "object", "additionalProperties": False, "required": ["stated", "bracket", "note"],
            "properties": {"stated": {"type": "boolean"},
                           "bracket": {"type": ["integer", "null"], "minimum": 1, "maximum": 5},
                           "note": {"type": "string"}}},
        "questions_for_owner": {
            "type": "array", "maxItems": 5,
            "items": {"type": "object", "additionalProperties": False, "required": ["question", "why_asked"],
                      "properties": {"question": _str, "why_asked": _str}}},
    },
}

_FINDING_IN = {
    "type": "object", "additionalProperties": False,
    "required": ["claim", "evidence", "severity", "confidence", "depends_on_questions", "fix_direction"],
    "properties": {
        "claim": {**_str, "description": "One or two sentences naming the cards and the mechanism."},
        "evidence": {"type": "object", "additionalProperties": False, "required": ["cards", "fact_keys"],
                     "properties": {"cards": {**_names, "description": "Deck cards, exact names from the card table."},
                                    "fact_keys": {**_names, "description": "Fact keys copied exactly from the FACTS block."}}},
        "severity": {"enum": SEVERITIES},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "depends_on_questions": {**_names, "description": "Ids of the brief's open questions this finding hinges on."},
        "fix_direction": {"type": "string", "description": "For problems: what kind of change would fix it, by function, never a card name. Empty for strengths."},
    },
}

ANALYSIS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["axis_score", "no_significant_problems", "strengths", "problems"],
    "properties": {
        "axis_score": {"type": "object", "additionalProperties": False, "required": ["score", "one_line"],
                       "properties": {"score": {"type": "integer", "minimum": 1, "maximum": 10},
                                      "one_line": {**_str, "description": "One line naming the fact or card that decided the score."}}},
        "no_significant_problems": {"type": "boolean"},
        "strengths": {"type": "array", "maxItems": 3, "items": _FINDING_IN},
        "problems": {"type": "array", "maxItems": 4, "items": _FINDING_IN},
    },
}


# ----------------------------------------------------------------------------------------------------------
# Pass C (recommendations) and Pass D (critic, regeneration). Model-facing shapes: adds are pool ids, never
# names; ids, prices, ownership, budget text and card refs are added by code.
# ----------------------------------------------------------------------------------------------------------
BRACKET_EFFECTS = ["none", "raises", "lowers"]
MAX_SWAPS = 8
TOP_CHANGES = 3

SWAP_IN = {
    "type": "object", "additionalProperties": False,
    "required": ["cut", "add_id", "alternative_id", "resolves_finding_id", "tradeoff", "bracket_effect"],
    "properties": {
        "cut": {**_str, "description": "Exact name of the deck card to remove, as spelled in the card table."},
        "add_id": {**_str, "description": "Pool id (pc-N) of the card to add. Never a card name."},
        "alternative_id": {"type": ["string", "null"], "description": "Pool id (pc-N) of ONE other pool card that could fill the same slot; null only if no other pool card fits."},
        "resolves_finding_id": {**_str, "description": "Id of the PROBLEM finding this swap fixes."},
        "tradeoff": {**_str, "description": "What the deck loses by cutting the card (name the mechanism) and what it gains; honest, one to three sentences."},
        "bracket_effect": {"enum": BRACKET_EFFECTS, "description": "Does this swap raise or lower the deck's bracket? 'raises' if the add is a Game Changer, tutor or combo piece the cut is not."},
    },
}

RECOMMEND_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["swaps", "top_changes", "cuts_to_consider"],
    "properties": {
        "swaps": {"type": "array", "maxItems": MAX_SWAPS, "items": SWAP_IN,
                  "description": "Ranked, most valuable first. Empty when the deck has no problem worth a swap."},
        "top_changes": {
            "type": "object", "additionalProperties": False, "required": ["ranks", "why_these_first"],
            "properties": {"ranks": {"type": "array", "maxItems": TOP_CHANGES, "items": {"type": "integer", "minimum": 1},
                                     "description": "1-based positions in `swaps` of the three changes to make first (all of them if there are fewer than three)."},
                           "why_these_first": {"type": "string", "description": "Why these changes come first, specific to this deck. Empty only if there are no swaps."}}},
        "cuts_to_consider": {
            "type": "array", "maxItems": 5,
            "items": {"type": "object", "additionalProperties": False, "required": ["card", "reason", "fact_keys"],
                      "properties": {"card": _str, "reason": _str,
                                     "fact_keys": {**_names, "description": "Fact keys copied exactly from the FACTS block that support the cut."}}},
            "description": "Cards the evidence says are dead weight, not already covered by a swap. An empty list is the right answer when nothing is clearly weak."},
    },
}

# Regeneration of ONE item (card 09). The model may also withdraw it.
SWAP_REGEN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["action", "swap", "note"],
    "properties": {"action": {"enum": ["replace", "withdraw"]},
                   "swap": {"anyOf": [SWAP_IN, {"type": "null"}], "description": "The replacement swap, or null when withdrawing."},
                   "note": {"type": "string", "description": "One line: what you changed, or why no valid swap exists."}},
}
FINDING_REGEN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["action", "finding", "note"],
    "properties": {"action": {"enum": ["rewrite", "withdraw"]},
                   "finding": {"anyOf": [_FINDING_IN, {"type": "null"}], "description": "The rewritten finding, or null when withdrawing."},
                   "note": {"type": "string", "description": "One line: what you changed, or why the claim cannot be supported."}},
}

CRITIC_CATEGORIES = ["unsupported_claim", "generic", "contradicts_brief", "tradeoff_missing"]
CRITIC_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["verdicts"],
    "properties": {"verdicts": {
        "type": "array",
        "items": {"type": "object", "additionalProperties": False,
                  "required": ["item_id", "verdict", "category", "quote", "reason"],
                  "properties": {
                      "item_id": _str,
                      "verdict": {"enum": ["ok", "flag"]},
                      "category": {"enum": [*CRITIC_CATEGORIES, "none"], "description": "'none' exactly when verdict is ok."},
                      "quote": {"type": "string", "description": "For a flag: the offending words copied VERBATIM from the item. Empty for ok."},
                      "reason": {**_str, "description": "One sentence. For a flag: what in the dossier fails to support it. For ok: what you checked."}}},
        "description": "Exactly one verdict per item, in the order given."}},
}

CUT_REGEN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["action", "cut", "note"],
    "properties": {"action": {"enum": ["replace", "withdraw"]},
                   "cut": {"anyOf": [RECOMMEND_SCHEMA["properties"]["cuts_to_consider"]["items"], {"type": "null"}]},
                   "note": {"type": "string"}},
}
TOP_REGEN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["ranks", "why_these_first"],
    "properties": RECOMMEND_SCHEMA["properties"]["top_changes"]["properties"],
}
