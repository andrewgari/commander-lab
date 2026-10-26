"""Build the packet from a deck + form + card metadata, call the model, validate the reply.

`ask` is any callable (system, user) -> str (production: the oracle client). Nothing here touches the network
or Redis, so it is testable with a fake; `app.py` wires `ask` and supplies `card_meta`, `spirit` and `table_facts`
(see context.py for the Redis-backed builders of those)."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from . import prompts
from .context import enrich_card_meta, mana_value_of, spirit_for_deck, table_facts_for_deck  # noqa: F401 (re-export)
from .forms import PRIMER_FIELDS, FormError, clean_form, missing_required
from .sanitize import html_to_text, markdown_to_html, sanitize_primer_html  # noqa: F401 (re-export)

Ask = Callable[[str, str], str]

DESCRIPTION_MAX_WORDS = 90
DESCRIPTION_MAX_CHARS = 280
PRIMER_MAX_WORDS = 900
LENGTH_WORDS = {"brief": 450, "standard": PRIMER_MAX_WORDS, "deep": 1400}
PRIMER_MIN_CHARS = 500  # floor for any single generation call (full primer or one-section redraft)
MAX_CARDS_IN_PROMPT = 100
MAX_ORACLE_CARDS = 14
ORACLE_TEXT_CHARS = 500
PRIMER_SECTIONS = ["Overview", "Game Plan", "Key Cards", "Combos and Win Lines", "Mulligans and Opening Hands",
                   "Piloting Tips", "Weaknesses", "Upgrades and Swaps", "Table Talk",
                   "Mulligan and Piloting", "Mulligan & Piloting"]
_TYPE_ORDER = ["Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker", "Battle", "Land"]

# Builder section id <-> normalized heading. Headings are compared case-insensitively with '&' == 'and'.
_SECTION_IDS = {"overview": "overview", "game plan": "plan", "key cards": "cards",
                "mulligan and piloting": "mull", "table talk": "table"}
WIZARD_ORDER = ["overview", "plan", "cards", "mull", "table"]
# Which answers feed which builder section (drives the per-section ◇ Drafted / ◆ Yours provenance).
SECTION_FIELDS = {"overview": ["pitch"], "plan": ["early", "mid", "late"], "cards": [], "mull": ["mull", "mist"],
                  "table": ["weak"]}
# Bold/bracket labels the model uses for structure, never card names.
_LABELS = {"early", "mid", "closing", "late", "keep", "watch for", "before you sit down", "weak to", "turn", "turns",
           "early game", "mid game", "late game", "closing game", "mulligan", "mistakes", "bracket", "overview",
           "note", "tip", "tips", "why", "how it wins", "win condition", "win conditions", "power level"}


class WriteupError(ValueError):
    """Bad inputs or an unusable model reply. `kind` is machine-readable for the UI."""

    def __init__(self, message: str, kind: str = "invalid"):
        super().__init__(message)
        self.kind = kind


def _key(name: str) -> str:
    return " ".join(name.lower().split())


def _norm_heading(h: str) -> str:
    return " ".join(h.lower().replace("&", " and ").replace(":", " ").split())


_VALID_HEADINGS = {_norm_heading(s) for s in PRIMER_SECTIONS}


def section_id(title: str) -> str:
    n = _norm_heading(title)
    return _SECTION_IDS.get(n) or re.sub(r"[^a-z0-9]+", "-", n).strip("-")


def _main_type(type_line: str) -> str:
    for t in _TYPE_ORDER:
        if t.lower() in (type_line or "").lower():
            return t
    return "Other"


def summarize_cards(card_meta: Sequence[Mapping[str, Any]]) -> dict:
    """card_meta rows: {name, type_line?, tags?, mana_value?}. Returns type counts, top tags, a bounded card list
    and the average mana value of non-land cards (None when no card carries one)."""
    types: Counter = Counter()
    tags: Counter = Counter()
    rows: List[str] = []
    mvs: List[float] = []
    for c in card_meta:
        name = c.get("name")
        if not name:
            continue
        t = _main_type(c.get("type_line") or "")
        types[t] += 1
        ctags = [str(x) for x in (c.get("tags") or []) if x]
        tags.update(ctags)
        if t != "Land" and c.get("mana_value") is not None:
            try:
                mvs.append(float(c["mana_value"]))
            except (TypeError, ValueError):
                pass
        if len(rows) < MAX_CARDS_IN_PROMPT and t != "Land":
            rows.append(f"- {name} ({t}{', ' + ', '.join(ctags[:3]) if ctags else ''})")
    return {"types": ", ".join(f"{n} {t}" for t, n in types.most_common()) or "unknown",
            "tags": ", ".join(f"{t} ({n})" for t, n in tags.most_common(10)) or "none",
            "cards": "\n".join(rows) or "(none)", "count": sum(types.values()),
            "avg_mv": round(sum(mvs) / len(mvs), 2) if mvs else None}


def _notes(form: Mapping[str, Any]) -> str:
    lines = []
    for f in PRIMER_FIELDS:
        if f["key"] == "key_cards" or f["key"] not in form:
            continue
        lines.append(f"{f['label']} {form[f['key']]}")
    return "\n".join(lines) or "(none given)"


def format_spirit(spirit: Optional[Mapping[str, Any]]) -> tuple:
    """(status, text) for the DECK SPIRIT block."""
    if not spirit:
        return "draft spirit, may be wrong", "(none recorded)"
    status = "confirmed" if spirit.get("confirmed") else "draft spirit, may be wrong"
    lines: List[str] = []
    if spirit.get("thesis"):
        lines.append(f"THESIS: {spirit['thesis']}")
    if spirit.get("vision"):
        lines.append(f"VISION: {spirit['vision']}")
    if spirit.get("intent"):
        lines.append(f"INTENT: {spirit['intent']}")
    pillars = [p for p in spirit.get("pillars") or [] if p.get("name")]
    if pillars:
        lines.append("PILLARS:")
        lines += [f"- {p['name']}" + (f": {p['description']}" if p.get("description") else "") for p in pillars]
    pets = [p for p in spirit.get("pets") or [] if p]
    lines.append("PETS: " + (", ".join(pets) if pets else "none"))
    if spirit.get("novelties"):
        lines.append(f"NOVELTIES: {spirit['novelties']}")
    if spirit.get("wincons"):
        lines.append("PREFERRED WIN CONDITIONS: " + ", ".join(str(x) for x in spirit["wincons"]))
    g = spirit.get("genre") or {}
    genre = [f"{k}: {', '.join(g[k])}" for k in ("archetype", "theme", "kindred") if g.get(k)]
    if genre:
        lines.append("GENRE: " + "; ".join(genre))
    if spirit.get("target_turn_win"):
        lines.append(f"TARGET WIN TURN: {spirit['target_turn_win']}")
    return status, "\n".join(lines) or "(none recorded)"


def format_table_facts(table_facts: Optional[Mapping[str, Any]], selected: Optional[Sequence[str]] = None) -> str:
    if table_facts is None:
        return "(not computed; make no claims about the deck's effect on the table)"
    facts = [f for f in table_facts.get("facts") or [] if f.get("on")]
    if not facts:
        return "(none)"
    if selected is not None:
        facts = [f for f in facts if f.get("id") in set(selected)]
        if not facts:
            return "(none selected by the owner; do not mention table facts)"
    return "\n".join(f"- {f['title']}" + (f": {f['detail']}" if f.get("detail") else "") for f in facts)


def format_combos(table_facts: Optional[Mapping[str, Any]], extra: Optional[Sequence[str]] = None) -> str:
    lines = list((table_facts or {}).get("combos") or []) + [c for c in extra or [] if c]
    lines = list(dict.fromkeys(lines))
    if lines:
        return "\n".join(f"- {c}" for c in lines)
    if table_facts is not None and not table_facts.get("combos_checked", True):
        return "(Commander Spellbook not checked; list only combos from the owner's notes)"
    return "(none found)"


def _oracle_block(card_meta: Sequence[Mapping[str, Any]], names: Sequence[str]) -> str:
    by_key = {_key(c["name"]): c for c in card_meta if c.get("name")}
    lines, seen = [], set()
    for n in names:
        k = _key(n)
        c = by_key.get(k)
        if k in seen or not c or not c.get("oracle_text"):
            continue
        seen.add(k)
        mv = f" [MV {c['mana_value']:g}]" if c.get("mana_value") is not None else ""
        text = " ".join(str(c["oracle_text"]).split())[:ORACLE_TEXT_CHARS]
        lines.append(f"- {c['name']}{mv}: {text}")
        if len(lines) >= MAX_ORACLE_CARDS:
            break
    return "\n".join(lines) or "(none available)"


def build_user_prompt(deck: Mapping[str, Any], card_meta: Sequence[Mapping[str, Any]], form: Mapping[str, Any],
                      *, identity: Optional[str] = None, bracket: Optional[str] = None,
                      spirit: Optional[Mapping[str, Any]] = None, table_facts: Optional[Mapping[str, Any]] = None,
                      combos: Optional[Sequence[str]] = None, key_whys: Optional[Mapping[str, str]] = None,
                      fact_ids: Optional[Sequence[str]] = None) -> str:
    s = summarize_cards(card_meta)
    key = form.get("key_cards") or []
    whys = key_whys or {}
    status, spirit_text = format_spirit(spirit)
    oracle_names = list(deck.get("commanders") or []) + list(key) + list((spirit or {}).get("pets") or [])
    return prompts.USER_TEMPLATE.format(
        deck_name=deck.get("name") or "Untitled", commanders=", ".join(deck.get("commanders") or []) or "unknown",
        identity=identity or "unknown", bracket=bracket or "unspecified", card_count=s["count"] or "unknown",
        spirit_status=status, spirit=spirit_text,
        notes=_notes(form), table_facts=format_table_facts(table_facts, fact_ids),
        combos=format_combos(table_facts, combos), types=s["types"], tags=s["tags"],
        avg_mv=s["avg_mv"] if s["avg_mv"] is not None else "unknown",
        key_cards="\n".join(f"- {k}" + (f": {whys[k]}" if whys.get(k) else "") for k in key) or "(none given)",
        oracle=_oracle_block(card_meta, oracle_names), cards=s["cards"])


def _known_names(deck: Mapping[str, Any], card_meta: Sequence[Mapping[str, Any]]) -> set:
    names = {_key(c["name"]) for c in card_meta if c.get("name")}
    names |= {_key(n) for n in deck.get("commanders") or []}
    names |= {n.split(" // ")[0] for n in list(names) if " // " in n}
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


def trim_to_chars(text: str, limit: int = DESCRIPTION_MAX_CHARS) -> str:
    """Cut at the last sentence end that fits, else the last word boundary, never past `limit` chars."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s|$)", head)]
    if ends and ends[-1] >= limit * 0.4:
        return head[:ends[-1]].strip()
    cut = head[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-–—")
    return (cut or head[:limit - 1]) + "…"


def validate_description(text: str, max_words: int = DESCRIPTION_MAX_WORDS,
                         max_chars: int = DESCRIPTION_MAX_CHARS) -> str:
    t = _strip_fences(text or "")
    t = re.sub(r"^(description|deck description)\s*:\s*", "", t, flags=re.I).strip()
    if not t:
        raise WriteupError("The model returned an empty description.", "empty")
    if "\n#" in "\n" + t or t.startswith("#"):
        raise WriteupError("The model returned headings instead of a description.", "format")
    t = " ".join(t.split())
    if _words(t) > max_words * 1.5:
        raise WriteupError("The model's description was far too long.", "too_long")
    return trim_to_chars(t, max_chars)


def split_sections(md: str) -> List[Dict[str, str]]:
    """[{title, markdown}] for each '## ' section of a primer (text before the first heading is dropped)."""
    parts = re.split(r"^##\s+(.+?)\s*$", md, flags=re.M)
    return [{"title": parts[i].strip(), "markdown": parts[i + 1].strip()} for i in range(1, len(parts) - 1, 2)]


def validate_primer(text: str, max_words: int = PRIMER_MAX_WORDS, min_chars: int = 0) -> str:
    t = _strip_fences(text or "")
    if not t:
        raise WriteupError("The model returned an empty primer.", "empty")
    heads = [h.strip() for h in re.findall(r"^##\s+(.+?)\s*$", t, re.M)]
    if not heads:
        raise WriteupError("The model's primer had no sections.", "format")
    unknown = [h for h in heads if _norm_heading(h) not in _VALID_HEADINGS]
    if unknown:
        raise WriteupError(f"The model's primer had unexpected sections: {', '.join(unknown)}.", "format")
    if min_chars and len(t) < min_chars:
        raise WriteupError(f"The model's primer was too short ({len(t)} of at least {min_chars} characters).",
                           "too_short")
    if _words(t) > max_words * 1.5:
        raise WriteupError("The model's primer was far too long.", "too_long")
    return t


_MENTION_RES = (re.compile(r"\*\*(.+?)\*\*"), re.compile(r"\[\[(.+?)\]\]"), re.compile(r'data-card="([^"]+)"'))


def unknown_mentions(text: str, known: set) -> List[str]:
    """Bold / [[bracket]] / data-card names in generated text that aren't cards of this deck."""
    skip = _LABELS | _VALID_HEADINGS
    out: List[str] = []
    for rx in _MENTION_RES:
        for raw in rx.findall(text or ""):
            if raw.rstrip().endswith(":"):
                continue  # '**Early:**' style label
            name = raw.strip().rstrip(".,;!?").strip(":").strip()
            k = _key(name)
            if (not name or k in known or k in skip or k.split(" // ")[0] in known or len(name.split()) > 8
                    or re.match(r"^(turns?|t)\s*[\d–-]", k) or re.fullmatch(r"[\d\s.,%+–-]+", k)):
                continue
            if name not in out:
                out.append(name)
    return out


def _result(text: str, form: Mapping[str, Any], known: set) -> dict:
    warnings = [f"Key card not found in this deck: {k}" for k in _check_key_cards(form, known)]
    if known:
        warnings += [f"Card mentioned in writeup not found in this deck: {n}" for n in unknown_mentions(text, known)]
    return {"text": text, "warnings": warnings}


def _voice(voice: Optional[str]) -> str:
    return prompts.VOICE_PRESETS.get((voice or "").lower(), prompts.VOICE_PRESETS[prompts.DEFAULT_VOICE])


def generate_description(deck: Mapping[str, Any], card_meta: Sequence[Mapping[str, Any]], ask: Ask,
                         form: Optional[Mapping[str, Any]] = None, *, identity: Optional[str] = None,
                         bracket: Optional[str] = None, spirit: Optional[Mapping[str, Any]] = None,
                         table_facts: Optional[Mapping[str, Any]] = None, combos: Optional[Sequence[str]] = None,
                         voice: Optional[str] = None, key_whys: Optional[Mapping[str, str]] = None) -> dict:
    """Description (<= 280 chars) from card data; form is optional (theme/tone/key_cards sharpen it). Nothing is saved."""
    if not (deck.get("commanders") or card_meta):
        raise WriteupError("The deck has no cards to describe.", "no_cards")
    try:
        cleaned = clean_form(form or {})
    except FormError as exc:
        raise WriteupError(str(exc)) from exc
    system = prompts.DESCRIPTION_SYSTEM.format(max_words=50, max_chars=DESCRIPTION_MAX_CHARS, voice=_voice(voice))
    user = build_user_prompt(deck, card_meta, cleaned, identity=identity, bracket=bracket, spirit=spirit,
                             table_facts=table_facts, combos=combos, key_whys=key_whys)
    return _result(validate_description(ask(system, user)), cleaned, _known_names(deck, card_meta))


def answers_to_form(answers: Mapping[str, Any]) -> tuple:
    """Wizard answers -> (legacy form dict, {key card: why}, selected fact ids | None, bracket | None).

    Wizard answers: {pitch, tone, bracket, ttw, win:[{id,t,on?,src}], early, mid, late,
    cards:[{name,on,why,src}], mull, mist, weak, facts:[id]}. Everything is optional."""
    a = answers if isinstance(answers, Mapping) else {}

    def txt(k):
        v = a.get(k)
        return v.strip() if isinstance(v, str) else ""

    wins = [str(w["t"]) for w in a.get("win") or [] if isinstance(w, Mapping) and w.get("on", True) and w.get("t")]
    plan = [f"{lab}: {txt(k)}" for k, lab in (("early", "Early (turns 1-3)"), ("mid", "Mid (turns 4-7)"),
                                              ("late", "Closing (turn 8+)")) if txt(k)]
    game_plan = "\n".join(([f"Wins through: {', '.join(wins)}"] if wins else []) + plan)
    cards = [c for c in a.get("cards") or [] if isinstance(c, Mapping) and c.get("name") and c.get("on", True)]
    form: Dict[str, Any] = {"theme": txt("pitch"), "game_plan": game_plan, "key_cards": [c["name"] for c in cards],
                            "mulligan": txt("mull"), "play_tips": txt("mist"), "weaknesses": txt("weak"),
                            "tone": txt("tone")}
    if a.get("ttw"):
        form["tone"] = (form["tone"] + f" Aims to win around turn {a['ttw']}.").strip()
    whys = {c["name"]: str(c.get("why") or "").strip() for c in cards if str(c.get("why") or "").strip()}
    facts = a.get("facts")
    bracket = a.get("bracket")
    return (form, whys, list(facts) if isinstance(facts, list) else None,
            str(bracket) if bracket not in (None, "", 0) else None)


def inferred_fields(answers: Mapping[str, Any], sec_id: str) -> List[str]:
    """Answer fields a builder section drew on that the owner left blank (rendered as ◇ Drafted)."""
    a = answers if isinstance(answers, Mapping) else {}
    if sec_id == "cards":
        return [c["name"] for c in a.get("cards") or []
                if isinstance(c, Mapping) and c.get("on", True) and c.get("name") and not str(c.get("why") or "").strip()]
    return [k for k in SECTION_FIELDS.get(sec_id, []) if not str(a.get(k) or "").strip()]


def _ordered(secs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rank = {s: i for i, s in enumerate(WIZARD_ORDER)}
    return sorted(secs, key=lambda s: rank.get(s["id"], 99))  # stable: legacy order is kept for unknown ids


def _ask_primer(ask: Ask, system: str, user: str, max_words: int) -> str:
    """Validated primer markdown. One retry with an expansion nudge when the first draft is under the floor."""
    try:
        return validate_primer(ask(system, user), max_words, PRIMER_MIN_CHARS)
    except WriteupError as exc:
        if exc.kind != "too_short":
            raise
    nudge = (user + f"\n\nYour previous draft was too short. Rewrite it with at least {PRIMER_MIN_CHARS} "
             "characters: expand with concrete detail from the card data and the owner's notes rather than padding.")
    return validate_primer(ask(system, nudge), max_words, PRIMER_MIN_CHARS)


def generate_primer(deck: Mapping[str, Any], card_meta: Sequence[Mapping[str, Any]], ask: Ask,
                    form: Mapping[str, Any], *, identity: Optional[str] = None,
                    bracket: Optional[str] = None, spirit: Optional[Mapping[str, Any]] = None,
                    table_facts: Optional[Mapping[str, Any]] = None, combos: Optional[Sequence[str]] = None,
                    voice: Optional[str] = None, length: Optional[str] = None,
                    key_whys: Optional[Mapping[str, str]] = None, fact_ids: Optional[Sequence[str]] = None,
                    wizard: bool = False, sections: Optional[Sequence[str]] = None,
                    keep: Optional[Mapping[str, str]] = None, require: Optional[bool] = None) -> dict:
    """Primer from the guided form (legacy: theme + game_plan required) or the wizard (nothing required; blanks
    are drafted from the deck). `keep` = {section id: html} the owner locked or edited: not asked of the model
    and returned untouched. Nothing is saved."""
    try:
        cleaned = clean_form(form)
    except FormError as exc:
        raise WriteupError(str(exc)) from exc
    if require if require is not None else not wizard:
        missing = missing_required(cleaned)
        if missing:
            raise WriteupError(f"Fill in: {', '.join(missing)}.", "missing_fields")
    keep = {k: v for k, v in (keep or {}).items() if isinstance(v, str) and v.strip()}
    known = _known_names(deck, card_meta)
    max_words = LENGTH_WORDS.get((length or "").lower(), PRIMER_MAX_WORDS)
    if wizard:
        ids = [i for i in (sections or WIZARD_ORDER) if i in prompts.WIZARD_SECTIONS]
        ids = [i for i in WIZARD_ORDER if i in ids]
        want = [prompts.WIZARD_SECTIONS[i] for i in ids if i not in keep]
    else:
        ids, want = [], list(prompts.LEGACY_SECTIONS)
    out_secs: List[Dict[str, Any]] = []
    text, warnings = "", [f"Key card not found in this deck: {k}" for k in _check_key_cards(cleaned, known)]
    if want:
        system = prompts.PRIMER_SYSTEM.format(max_words=max_words, min_chars=PRIMER_MIN_CHARS, voice=_voice(voice),
                                              sections="\n".join(f"## {s}" for s in want))
        user = build_user_prompt(deck, card_meta, cleaned, identity=identity, bracket=bracket, spirit=spirit,
                                 table_facts=table_facts, combos=combos, key_whys=key_whys, fact_ids=fact_ids)
        res = _result(_ask_primer(ask, system, user, max_words), cleaned, known)
        text, warnings = res["text"], res["warnings"]
        for s in split_sections(text):
            sid = section_id(s["title"])
            if sid in keep or (wizard and sid not in ids):
                continue  # the model re-wrote a locked / switched-off section: ignore it
            out_secs.append({"id": sid, "title": s["title"], "markdown": s["markdown"]})
    for sid, html_text in keep.items():
        if not wizard or sid in ids or sid not in prompts.WIZARD_SECTIONS:
            out_secs.append({"id": sid, "title": prompts.WIZARD_SECTIONS.get(sid, sid), "markdown": html_to_text(html_text),
                             "html": html_text, "kept": True})
    out_secs = _ordered(out_secs)
    md = "\n\n".join(f"## {s['title']}\n{s['markdown']}" for s in out_secs) if wizard else text
    return {"text": md, "warnings": warnings, "sections": out_secs}


def card_meta_for_deck(r, deck: Mapping[str, Any]) -> List[dict]:
    """Deck cards + tags (tag_store) + mana value / oracle text (deck dict, else the interaction cache),
    as {name, type_line, tags, mana_value, oracle_text, is_commander} rows."""
    import tags as tag_store
    import registry

    try:
        tag_map = tag_store.get_deck_card_tags(r, registry.registry_id_of(deck))
    except Exception:
        tag_map = {}
    commanders = {_key(c) for c in deck.get("commanders") or []}
    rows = []
    for c in deck.get("cards") or []:
        if not isinstance(c, Mapping) or not c.get("name"):
            continue
        types = c.get("types") or c.get("type_line") or ""
        if isinstance(types, (list, tuple)):
            types = " ".join(str(t) for t in types)
        tags = [t.get("tag") if isinstance(t, Mapping) else t for t in tag_map.get(c["name"], [])]
        mv = c.get("mana_value", c.get("cmc"))
        rows.append({"name": c["name"], "type_line": types, "tags": [t for t in tags if t],
                     "mana_value": float(mv) if isinstance(mv, (int, float)) else mana_value_of(c.get("mana_cost")),
                     "oracle_text": c.get("oracle_text") or "", "is_commander": _key(c["name"]) in commanders})
    have = {_key(row["name"]) for row in rows}
    for name in deck.get("commanders") or []:  # commanders often live outside deck["cards"]
        if _key(name) not in have:
            rows.append({"name": name, "type_line": "", "tags": [], "mana_value": None, "oracle_text": "",
                         "is_commander": True})
    try:
        enrich_card_meta(r, rows)
    except Exception:
        pass
    return rows
