"""Draft or improve ONE custom category description with the LLM chain (categories-v1 section 5).

Stateless and never saves: the caller gets text back and the user decides. Custom categories only:
core definitions are fixed seed text. The reply is a *membership test* ("Cards that ..."), names no cards,
and doesn't judge power, price, EDHREC rank or salt. The model may also return a suggestion ``rule``
(validated here; a bad one is dropped and listed in ``flagged``) or ``duplicate_of`` a core id.
Alias names ("Draw", "Wincon") are refused before any model call.
"""
from __future__ import annotations

import re
from typing import Callable, List, Mapping, Optional

from . import categories as cat

HINT_MAX = 400
CONTEXT_CARDS = 30

SYSTEM_PROMPT = (
    "You write the description of one custom CATEGORY (a card role) for a Magic: The Gathering Commander deck tool. "
    "A description is a membership test: someone reading it can decide whether a given card belongs.\n"
    "Rules:\n"
    f"- At most {cat.DESC_MAX} characters, plain text (no markdown, no lists).\n"
    "- Start with \"Cards that\" and describe the JOB the cards do. Optionally end with \"Not: ...\" naming what does not count.\n"
    "- Never name any card. Never judge power level, price, popularity, EDHREC rank or salt.\n"
    "- If an existing description is given, improve it and keep the user's intent. If a hint is given, follow it.\n"
    "- If the name is really one of the CORE categories listed, set duplicate_of to that core id.\n"
    "- Optionally suggest ONE rule that finds candidate cards: \"autotag:<theme>\" (theme from the allowed list), "
    "\"kindred:<Creature type>\" or \"type:<type line word>\". Use null if none fits.\n"
    "- Return JSON only: {\"description\": str, \"rule\": str|null, \"duplicate_of\": str|null}"
)

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {"description": {"type": "STRING"}, "rule": {"type": "STRING", "nullable": True},
                   "duplicate_of": {"type": "STRING", "nullable": True}},
    "required": ["description"],
}

_JUDGE = re.compile(r"\b(powerful|strong(?:est)?|best|broken|op|staple|salt(?:y)?|edhrec|budget|cheap(?:est)?|"
                    r"expensive|price|popular|meta)\b", re.I)


class CategoryDescribeError(cat.CategoryError):
    """Bad input (422)."""


class CategoryDescribeUnavailable(RuntimeError):
    """No LLM configured, or every provider failed / returned junk (503/502)."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = kind


def _text(payload: Mapping, key: str, limit: int) -> str:
    v = payload.get(key)
    if v is None:
        return ""
    if not isinstance(v, str):
        raise CategoryDescribeError(f"{key} must be text", 422)
    v = " ".join(v.split())
    if len(v) > limit:
        raise CategoryDescribeError(f"{key} is at most {limit} characters", 422)
    return v


def clean_request(payload) -> dict:
    """Validated {name, description, hint, group, avoid, mode}. An alias name raises before any model call."""
    if not isinstance(payload, Mapping):
        raise CategoryDescribeError("expected a JSON object", 422)
    out = {"name": _text(payload, "name", cat.NAME_MAX), "description": _text(payload, "description", cat.DESC_MAX + 200),
           "hint": _text(payload, "hint", HINT_MAX)}
    group = payload.get("group") or ""
    out["group"] = group if group in cat.GROUP_IDS else ""
    avoid = payload.get("avoid") or []
    out["avoid"] = [" ".join(a.split())[:cat.DESC_MAX] for a in avoid if isinstance(a, str)][:5] if isinstance(avoid, list) else []
    if not (out["name"] or out["description"] or out["hint"]):
        raise CategoryDescribeError("give a name, a hint, or a description to improve", 422)
    if out["name"]:
        hit = cat.alias_hit(out["name"])
        if hit:
            core_id = hit.get("core_id", "removal")
            c = cat.CORE_BY_ID[core_id]
            msg = ("That's the Interaction group (Removal, Counterspells, Board Wipes)." if "group" in hit
                   else f"That's the core category {c['name']}. Use it instead.")
            raise cat.CategoryError(msg, 422, "alias", core_id=core_id)
    out["mode"] = "improve" if out["description"] else "draft"
    return out


def build_prompt(req: dict, deck: Optional[dict] = None, card_texts: Optional[Mapping[str, dict]] = None,
                 deck_tags: Optional[List[str]] = None, pillars: Optional[List[dict]] = None) -> str:
    lines = ["CORE categories (id: definition):"]
    lines += [f"- {c['id']}: {c['description']}" for c in cat.CORE]
    lines += ["", "Allowed autotag themes: " + ", ".join(cat.theme_tags())]
    if deck is not None:
        texts = card_texts or {}
        cmdrs = [n for n in deck.get("commanders") or [] if isinstance(n, str)]
        lines += ["", f"Deck context. Commander(s): {', '.join(cmdrs) or 'none'}"]
        for n in cmdrs:
            c = texts.get(n) or {}
            lines.append(f"COMMANDER {n} | {c.get('type_line', '')} | {' '.join((c.get('oracle_text') or '').split())[:300]}")
        if deck_tags:
            lines.append("Deck themes: " + ", ".join(deck_tags))
        if pillars:
            lines += ["Pillars of this deck (a category is a card-level job, not a pillar):"]
            lines += [f"- {p.get('name')}: {p.get('description', '')}" for p in pillars]
        sample = []
        for n in cat.deck_card_names(deck):
            c = texts.get(n)
            if not c or "land" in (c.get("type_line") or "").split("//")[0].lower():
                continue
            sample.append(f"- {n} | {c.get('type_line', '')}")
            if len(sample) >= CONTEXT_CARDS:
                break
        if sample:
            lines += ["Some cards in the deck (context only: never name them in the description):"] + sample
    lines += ["", f"Task: {req['mode']} a category description."]
    if req["name"]:
        lines.append(f"Category name: {req['name']}")
    if req["group"]:
        lines.append(f"Group: {req['group']}")
    if req["description"]:
        lines.append(f"Existing description to improve: {req['description']}")
    if req["hint"]:
        lines.append(f"User hint: {req['hint']}")
    if req["avoid"]:
        lines.append("Write something different from these earlier drafts:")
        lines += [f"- {a}" for a in req["avoid"]]
    return "\n".join(lines)


def _trim(desc: str) -> str:
    desc = " ".join(desc.split())
    if len(desc) <= cat.DESC_MAX:
        return desc
    cut = desc[:cat.DESC_MAX]
    end = cut.rfind(".")
    return cut[: end + 1] if end > cat.DESC_MAX // 2 else cut.rstrip()


def validate_reply(data, req: dict, card_names: Optional[List[str]] = None) -> dict:
    """Turn the model's JSON into {description, rule, duplicate_of, flagged}. Raises Unavailable(bad_response)."""
    if not isinstance(data, dict):
        raise CategoryDescribeUnavailable("bad_response", "the LLM returned no description")
    dup = data.get("duplicate_of")
    dup = dup if isinstance(dup, str) and dup in cat.CORE_BY_ID else None
    desc = data.get("description")
    desc = _trim(desc) if isinstance(desc, str) else ""
    if not desc and not dup:
        raise CategoryDescribeUnavailable("bad_response", "the LLM returned no description")
    lower = desc.lower()
    for n in card_names or []:
        if len(n) >= 4 and re.search(r"\b" + re.escape(n.lower()) + r"\b", lower):
            raise CategoryDescribeUnavailable("bad_response", "the draft named a card, which a membership test must not")
    if _JUDGE.search(desc):
        raise CategoryDescribeUnavailable("bad_response", "the draft judged power or price instead of describing a job")
    flagged, rule = [], None
    raw = data.get("rule")
    if isinstance(raw, str) and raw.strip():
        try:
            rule = cat.validate_rule(raw) or None
        except cat.CategoryError as exc:
            flagged.append({"rule": raw[:60], "reason": str(exc)})
    out = {"description": desc, "rule": rule, "duplicate_of": dup, "mode": req["mode"]}
    if flagged:
        out["flagged"] = flagged
    return out


def describe_category(chain, payload, deck: Optional[dict] = None, card_texts: Optional[Mapping[str, dict]] = None,
                      deck_tags: Optional[List[str]] = None, pillars: Optional[List[dict]] = None,
                      on_call: Optional[Callable[[dict], None]] = None) -> dict:
    """{description, rule, duplicate_of, flagged?, mode, provider, model}. Raises CategoryError (alias/bad input, no
    model call) or CategoryDescribeUnavailable (kind: no_llm | llm_failed | bad_response)."""
    req = clean_request(payload)
    if chain is None or not chain.available():
        raise CategoryDescribeUnavailable("no_llm", "no LLM provider is configured")
    data, provider, model = chain.generate_json(SYSTEM_PROMPT, build_prompt(req, deck, card_texts, deck_tags, pillars),
                                                schema=RESPONSE_SCHEMA, purpose="category_description",
                                                on_call=on_call)
    if data is None:
        raise CategoryDescribeUnavailable("llm_failed", "every LLM provider failed")
    out = validate_reply(data, req, cat.deck_card_names(deck) if deck is not None else None)
    return {**out, "provider": provider, "model": model}
