"""Hard rules for Brew: structured, code-checkable, deterministic (no LLM here).

A rule is a small dict::

    {"kind": "exclude", "field": "creature_type", "values": ["Dragon", "Angel"], "label": "No Dragons or Angels"}

``kind`` (only one today, others are rejected with a clear error):
  * ``"exclude"``: a card that matches ANY value in ``values`` on ``field`` is removed.

``field`` (what a value is compared against):
  * ``creature_type``: a creature subtype (the words after the em dash in the type line of a
    Creature / Kindred / Tribal face), matched whole-word, case-insensitive. "Dragons" matches Dragon.
  * ``card_type``: a card type or supertype in the type line (Creature, Land, Legendary, ...).
  * ``name``: the exact card name (any face), case-insensitive.
  * ``oracle_text``: case-insensitive substring of the rules text (any face).
  * ``color``: a card colour (W, U, B, R, G or white/blue/black/red/green, or "colorless").
    Colour (not colour identity) of any face.

``values``: non-empty list of non-empty strings. ``label``: short human text shown in the UI and in the
``rule_removed`` records (generated from the rule when blank).

Double-faced / split cards: a card violates a rule when ANY face matches ("A // B" type lines,
``card_faces``). Basic lands are never judged (they cannot be removed to "make room").
Commanders are never removed by a rule; ``commander_violations`` reports a commander that breaks one
(approval then fails).

Enforcement points (see brew.py / brew_builder.py / deck_fill.py): every candidate pool, the fill
sources inside ``deck_fill.fill_deck`` (via ``rules_scope`` / ``active_filter``) and the final draft.
"""
from __future__ import annotations

import contextlib
import contextvars
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

KINDS = ("exclude",)
FIELDS = ("creature_type", "card_type", "name", "oracle_text", "color")
MAX_RULES = 50
# A rule-caused shortfall may never be covered by basics beyond a normal deck's land count:
# 99 non-commander cards - NORMAL_LAND_COUNT lands = the non-basic cards a rules-affected build needs.
NORMAL_LAND_COUNT = 37
MIN_NON_BASIC = 99 - NORMAL_LAND_COUNT
MAX_VALUES = 50
MAX_TEXT = 120

_COLOR_WORDS = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G", "colorless": "C"}
_BASICS = frozenset({"plains", "island", "swamp", "mountain", "forest", "wastes",
                     "snow-covered plains", "snow-covered island", "snow-covered swamp",
                     "snow-covered mountain", "snow-covered forest"})
_CREATURE_FACE_TYPES = {"creature", "kindred", "tribal"}


class RuleError(ValueError):
    """A rule failed validation (message names the problem)."""


class RulesStarvedError(ValueError):
    """Enforcement left too few cards to build; the message names the responsible rule(s)."""


# -- validation --------------------------------------------------------------------------

def _clean_values(field: str, values: Any) -> List[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)) or not values:
        raise RuleError("values must be a non-empty list of strings")
    if len(values) > MAX_VALUES:
        raise RuleError(f"too many values (max {MAX_VALUES})")
    out: List[str] = []
    for v in values:
        if not isinstance(v, str) or not v.strip():
            raise RuleError("every value must be a non-empty string")
        v = " ".join(v.split())
        if len(v) > MAX_TEXT:
            raise RuleError(f"value too long (max {MAX_TEXT} chars)")
        if field == "color":
            c = _COLOR_WORDS.get(v.casefold(), v.upper())
            if c not in {"W", "U", "B", "R", "G", "C"}:
                raise RuleError(f"unknown colour {v!r} (use W/U/B/R/G, a colour word or colorless)")
            v = c
        if v.casefold() not in {x.casefold() for x in out}:
            out.append(v)
    return out


def default_label(rule: dict) -> str:
    vals = ", ".join(rule["values"])
    return {
        "creature_type": f"No {vals} creatures",
        "card_type": f"No {vals} cards",
        "name": f"Never include {vals}",
        "oracle_text": f"No cards with text: {vals}",
        "color": f"No {vals} cards",
    }[rule["field"]]


def validate_rule(raw: Any) -> dict:
    """Return the normalised rule or raise RuleError."""
    if not isinstance(raw, dict):
        raise RuleError("a rule must be an object")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise RuleError(f"unknown rule kind {kind!r} (supported: {', '.join(KINDS)})")
    field = raw.get("field")
    if field not in FIELDS:
        raise RuleError(f"unknown rule field {field!r} (supported: {', '.join(FIELDS)})")
    rule = {"kind": kind, "field": field, "values": _clean_values(field, raw.get("values"))}
    label = raw.get("label")
    label = " ".join(label.split()) if isinstance(label, str) else ""
    rule["label"] = label[:MAX_TEXT] if label else default_label(rule)
    return rule


def validate_rules(raw: Any) -> Tuple[List[dict], List[dict]]:
    """Lenient: ``(valid_rules, errors)``; errors are ``{"index", "rule", "error"}`` (never silently enforced)."""
    if raw is None:
        raw = []
    if not isinstance(raw, (list, tuple)):
        return [], [{"index": None, "rule": raw, "error": "rules must be a list"}]
    rules: List[dict] = []
    errors: List[dict] = []
    for i, item in enumerate(raw):
        if len(rules) >= MAX_RULES:
            errors.append({"index": i, "rule": item, "error": f"too many rules (max {MAX_RULES})"})
            continue
        try:
            rules.append(validate_rule(item))
        except RuleError as exc:
            errors.append({"index": i, "rule": item, "error": str(exc)})
    return rules, errors


def validate_rules_strict(raw: Any) -> List[dict]:
    """Strict: raises RuleError naming every bad rule."""
    rules, errors = validate_rules(raw)
    if errors:
        raise RuleError("; ".join(
            f"rule {e['index'] + 1 if isinstance(e['index'], int) else ''}: {e['error']}".replace("rule : ", "")
            for e in errors))
    return rules


# -- card facts --------------------------------------------------------------------------

def _split_face(type_line: str) -> Tuple[List[str], List[str]]:
    left, _, right = (type_line or "").partition("—")
    if not right and "-" in left and " - " in left:
        left, _, right = left.partition(" - ")
    return left.split(), right.split()


def _color_set(v: Any) -> set:
    """Colours from a list (["W","U"]), a string ("WU" / "W,U" / "white") or None."""
    if not v:
        return set()
    items = [v] if isinstance(v, str) else list(v)
    out = set()
    for it in items:
        it = str(it).strip()
        if it.casefold() in _COLOR_WORDS:
            out.add(_COLOR_WORDS[it.casefold()])
        else:
            out |= {ch for ch in it.upper() if ch in "WUBRGC"}
    return out


def facts_from_record(name: str, rec: Optional[dict]) -> dict:
    """Normalise a card record into the facts rules are checked against:
    ``{"names": [...], "faces": [{"types": set, "subtypes": set, "text": str}], "colors": set}``."""
    rec = rec or {}
    names = {name or ""}
    faces_raw = [f for f in (rec.get("card_faces") or []) if isinstance(f, dict)]
    type_line = rec.get("type_line") or rec.get("type") or ""
    if isinstance(type_line, (list, tuple)):
        type_line = " ".join(str(x) for x in type_line)
    if not type_line and rec.get("types"):
        type_line = " ".join(str(x) for x in rec["types"])
    # Canonical records (inventory.cards) carry the type line split into ``super_types`` / ``types`` /
    # ``sub_types`` and ``type`` is only the first card type, so rebuild "Legendary Creature — Angel".
    if rec.get("sub_types") and "—" not in str(type_line):
        left = " ".join(str(x) for x in [*(rec.get("super_types") or []), *(rec.get("types") or [rec.get("type")])] if x)
        type_line = f"{left} — {' '.join(str(x) for x in rec['sub_types'])}"
    face_lines = [str(f.get("type_line") or "") for f in faces_raw] or [t for t in str(type_line).split("//")]
    texts = [str(f.get("oracle_text") or "") for f in faces_raw]
    if not any(texts):
        texts = str(rec.get("oracle_text") or "").split("\n//\n") if rec.get("oracle_text") else [""]
    for f in faces_raw:
        if f.get("name"):
            names.add(str(f["name"]))
    if "//" in (name or ""):
        names.update(p.strip() for p in name.split("//"))
    faces = []
    for i, line in enumerate(face_lines):
        types, subs = _split_face(line)
        faces.append({"types": {t.casefold() for t in types}, "subtypes": {s.casefold() for s in subs},
                      "text": (texts[i] if i < len(texts) else texts[0] if len(texts) == 1 else "").casefold()})
    if not faces:
        faces = [{"types": set(), "subtypes": set(), "text": str(rec.get("oracle_text") or "").casefold()}]
    # a single combined oracle_text on a multi-face card applies to every face's text check
    whole_text = str(rec.get("oracle_text") or "").casefold()
    colors = _color_set(rec.get("colors")) | _color_set(rec.get("color"))
    for f in faces_raw:
        colors |= _color_set(f.get("colors")) | _color_set(f.get("color"))
    known = any(k in rec for k in ("colors", "color")) or any("colors" in f or "color" in f for f in faces_raw)
    return {"names": {n.casefold() for n in names if n}, "faces": faces, "colors": colors,
            "text": whole_text, "known_colors": known}


def card_facts(r: Any, name: str) -> dict:
    """Facts for a card name from the canonical record, then legacy card_meta."""
    rec: dict = {}
    if r is not None and name:
        try:
            from src.server.domain.inventory import cards as card_store

            rec = card_store.get_card_by_name(r, name) or {}
        except Exception:
            rec = {}
        if not rec:
            try:
                import json

                raw = r.get(f"card_meta:{name}")
                rec = json.loads(raw) if raw else {}
            except Exception:
                rec = {}
    return facts_from_record(name, rec if isinstance(rec, dict) else {})


# -- matching ----------------------------------------------------------------------------

def _subtype_match(value: str, subtype: str) -> bool:
    v = value.casefold()
    return v == subtype or v == subtype + "s" or v == subtype + "es" or (subtype.endswith("f") and v == subtype[:-1] + "ves")


def violates(rule: dict, facts: dict) -> bool:
    """True when the card (its facts) breaks ``rule``. Any face counts."""
    field, values = rule["field"], rule["values"]
    low = [v.casefold() for v in values]
    if field == "name":
        return any(v in facts["names"] for v in low)
    if field == "oracle_text":
        texts = [f["text"] for f in facts["faces"]] + [facts.get("text", "")]
        return any(v in t for v in low for t in texts if t)
    if field == "card_type":
        return any(v in f["types"] for v in low for f in facts["faces"])
    if field == "creature_type":
        for f in facts["faces"]:
            if f["types"] & _CREATURE_FACE_TYPES:
                if any(_subtype_match(v, s) for v in values for s in f["subtypes"]):
                    return True
        return False
    if field == "color":
        want = {v.upper() for v in values}
        has = facts["colors"] or ({"C"} if facts.get("known_colors") else set())
        return bool(want & has)
    return False


def _is_basic(name: str) -> bool:
    return (name or "").casefold() in _BASICS


def first_violation(rules: Sequence[dict], name: str, facts: dict) -> Optional[dict]:
    if _is_basic(name):
        return None
    for rule in rules:
        if violates(rule, facts):
            return rule
    return None


# -- enforcement -------------------------------------------------------------------------

def _norm(n: str) -> str:
    return " ".join((n or "").casefold().split())


def make_filter(rules: Sequence[dict], r: Any, protected: Iterable[str] = ()) -> Callable[[str], Optional[dict]]:
    """name -> the violated rule (or None). Protected names (commanders) always pass. Cached per filter."""
    rules = list(rules)
    keep = {_norm(p) for p in protected}
    cache: Dict[str, Optional[dict]] = {}

    def check(name: str) -> Optional[dict]:
        if not rules or not isinstance(name, str) or _norm(name) in keep:
            return None
        k = _norm(name)
        if k not in cache:
            cache[k] = first_violation(rules, name, card_facts(r, name))
        return cache[k]

    return check


def filter_names(rules: Sequence[dict], names: Iterable[str], r: Any,
                 protected: Iterable[str] = ()) -> Tuple[List[str], List[dict]]:
    """``(kept names, removed [{"card", "rule"}])``; order kept."""
    check = make_filter(rules, r, protected)
    kept: List[str] = []
    removed: List[dict] = []
    for n in names:
        rule = check(n)
        if rule is None:
            kept.append(n)
        else:
            removed.append({"card": n, "rule": rule["label"]})
    return kept, removed


def filter_pools(rules: Sequence[dict], pools: Any, r: Any, protected: Iterable[str] = ()) -> List[dict]:
    """Remove violating cards from every tier of ``pools`` in place; returns the removed records
    (also stored as ``pools.rule_removed``)."""
    check = make_filter(rules, r, protected)
    removed: List[dict] = []
    for attr in ("tier1", "tier2", "tier3"):
        kept = []
        for pc in getattr(pools, attr):
            rule = check(pc.name)
            if rule is None:
                kept.append(pc)
            else:
                removed.append({"card": pc.name, "rule": rule["label"]})
        setattr(pools, attr, kept)
    pools.rule_removed = removed
    return removed


def enforce_draft(rules: Sequence[dict], draft: dict, r: Any, protected: Iterable[str] = ()) -> List[dict]:
    """Drop violating card lines from ``draft['cards']`` in place; records them (merged, deduped) on
    ``draft['rule_removed']`` as ``{"card", "rule"}``. Returns the newly removed records."""
    check = make_filter(rules, r, protected)
    kept, removed = [], []
    for c in draft.get("cards") or []:
        rule = check(c.get("name"))
        if rule is None:
            kept.append(c)
        else:
            removed.append({"card": c.get("name"), "rule": rule["label"]})
    draft["cards"] = kept
    record_removed(draft, removed)
    return removed


def record_removed(draft: dict, removed: Iterable[dict]) -> None:
    have = {(x.get("card"), x.get("rule")) for x in draft.get("rule_removed") or []}
    out = list(draft.get("rule_removed") or [])
    for x in removed:
        if (x["card"], x["rule"]) not in have:
            have.add((x["card"], x["rule"]))
            out.append({"card": x["card"], "rule": x["rule"]})
    draft["rule_removed"] = out


def draft_violations(rules: Sequence[dict], draft: dict, r: Any, protected: Iterable[str] = ()) -> List[dict]:
    """Cards still in the draft that break a rule: ``[{"card", "rule"}]`` (empty = clean)."""
    check = make_filter(rules, r, protected)
    out = []
    for c in draft.get("cards") or []:
        rule = check(c.get("name"))
        if rule is not None:
            out.append({"card": c.get("name"), "rule": rule["label"]})
    return out


def commander_violations(rules: Sequence[dict], commanders: Iterable[str], r: Any) -> List[dict]:
    """Commanders that themselves break a rule: ``[{"card", "rule"}]``."""
    check = make_filter(rules, r)
    return [{"card": c, "rule": rule["label"]} for c in commanders if (rule := check(c)) is not None]


def starved_message(removed: Sequence[dict], have: int, need: int) -> str:
    counts: Dict[str, int] = {}
    for x in removed:
        counts[x["rule"]] = counts.get(x["rule"], 0) + 1
    why = "; ".join(f"“{label}” removed {n}" for label, n in sorted(counts.items(), key=lambda kv: -kv[1])) or "no card was removed"
    return (f"Hard rules left too few cards to build a deck ({have} usable non-basic cards, need at least {need}): "
            f"{why}. Loosen or remove those rules and try again; the brew will not pad the deck with basic lands.")


# -- scope for deck_fill (gate repair) -----------------------------------------------------

_active: contextvars.ContextVar = contextvars.ContextVar("brew_rules_filter", default=None)


@contextlib.contextmanager
def rules_scope(rules: Sequence[dict], r: Any, protected: Iterable[str] = ()):
    """Make ``active_filter()`` return this rule filter for the duration (used by deck_fill)."""
    token = _active.set(make_filter(rules, r, protected) if rules else None)
    try:
        yield
    finally:
        _active.reset(token)


def active_filter() -> Optional[Callable[[str], Optional[dict]]]:
    return _active.get()


__all__ = [
    "KINDS", "FIELDS", "RuleError", "RulesStarvedError", "validate_rule", "validate_rules",
    "validate_rules_strict", "card_facts", "facts_from_record", "violates", "make_filter", "filter_names",
    "filter_pools", "enforce_draft", "draft_violations", "commander_violations", "rules_scope",
    "active_filter", "starved_message", "record_removed",
]
