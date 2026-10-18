"""Build the packet from a deck + form + card metadata, call the model, validate the reply.

`ask` is any callable (system, user) -> str (production: the oracle client). Nothing here touches the network
or Redis, so it is testable with a fake; `app.py` wires `ask` and supplies `card_meta`."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from . import prompts
from .forms import FormError, clean_form, missing_required

Ask = Callable[[str, str], str]

DESCRIPTION_MAX_WORDS = 90
PRIMER_MAX_WORDS = 900
MAX_CARDS_IN_PROMPT = 100
PRIMER_SECTIONS = ["Overview", "Game Plan", "Key Cards", "Combos and Win Lines", "Mulligans and Opening Hands",
                   "Piloting Tips", "Weaknesses", "Upgrades and Swaps"]
_TYPE_ORDER = ["Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker", "Battle", "Land"]


class WriteupError(ValueError):
    """Bad inputs or an unusable model reply. `kind` is machine-readable for the UI."""

    def __init__(self, message: str, kind: str = "invalid"):
        super().__init__(message)
        self.kind = kind


def _key(name: str) -> str:
    return " ".join(name.lower().split())


def _main_type(type_line: str) -> str:
    for t in _TYPE_ORDER:
        if t.lower() in (type_line or "").lower():
            return t
    return "Other"


def summarize_cards(card_meta: Sequence[Mapping[str, Any]]) -> dict:
    """card_meta rows: {name, type_line?, tags?, mana_value?}. Returns type counts, top tags and a bounded card list."""
    types: Counter = Counter()
    tags: Counter = Counter()
    rows: List[str] = []
    for c in card_meta:
        name = c.get("name")
        if not name:
            continue
        t = _main_type(c.get("type_line") or "")
        types[t] += 1
        ctags = [str(x) for x in (c.get("tags") or []) if x]
        tags.update(ctags)
        if len(rows) < MAX_CARDS_IN_PROMPT and t != "Land":
            rows.append(f"- {name} ({t}{', ' + ', '.join(ctags[:3]) if ctags else ''})")
    return {"types": ", ".join(f"{n} {t}" for t, n in types.most_common()) or "unknown",
            "tags": ", ".join(f"{t} ({n})" for t, n in tags.most_common(10)) or "none",
            "cards": "\n".join(rows) or "(none)", "count": sum(types.values())}


def _notes(form: Mapping[str, Any]) -> str:
    from .forms import PRIMER_FIELDS
    lines = []
    for f in PRIMER_FIELDS:
        if f["key"] == "key_cards" or f["key"] not in form:
            continue
        lines.append(f"{f['label']} {form[f['key']]}")
    return "\n".join(lines) or "(none given)"


def build_user_prompt(deck: Mapping[str, Any], card_meta: Sequence[Mapping[str, Any]], form: Mapping[str, Any],
                      *, identity: Optional[str] = None, bracket: Optional[str] = None) -> str:
    s = summarize_cards(card_meta)
    key = form.get("key_cards") or []
    return prompts.USER_TEMPLATE.format(
        deck_name=deck.get("name") or "Untitled", commanders=", ".join(deck.get("commanders") or []) or "unknown",
        identity=identity or "unknown", bracket=bracket or "unspecified", card_count=s["count"] or "unknown",
        notes=_notes(form), types=s["types"], tags=s["tags"],
        key_cards="\n".join(f"- {k}" for k in key) or "(none given)", cards=s["cards"])


def _known_names(deck: Mapping[str, Any], card_meta: Sequence[Mapping[str, Any]]) -> set:
    names = {_key(c["name"]) for c in card_meta if c.get("name")}
    names |= {_key(n) for n in deck.get("commanders") or []}
    return names


def _check_key_cards(form: Mapping[str, Any], known: set) -> List[str]:
    """Return key cards the owner typed that aren't in the deck (surfaced as a warning, not an error)."""
    return [k for k in form.get("key_cards") or [] if known and _key(k) not in known]


def _strip_fences(text: str) -> str:
    t = text.strip()
    m = re.match(r"^```(?:markdown|md|text)?\s*\n(.*?)\n```$", t, re.S)
    return (m.group(1) if m else t).strip()


def _words(text: str) -> int:
    return len(text.split())


def validate_description(text: str, max_words: int = DESCRIPTION_MAX_WORDS) -> str:
    t = _strip_fences(text or "")
    t = re.sub(r"^(description|deck description)\s*:\s*", "", t, flags=re.I).strip()
    if not t:
        raise WriteupError("The model returned an empty description.", "empty")
    if "\n#" in "\n" + t or t.startswith("#"):
        raise WriteupError("The model returned headings instead of a description.", "format")
    t = " ".join(t.split())
    if _words(t) > max_words * 1.5:
        raise WriteupError("The model's description was far too long.", "too_long")
    return t


def validate_primer(text: str, max_words: int = PRIMER_MAX_WORDS) -> str:
    t = _strip_fences(text or "")
    if not t:
        raise WriteupError("The model returned an empty primer.", "empty")
    heads = [h.strip() for h in re.findall(r"^##\s+(.+?)\s*$", t, re.M)]
    if not heads:
        raise WriteupError("The model's primer had no sections.", "format")
    unknown = [h for h in heads if h not in PRIMER_SECTIONS]
    if unknown:
        raise WriteupError(f"The model's primer had unexpected sections: {', '.join(unknown)}.", "format")
    if _words(t) > max_words * 1.5:
        raise WriteupError("The model's primer was far too long.", "too_long")
    return t


def _result(text: str, form: Mapping[str, Any], known: set) -> dict:
    return {"text": text, "warnings": [f"Key card not found in this deck: {k}" for k in _check_key_cards(form, known)]}


def generate_description(deck: Mapping[str, Any], card_meta: Sequence[Mapping[str, Any]], ask: Ask,
                         form: Optional[Mapping[str, Any]] = None, *, identity: Optional[str] = None,
                         bracket: Optional[str] = None) -> dict:
    """Description from card data alone; form is optional (theme/tone/key_cards sharpen it). Nothing is saved."""
    if not (deck.get("commanders") or card_meta):
        raise WriteupError("The deck has no cards to describe.", "no_cards")
    try:
        cleaned = clean_form(form or {})
    except FormError as exc:
        raise WriteupError(str(exc)) from exc
    system = prompts.DESCRIPTION_SYSTEM.format(max_words=DESCRIPTION_MAX_WORDS)
    reply = ask(system, build_user_prompt(deck, card_meta, cleaned, identity=identity, bracket=bracket))
    return _result(validate_description(reply), cleaned, _known_names(deck, card_meta))


def generate_primer(deck: Mapping[str, Any], card_meta: Sequence[Mapping[str, Any]], ask: Ask,
                    form: Mapping[str, Any], *, identity: Optional[str] = None,
                    bracket: Optional[str] = None) -> dict:
    """Primer from the guided form. Requires the required form fields. Nothing is saved."""
    try:
        cleaned = clean_form(form)
    except FormError as exc:
        raise WriteupError(str(exc)) from exc
    missing = missing_required(cleaned)
    if missing:
        raise WriteupError(f"Fill in: {', '.join(missing)}.", "missing_fields")
    system = prompts.PRIMER_SYSTEM.format(max_words=PRIMER_MAX_WORDS)
    reply = ask(system, build_user_prompt(deck, card_meta, cleaned, identity=identity, bracket=bracket))
    return _result(validate_primer(reply), cleaned, _known_names(deck, card_meta))


def card_meta_for_deck(r, deck: Mapping[str, Any]) -> List[dict]:
    """Deck cards + their tags from Redis (tag_store), as {name, type_line, tags} rows."""
    import tags as tag_store
    import registry

    try:
        tag_map = tag_store.get_deck_card_tags(r, registry.registry_id_of(deck))
    except Exception:
        tag_map = {}
    rows = []
    for c in deck.get("cards") or []:
        if not isinstance(c, Mapping) or not c.get("name"):
            continue
        types = c.get("types") or c.get("type_line") or ""
        if isinstance(types, (list, tuple)):
            types = " ".join(str(t) for t in types)
        tags = [t.get("tag") if isinstance(t, Mapping) else t for t in tag_map.get(c["name"], [])]
        rows.append({"name": c["name"], "type_line": types, "tags": [t for t in tags if t]})
    return rows
