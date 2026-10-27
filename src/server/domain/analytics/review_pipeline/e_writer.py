"""Pass E output checks: prose may only restate verified Pass D evidence."""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from src.server.domain.analytics.review_pipeline.names import normalize

_NUMBER = re.compile(r"(?<![\w])\d+(?:\.\d+)?%?(?![\w])")
_SWAP_ID = re.compile(r"\bsw-[a-z0-9_-]+\b", re.I)
_GRADE_CLAIM = re.compile(r"\bgrade\s*(?:is\s+|:\s*)?([A-F])\b", re.I)
_CONFIDENCE_CLAIM = re.compile(r"\bconfidence\s*(?:is\s+|:\s*)?(0(?:\.\d+)?|1(?:\.0+)?)\b", re.I)
_NON_NAME_TITLE_WORDS = {"a", "an", "the", "this", "that", "these", "those", "it", "its", "your", "you", "we", "our", "they", "their", "with", "for", "from", "while", "because", "when", "if", "cards", "deck", "mana", "overall"}
_TITLE_PHRASE = re.compile(r"\b(?:[A-Z][a-z]+(?:['’][A-Za-z]+)?)(?:\s+(?:of|the|and|to|in|for|with|at|on|a|an|[A-Z][a-z]+(?:['’][A-Za-z]+)?)){0,5}\b")


def _walk_verified(value: Any):
    """Walk only reviewable evidence; verifier diagnostics are not writer input."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "omitted":
                continue
            yield key, item
            yield from _walk_verified(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_verified(item)


def _verified_names(data: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for key, value in _walk_verified(data):
        if key == "name" and isinstance(value, str):
            full = value.strip()
            names.add(normalize(full))
            if "//" in full:
                names.update(normalize(face.strip()) for face in full.split("//"))
    return names


def _verified_numbers(data: Mapping[str, Any]) -> set[str]:
    return {m.group(0).rstrip("%") for _, value in _walk_verified(data) if isinstance(value, (str, int, float))
            for m in _NUMBER.finditer(str(value))}


def check_writer_output(text: str, verified: Mapping[str, Any]) -> list[str]:
    """Return policy violations; verified contains only the completed, Pass-D-approved payload."""
    errors: list[str] = []
    allowed_names = _verified_names(verified)
    try:
        output = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        output = None
    source_verdict = verified.get("verdict") if isinstance(verified.get("verdict"), Mapping) else {}
    if not source_verdict or "grade" not in source_verdict or "confidence" not in source_verdict:
        errors.append("verified input is missing grade or confidence")
    if not isinstance(output, Mapping) or not isinstance(output.get("verdict"), Mapping):
        errors.append("writer output must be JSON with a verdict object")
        output_verdict = None
    else:
        output_verdict = output["verdict"]
    if isinstance(output_verdict, Mapping) and source_verdict:
        for field in ("grade", "confidence"):
            if field not in output_verdict or output_verdict.get(field) != source_verdict.get(field):
                errors.append(f"verdict {field} does not exactly match the verified input")
        for match in _GRADE_CLAIM.finditer(text):
            if match.group(1).upper() != str(source_verdict["grade"]).upper():
                errors.append("verdict grade claim does not match the verified input")
        for match in _CONFIDENCE_CLAIM.finditer(text):
            try:
                matches_source = float(match.group(1)) == float(source_verdict["confidence"])
            except (TypeError, ValueError):
                matches_source = False
            if not matches_source:
                errors.append("verdict confidence claim does not match the verified input")
    for match in _TITLE_PHRASE.finditer(text):
        phrase = match.group(0).strip()
        # Sentence-initial function words are ordinary prose, not candidate card names.
        if len(phrase.split()) == 1 and phrase.casefold() in _NON_NAME_TITLE_WORDS:
            continue
        if normalize(phrase) in allowed_names:
            continue
        errors.append(f"unverified card name or proper-name phrase: {phrase}")
    allowed_numbers = _verified_numbers(verified)
    for match in _NUMBER.finditer(text):
        number = match.group(0).rstrip("%")
        if number not in allowed_numbers:
            errors.append(f"unverified number: {match.group(0)}")
    survivors = {str(item.get("id")) for item in verified.get("swaps", []) if isinstance(item, Mapping)}
    if isinstance(verified.get("top_changes"), Mapping):
        survivors &= set(map(str, verified["top_changes"].get("swap_ids", [])))
    for swap_id in _SWAP_ID.findall(text):
        if swap_id not in survivors:
            errors.append(f"unverified swap id: {swap_id}")
    return errors


def generate_checked(writer, verified: Mapping[str, Any], *, max_attempts: int = 2) -> str | None:
    """Call writer with the verified payload, retry policy violations at most twice, then drop."""
    for _ in range(max(0, max_attempts) + 1):
        text = writer(verified)
        if not check_writer_output(text, verified):
            return text
    return None


def render_user_input(verified: Mapping[str, Any], review_preferences: str = "") -> str:
    """Render only the verified JSON and the explicitly supplied profile voice preference."""
    from src.server.domain.analytics.review_pipeline import prompts
    import json

    template = prompts.load("e_writer")
    return template.render("user", verified_json=json.dumps(verified, ensure_ascii=False, sort_keys=True),
                           review_preferences=review_preferences or "direct, specific, no padding, no cheerleading")
