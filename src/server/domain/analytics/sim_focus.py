"""Simulate "Focus" chips: an editable, persisted catalog + deterministic suggestions.

Catalog (Redis):
    sim:focus:catalog   JSON list of {slug, label, hint, order, telemetry_key, rules}
    sim:focus:seeded    marker, set the first time the catalog is seeded

The eight seed entries below are written ONCE (guarded by the marker, not by "catalog is empty"),
so a deleted chip is never restored. Runtime code only ever reads the stored catalog; entries carry
no built-in flag and are fully editable / deletable / reorderable.

Telemetry: a chip's ``telemetry_key`` names the sim metric it needs. No key -> selectable and
emphasis-only. A key that is not in SUPPORTED_TELEMETRY (what headless Forge actually logs, see
docs/design/simulate-forge-telemetry.md) -> the chip is unavailable: hidden in the UI, never
suggested, dropped by server validation. Seeds keep their intended key, so they appear
automatically once the engine emits it.

Suggestions are pure and deterministic (no LLM): a chip's ``rules`` are evaluated against the same
numbers the deck page shows (analytics.categories.core_rows counts, floors, template targets).
A chip with no rules is never suggested. Nothing triggering -> no suggestion (balanced run).
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

CATALOG_KEY = "sim:focus:catalog"
SEEDED_KEY = "sim:focus:seeded"
CHOICE_PREFIX = "deck:sim:focus:"

MAX_FOCUS = 3
MAX_CHIPS = 40

# What headless Forge logs today (docs/design/simulate-forge-telemetry.md): land drops, opening
# hands / mulligans, commander cast turn, spells cast, game outcome + win turn. Not logged: cards
# drawn, hand contents, mana pool, removal/interaction events.
SUPPORTED_TELEMETRY = frozenset({"land_drops", "mulligans", "commander_cast_turn", "spells_cast", "win_turn"})

# Suggestion-rule constants that the deck page keeps inline (analytics/categories.core_rows).
HIGH_COMMANDER_MV = 5            # core_rows raises the ramp floor when commander MV >= 5
COMMANDER_DEPENDENT_MIN_ROLES = 2  # core roles the commander itself provides (core_rows lowers floors for these)
LANDS_SHORT_BY = 2               # composition.delta_status: "short" means >= 2 under the template target
ROW_NOUNS = {"ramp": "ramp pieces", "card_advantage": "card-draw pieces", "removal": "removal and counterspells",
             "finisher": "win conditions", "lands": "lands"}
# Every metric key the catalog may reference: what Forge logs today plus the keys the seeds already name
# (those become available once the engine emits them). The Manage editor's picker is limited to these.
KNOWN_TELEMETRY = tuple(sorted(SUPPORTED_TELEMETRY | {"mana_pool", "cards_drawn", "interaction_events"}))
RULE_KINDS = ("below_target", "below_floor", "mv_at_least", "commander_roles")
RULE_ROWS = tuple(ROW_NOUNS)

SEED: Tuple[dict, ...] = (
    {"slug": "manabase", "label": "Manabase", "hint": "Color screw, missed land drops, tapped lands",
     "telemetry_key": "land_drops", "rules": [{"kind": "below_target", "row": "lands"}]},
    {"slug": "ramp", "label": "Ramp", "hint": "Mana by T3/T4, dead ramp",
     "telemetry_key": "mana_pool", "rules": [{"kind": "below_floor", "row": "ramp"}, {"kind": "mv_at_least"}]},
    {"slug": "tempo", "label": "Tempo", "hint": "Curve vs math, turns with nothing cast",
     "telemetry_key": "spells_cast", "rules": []},
    {"slug": "consistency", "label": "Consistency", "hint": "Mulligans, does the plan assemble",
     "telemetry_key": "mulligans", "rules": []},
    {"slug": "card-draw", "label": "Card draw", "hint": "Running out of gas",
     "telemetry_key": "cards_drawn", "rules": [{"kind": "below_floor", "row": "card_advantage"}]},
    {"slug": "commander", "label": "Commander", "hint": "Cast turn, removal, recast tax, the deck without it",
     "telemetry_key": "commander_cast_turn", "rules": [{"kind": "commander_roles"}]},
    {"slug": "interaction", "label": "Interaction", "hint": "Answers vs threats",
     "telemetry_key": "interaction_events", "rules": [{"kind": "below_floor", "row": "removal"}]},
    {"slug": "closing", "label": "Closing", "hint": "Win turn, stalled boards",
     "telemetry_key": "win_turn", "rules": [{"kind": "below_floor", "row": "finisher"}]},
)


class FocusError(ValueError):
    pass


# -- store -----------------------------------------------------------------------------------

def _loads(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def ensure_seeded(r) -> None:
    """Seed the catalog the first time only. Safe to call on every read."""
    if r.get(SEEDED_KEY) is not None:
        return
    seed = [dict(c, order=i) for i, c in enumerate(SEED)]
    r.set(CATALOG_KEY, json.dumps(seed), nx=True)
    r.set(SEEDED_KEY, "1")


def _save(r, items: List[dict]) -> None:
    r.set(CATALOG_KEY, json.dumps(items))


def list_catalog(r) -> List[dict]:
    """The stored catalog in order (including chips whose telemetry is unavailable)."""
    ensure_seeded(r)
    data = _loads(r.get(CATALOG_KEY))
    items = [c for c in data if isinstance(c, dict) and c.get("slug")] if isinstance(data, list) else []
    return sorted(items, key=lambda c: (c.get("order", 0), c["slug"]))


def is_available(chip: Mapping, supported: Optional[Iterable[str]] = None) -> bool:
    key = chip.get("telemetry_key")
    return not key or key in (SUPPORTED_TELEMETRY if supported is None else set(supported))


def available_catalog(r, supported: Optional[Iterable[str]] = None) -> List[dict]:
    return [c for c in list_catalog(r) if is_available(c, supported)]


_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def _clean_rules(rules: Any) -> List[dict]:
    if rules in (None, ""):
        return []
    if not isinstance(rules, list):
        raise FocusError("rules must be a list")
    out = []
    for rule in rules:
        if not isinstance(rule, Mapping) or rule.get("kind") not in RULE_KINDS:
            raise FocusError(f"rule kind must be one of {', '.join(RULE_KINDS)}")
        row = rule.get("row")
        if rule["kind"] in ("below_target", "below_floor") and row not in RULE_ROWS:
            raise FocusError(f"rule row must be one of {', '.join(RULE_ROWS)}")
        out.append({"kind": rule["kind"], **({"row": row} if row else {})})
    return out


def _clean(body: Mapping, base: Optional[Mapping] = None) -> dict:
    cur = dict(base or {})
    cur.update({k: body[k] for k in ("slug", "label", "hint", "telemetry_key", "rules") if k in body})
    slug, label, hint = cur.get("slug"), cur.get("label"), cur.get("hint")
    if not isinstance(slug, str) or not _SLUG.match(slug):
        raise FocusError("slug must be 1-32 chars: lowercase letters, digits, hyphens")
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 40:
        raise FocusError("label must be 1-40 characters")
    if not isinstance(hint, str) or not 1 <= len(hint.strip()) <= 140:
        raise FocusError("hint must be 1-140 characters")
    key = cur.get("telemetry_key") or None
    if key is not None and key not in KNOWN_TELEMETRY:
        raise FocusError(f"telemetry_key must be one of {', '.join(KNOWN_TELEMETRY)}")
    return {"slug": slug, "label": label.strip(), "hint": hint.strip(), "order": cur.get("order", 0),
            "telemetry_key": key, "rules": _clean_rules(cur.get("rules"))}


def slugify(label: Any, taken: Iterable[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", str(label or "").lower()).strip("-")[:28].strip("-")
    if not base:
        raise FocusError("label must contain letters or digits")
    taken = set(taken)
    slug, n = base, 2
    while slug in taken:
        slug = f"{base}-{n}"
        n += 1
    return slug


def options() -> dict:
    """Finite choices for the Manage editor's pickers (no free-typed numbers)."""
    return {"telemetry_keys": list(KNOWN_TELEMETRY), "supported_telemetry": sorted(SUPPORTED_TELEMETRY),
            "rule_kinds": list(RULE_KINDS), "rule_rows": list(RULE_ROWS), "row_nouns": dict(ROW_NOUNS),
            "limits": {"label": 40, "hint": 140, "max_chips": MAX_CHIPS}}


def create(r, body: Mapping) -> dict:
    items = list_catalog(r)
    body = dict(body)
    if not body.get("slug"):  # generated once, here; never recomputed on rename
        if not isinstance(body.get("label"), str) or not body["label"].strip():
            raise FocusError("label must be 1-40 characters")
        body["slug"] = slugify(body["label"], (c["slug"] for c in items))
    chip = _clean(body)
    if any(c["slug"] == chip["slug"] for c in items):
        raise FocusError(f"slug '{chip['slug']}' already exists")
    if len(items) >= MAX_CHIPS:
        raise FocusError(f"at most {MAX_CHIPS} focus chips")
    chip["order"] = max([c.get("order", 0) for c in items] + [-1]) + 1
    items.append(chip)
    _save(r, items)
    return chip


def update(r, slug: str, body: Mapping) -> dict:
    """Edit label / hint / telemetry_key / rules. The slug is immutable (old runs refer to it)."""
    items = list_catalog(r)
    for i, c in enumerate(items):
        if c["slug"] == slug:
            new = _clean({k: v for k, v in body.items() if k != "slug"}, c)
            items[i] = new
            _save(r, items)
            return new
    raise FocusError(f"unknown focus chip '{slug}'")


def delete(r, slug: str) -> None:
    items = list_catalog(r)
    left = [c for c in items if c["slug"] != slug]
    if len(left) == len(items):
        raise FocusError(f"unknown focus chip '{slug}'")
    _save(r, left)


def reorder(r, slugs: List[str]) -> List[dict]:
    items = {c["slug"]: c for c in list_catalog(r)}
    if sorted(slugs) != sorted(items):
        raise FocusError("reorder needs every existing slug exactly once")
    out = []
    for i, s in enumerate(slugs):
        out.append(dict(items[s], order=i))
    _save(r, out)
    return out


# -- validation / snapshot --------------------------------------------------------------------

def normalize_focus(raw: Any, catalog: Optional[Iterable[Mapping]]) -> Tuple[List[str], List[dict]]:
    """Client slugs -> (valid slugs, [{slug, label}] snapshot). Unknown/unavailable dropped, deduped, capped."""
    if raw is None:
        return [], []
    if not isinstance(raw, list) or not all(isinstance(s, str) for s in raw):
        raise FocusError("focus must be a list of chip slugs")
    by_slug = {c["slug"]: c for c in catalog or []}
    slugs: List[str] = []
    for s in raw:
        if s in by_slug and s not in slugs:
            slugs.append(s)
    slugs = slugs[:MAX_FOCUS]
    return slugs, [{"slug": s, "label": by_slug[s]["label"]} for s in slugs]


# -- saved choice (per deck + decklist fingerprint) ---------------------------------------------

def save_choice(r, rid: str, fingerprint: str, focus: List[str]) -> None:
    """Remember the user's final selection (an empty list is a real choice: balanced run)."""
    r.set(CHOICE_PREFIX + rid, json.dumps({"fingerprint": fingerprint, "focus": list(focus)}))


def saved_choice(r, rid: str, fingerprint: str) -> Optional[List[str]]:
    data = _loads(r.get(CHOICE_PREFIX + rid))
    if not isinstance(data, dict) or data.get("fingerprint") != fingerprint or not isinstance(data.get("focus"), list):
        return None
    return [s for s in data["focus"] if isinstance(s, str)]


# -- suggestions ------------------------------------------------------------------------------

def build_stats(deck: Mapping, rows: List[dict], meta: Mapping, removal_combined: Optional[int] = None) -> dict:
    """Normalise categories.core_rows output into the numbers the rules read."""
    by_id = {r_["id"]: r_ for r_ in rows}
    counts = {k: v.get("count") for k, v in by_id.items()}
    if removal_combined is not None:
        counts["removal"] = removal_combined
    return {"built": bool(meta.get("built")), "mv": meta.get("commander_mv") or 0, "counts": counts,
            "floors": {k: v.get("floor") for k, v in by_id.items()},
            "targets": {k: v.get("target") for k, v in by_id.items()},
            "commander_roles": sum(1 for v in rows if v.get("commander_does_this"))}


def evaluate_rule(rule: Mapping, stats: Mapping) -> Optional[Tuple[float, str]]:
    """(distance from threshold, reason quoting the computed value), or None when not triggered."""
    kind, row = rule.get("kind"), rule.get("row") or ""
    if not stats.get("built"):
        return None
    if kind == "below_target":
        count, target = stats["counts"].get(row), stats["targets"].get(row)
        if count is None or not target or count > target - LANDS_SHORT_BY:
            return None
        return (target - count) / target, f"{count} {ROW_NOUNS.get(row, row)} (template {target})"
    if kind == "below_floor":
        count, floor = stats["counts"].get(row), stats["floors"].get(row)
        if count is None or not floor or count >= floor:
            return None
        return (floor - count) / floor, f"{count} {ROW_NOUNS.get(row, row)} (floor {floor})"
    if kind == "mv_at_least":
        mv = stats.get("mv") or 0
        if mv < HIGH_COMMANDER_MV:
            return None
        return (mv - HIGH_COMMANDER_MV + 1) / mv, f"commander mana value {mv}"
    if kind == "commander_roles":
        n = stats.get("commander_roles") or 0
        if n < COMMANDER_DEPENDENT_MIN_ROLES:
            return None
        return (n - COMMANDER_DEPENDENT_MIN_ROLES + 1) / n, f"commander covers {n} core roles itself"
    return None


def suggest(catalog: Iterable[Mapping], stats: Mapping, supported: Optional[Iterable[str]] = None) -> List[dict]:
    """Up to MAX_FOCUS [{slug, reason, distance}], most-off-threshold first. [] when nothing triggers."""
    hits = []
    for chip in catalog:
        if not is_available(chip, supported):
            continue
        best = None
        for rule in chip.get("rules") or []:
            res = evaluate_rule(rule, stats)
            if res and (best is None or res[0] > best[0]):
                best = res
        if best:
            hits.append((-best[0], chip.get("order", 0), chip["slug"], best[1], best[0]))
    hits.sort(key=lambda h: h[:3])
    return [{"slug": s, "reason": reason, "distance": round(d, 4)} for _n, _o, s, reason, d in hits[:MAX_FOCUS]]
