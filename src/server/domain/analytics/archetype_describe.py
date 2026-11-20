"""Draft or improve ONE archetype description with the LLM chain (ARCH-1).

Powers the "Draft / Enhance" button on the archetype editor's Description field. Stateless and
never saves: the caller gets text back and the user decides whether to keep it (nothing persists
until they hit Save). Mirrors category_describe.py's chain/schema/validator pattern, but the
archetype description is deck-facing Markdown (not a plain-text membership test), so it is its
own module.

A draft names no specific card and states no hard counts ("run 10 ramp spells") -- a validator
rejects replies that do, and the user sees the plain message.
"""
from __future__ import annotations

import re
from typing import Callable, List, Mapping, Optional

from . import archetypes as ar

MAX_DESCRIPTION = ar.MAX_DESCRIPTION  # 4000; the editor hint aims for <= 600

SYSTEM_PROMPT = (
    "You write the description of one deck ARCHETYPE (a strategy, like \"Voltron\" or \"Spellslinger\") "
    "for a Magic: The Gathering Commander deck tool. The description is shown on decks of that archetype "
    "and supports Markdown (**bold**, *italic*, `code`, lists).\n"
    "Rules:\n"
    "- Two to four sentences, under 600 characters. Describe the game plan and what the deck wants to do.\n"
    "- Never name a specific card. Never state hard counts (\"run 10 ramp spells\", \"at least 5 board wipes\"): "
    "describe the plan, not a checklist.\n"
    "- Never judge power level, price, popularity, EDHREC rank or salt.\n"
    "- If an existing description is given, improve it: keep the user's intent and facts, fix vagueness and "
    "wording, and do NOT invent numbers or requirements the user did not state.\n"
    "- Required roles, when given, are card-level jobs (produces/consumes tags) the archetype needs; you may "
    "reference the plan they imply, but never turn them into numeric requirements.\n"
    "- Return JSON only: {\"description\": str}"
)

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {"description": {"type": "STRING"}},
    "required": ["description"],
}

_HARD_COUNT = re.compile(
    r"(\b\d+\s*(?:[-–]|to)\s*\d+\s+(?:copies|copy|cards?|lands?|sources|pieces|slots)\b"
    r"|\b\d+\s+(?:copies|copy|cards?|lands?|sources|pieces|slots|of them|ramp|removal|counterspells?|"
    r"board ?wipes?|wraths?|draw spells?|pieces of)\b"
    r"|\b(?:run|play|pack|need|include|wants?|at least|at most|exactly|around|about|roughly)\s+\d+\b)",
    re.I)


class ArchetypeDescribeError(ar.ArchetypeError):
    """Bad input (422)."""


class ArchetypeDescribeUnavailable(RuntimeError):
    """No LLM configured, or every provider failed / returned junk (503/502)."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = kind


def _text(payload: Mapping, key: str, limit: int) -> str:
    v = payload.get(key)
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ArchetypeDescribeError(f"{key} must be text")
    v = " ".join(v.split())
    if len(v) > limit:
        raise ArchetypeDescribeError(f"{key} is at most {limit} characters")
    return v


def clean_request(payload) -> dict:
    """Validated {name, description, roles, mode}. Roles are tags from role_vocabulary."""
    if not isinstance(payload, Mapping):
        raise ArchetypeDescribeError("expected a JSON object")
    out = {"name": _text(payload, "name", ar.MAX_NAME),
           "description": _text(payload, "description", MAX_DESCRIPTION + 200)}
    roles = payload.get("roles") or []
    out["roles"] = [r for r in roles if isinstance(r, str)][:ar.MAX_ROLES] if isinstance(roles, list) else []
    if not (out["name"] or out["description"]):
        raise ArchetypeDescribeError("give the archetype a name, or a description to improve")
    out["mode"] = "improve" if out["description"] else "draft"
    return out


def build_prompt(req: dict, roles_vocab: Optional[List[dict]] = None,
                 archetypes: Optional[List[dict]] = None) -> str:
    lines: List[str] = []
    known = {v.get("tag"): v for v in roles_vocab or []}
    if req["roles"]:
        lines.append("Required roles of this archetype (tag: meaning):")
        for tag in req["roles"]:
            v = known.get(tag) or {}
            lines.append(f"- {tag}: {v.get('meaning', v.get('label', tag))}")
        lines.append("")
    others = [a for a in (archetypes or []) if a.get("name") and a.get("name").lower() != req["name"].lower()]
    if others:
        lines.append("Other archetypes in the registry (do not blur into them): "
                     + ", ".join(a["name"] for a in others[:20]))
        lines.append("")
    lines.append(f"Task: {req['mode']} an archetype description.")
    if req["name"]:
        lines.append(f"Archetype name: {req['name']}")
    if req["description"]:
        lines.append(f"Existing description to improve: {req['description']}")
    return "\n".join(lines)


def _trim(desc: str) -> str:
    desc = desc.strip()
    if len(desc) <= MAX_DESCRIPTION:
        return desc
    cut = desc[:MAX_DESCRIPTION]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind(".\n"))
    return cut[: end + 1] if end > MAX_DESCRIPTION // 2 else cut.rstrip()


def validate_reply(data, card_names: Optional[List[str]] = None) -> str:
    """The description text, or raise ArchetypeDescribeUnavailable (bad_response)."""
    desc = data.get("description") if isinstance(data, dict) else None
    if not isinstance(desc, str) or not desc.strip():
        raise ArchetypeDescribeUnavailable("bad_response", "the LLM returned no description")
    desc = _trim(desc)
    lower = desc.lower()
    for n in card_names or []:
        if len(n) >= 4 and re.search(r"\b" + re.escape(n.lower()) + r"\b", lower):
            raise ArchetypeDescribeUnavailable(
                "bad_response", "the draft named a specific card, which an archetype description must not")
    if _HARD_COUNT.search(desc):
        raise ArchetypeDescribeUnavailable(
            "bad_response", "the draft stated a hard count, which an archetype description must not")
    return desc


def describe_archetype(chain, payload, roles_vocab: Optional[List[dict]] = None,
                       card_names: Optional[List[str]] = None, archetypes: Optional[List[dict]] = None,
                       on_call: Optional[Callable[[dict], None]] = None) -> dict:
    """{description, mode, provider, model}. Raises ArchetypeDescribeError (bad input, no model call)
    or ArchetypeDescribeUnavailable (kind: no_llm | llm_failed | bad_response)."""
    req = clean_request(payload)
    if chain is None or not chain.available():
        raise ArchetypeDescribeUnavailable("no_llm", "no LLM provider is configured")
    data, provider, model = chain.generate_json(SYSTEM_PROMPT, build_prompt(req, roles_vocab, archetypes),
                                                schema=RESPONSE_SCHEMA, purpose="archetype_description",
                                                on_call=on_call)
    if data is None:
        raise ArchetypeDescribeUnavailable("llm_failed", "every LLM provider failed")
    desc = validate_reply(data, card_names)
    return {"description": desc, "mode": req["mode"], "provider": provider, "model": model}
