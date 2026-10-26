"""AI 'improve wording' for a single short deck-genre text value (Journal's Theme/Kindred custom
entries). Stateless and never saves: the caller shows the suggestion and the user accepts or
discards it (GN-TXT-1). Mirrors category_describe.py's chain/schema pattern but much smaller: one
short phrase in, one reworded phrase out, no rule/duplicate detection.
"""
from __future__ import annotations

from typing import Callable, Mapping, Optional

MAX_LEN = 32
KIND_NOUN = {"theme": "deck theme tag", "kindred": "creature-type tag", "archetype": "deck archetype tag"}

SYSTEM_PROMPT = (
    "You reword one short tag for a Magic: The Gathering Commander deck tool. "
    f"Rules:\n- At most {MAX_LEN} characters, plain text, no punctuation at the end, no quotes.\n"
    "- Keep the user's meaning and intent; just improve clarity, spelling, and word choice.\n"
    "- Return a single short phrase, not a sentence or description.\n"
    "- Never invent a different theme or creature type; only reword what was given.\n"
    "- Return JSON only: {\"text\": str}"
)

RESPONSE_SCHEMA = {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]}


class GenreTextImproveError(Exception):
    """Bad input (422)."""


class GenreTextImproveUnavailable(RuntimeError):
    """No LLM configured, or every provider failed / returned junk (503/502)."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = kind


def clean_request(payload) -> dict:
    if not isinstance(payload, Mapping):
        raise GenreTextImproveError("expected a JSON object")
    kind = payload.get("kind")
    if kind not in KIND_NOUN:
        raise GenreTextImproveError("kind must be one of: " + ", ".join(KIND_NOUN))
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise GenreTextImproveError("text is required")
    text = " ".join(text.split())
    if len(text) > MAX_LEN:
        raise GenreTextImproveError(f"text is at most {MAX_LEN} characters")
    return {"kind": kind, "text": text}


def build_prompt(req: dict) -> str:
    return f"Task: reword this {KIND_NOUN[req['kind']]}.\nCurrent text: {req['text']}"


def _trim(text: str) -> str:
    text = " ".join(text.split()).strip(" .")
    return text[:MAX_LEN] if len(text) > MAX_LEN else text


def validate_reply(data) -> str:
    if not isinstance(data, dict):
        raise GenreTextImproveUnavailable("bad_response", "the LLM returned no text")
    text = data.get("text")
    text = _trim(text) if isinstance(text, str) else ""
    if not text:
        raise GenreTextImproveUnavailable("bad_response", "the LLM returned no text")
    return text


def improve_text(chain, payload, on_call: Optional[Callable[[dict], None]] = None) -> dict:
    """{text, original, kind, provider, model}. Raises GenreTextImproveError (bad input, no model call)
    or GenreTextImproveUnavailable (kind: no_llm | llm_failed | bad_response)."""
    req = clean_request(payload)
    if chain is None or not chain.available():
        raise GenreTextImproveUnavailable("no_llm", "no LLM provider is configured")
    data, provider, model = chain.generate_json(SYSTEM_PROMPT, build_prompt(req), schema=RESPONSE_SCHEMA,
                                                purpose="genre_text_improve", on_call=on_call)
    if data is None:
        raise GenreTextImproveUnavailable("llm_failed", "every LLM provider failed")
    text = validate_reply(data)
    return {"text": text, "original": req["text"], "kind": req["kind"], "provider": provider, "model": model}
