"""Generate or improve ONE pillar description with the LLM chain.

Powers the "generate / improve" button on the new/edit pillar form. Nothing is
saved: the caller gets text back and the user decides whether to keep it.
Inputs (any of): pillar name, a free-text prompt/hint, an existing description
to improve. At least one is required.
"""
from __future__ import annotations

from typing import Callable, List, Mapping, Optional

from .identity import DESC_MAX, NAME_MAX, IdentityError, _short_text, deck_names, live_pillars

PROMPT_MAX = 1000
CONTEXT_CARDS = 40

SYSTEM_PROMPT = (
    "You write the description of one strategic PILLAR of a Magic: The Gathering Commander deck. "
    "Cards are matched to pillars by embedding the description against card text, so be concrete: "
    "name the mechanics, card types, triggers and roles that serve the pillar.\n"
    "Rules:\n"
    f"- One or two sentences, at most {DESC_MAX} characters, plain text (no markdown, no lists).\n"
    "- If an existing description is given, improve it: keep the user's intent, fix vagueness, "
    "make it more concrete. If a prompt/hint is given, follow it.\n"
    "- Do not repeat other pillars of the deck; do not invent cards that are not real.\n"
    "- Return JSON only: {\"description\": str}"
)

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {"description": {"type": "STRING"}},
    "required": ["description"],
}


class PillarDescribeError(IdentityError):
    """Bad input (400/422 territory)."""


class PillarDescribeUnavailable(RuntimeError):
    """No LLM configured, or every provider failed / returned junk (503/502)."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = kind


def _text(payload: Mapping, key: str, limit: int) -> str:
    v = payload.get(key)
    if v is None:
        return ""
    if not isinstance(v, str):
        raise PillarDescribeError(f"{key} must be text")
    v = " ".join(v.split())
    if len(v) > limit:
        raise PillarDescribeError(f"{key} is at most {limit} characters")
    return v


def clean_request(payload) -> dict:
    if not isinstance(payload, Mapping):
        raise PillarDescribeError("expected a JSON object")
    # Existing text may be longer than DESC_MAX only if it was never saved; allow some slack.
    out = {"name": _text(payload, "name", NAME_MAX),
           "prompt": _text(payload, "prompt", PROMPT_MAX),
           "description": _text(payload, "description", PROMPT_MAX)}
    if not any(out.values()):
        raise PillarDescribeError("give a pillar name, a prompt, or a description to improve")
    out["mode"] = "improve" if out["description"] else "generate"
    return out


def build_prompt(deck: dict, card_texts: Mapping[str, dict], req: dict, ident: Optional[dict] = None,
                 exclude_id: Optional[str] = None) -> str:
    commanders = deck.get("commanders") or []
    lines = [f"Deck: {deck.get('name') or ''}", f"Commander(s): {', '.join(commanders) or 'none'}"]
    for n in commanders:
        c = card_texts.get(n) or {}
        lines.append(f"COMMANDER {n} | {c.get('type_line', '')} | {_short_text(c, 300)}")
    others = [p for p in live_pillars(ident) if p.get("id") != exclude_id]
    if others:
        lines += ["", "Other pillars of this deck (do not overlap):"]
        lines += [f"- {p['name']}: {p.get('description', '')}" for p in others]
    sample: List[str] = []
    for n in deck_names(deck):
        if n in commanders:
            continue
        c = card_texts.get(n)
        if not c or "Land" in (c.get("type_line") or "").split("//")[0]:
            continue
        sample.append(f"- {n} | {c.get('type_line', '')} | {_short_text(c, 120)}")
        if len(sample) >= CONTEXT_CARDS:
            break
    if sample:
        lines += ["", "Some cards in the deck:"] + sample
    lines += ["", f"Task: {req['mode']} a pillar description."]
    if req["name"]:
        lines.append(f"Pillar name: {req['name']}")
    if req["description"]:
        lines.append(f"Existing description to improve: {req['description']}")
    if req["prompt"]:
        lines.append(f"User prompt: {req['prompt']}")
    return "\n".join(lines)


def describe_pillar(deck: dict, chain, card_texts: Mapping[str, dict], payload, ident: Optional[dict] = None,
                    exclude_id: Optional[str] = None,
                    on_call: Optional[Callable[[dict], None]] = None) -> dict:
    """Returns {description, mode, provider, model}. Raises PillarDescribeError (bad input)
    or PillarDescribeUnavailable (kind: no_llm | llm_failed | bad_response)."""
    req = clean_request(payload)
    if chain is None or not chain.available():
        raise PillarDescribeUnavailable("no_llm", "no LLM provider is configured")
    data, provider, model = chain.generate_json(SYSTEM_PROMPT, build_prompt(deck, card_texts, req, ident, exclude_id),
                                                schema=RESPONSE_SCHEMA, purpose="pillar_description",
                                                on_call=on_call)
    if data is None:
        raise PillarDescribeUnavailable("llm_failed", "every LLM provider failed")
    desc = data.get("description") if isinstance(data, dict) else None
    if not isinstance(desc, str) or not " ".join(desc.split()):
        raise PillarDescribeUnavailable("bad_response", "the LLM returned no description")
    desc = " ".join(desc.split())
    if len(desc) > DESC_MAX:
        cut = desc[:DESC_MAX]
        end = max(cut.rfind(". "), cut.rfind("."))
        desc = cut[: end + 1] if end > DESC_MAX // 2 else cut.rstrip()
    return {"description": desc, "mode": req["mode"], "provider": provider, "model": model}
