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
