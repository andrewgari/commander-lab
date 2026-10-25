"""Archetype registry (vocab PR3): the global, user-editable list of deck archetypes.

An archetype is

    {id, name, description, required_roles: [{tag, min_count, weight}],
     source: "user", draft: bool, updated_at}

A role ``tag`` is a role-tag registry id (analytics/role_tags.py): every tag is one
Scryfall query, and scoring intersects the query's cached name set with the deck's
cards. Legacy ``produces:<resource>``/``consumes:<resource>`` spellings (the old
interaction-graph vocabulary) resolve at read/score time to ``legacy:<side>:<key>``
tags with identical membership, so saved archetypes score unchanged (spec §3.2).

Redis: ``archetypes`` (JSON list, absent until first read, which seeds it) and
``archetypes:version`` (integer, bumped on every create/update/delete). A deck's
chosen/derived archetype is a separate small record at ``archetype_sel:{registry_id}``.

``archetype_match`` is a pure function: same deck + same archetypes -> identical
output. It scores every archetype by how many of its required roles the WHOLE
deck satisfies (not by the single most common card tag).

This module is deliberately independent from analytics/genres.py (the Deep
Review "Deck Tags" registry, a different concept per PR1/PR3 scoping) even
though the shape rhymes -- they serve different features and must not be
conflated.
"""
from __future__ import annotations

import functools
import json
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import markdown as _markdown

from analytics import role_tags
from analytics.deck_writeup.sanitize import sanitize_primer_html
from analytics.interactions.vocab import RESOURCES, RESOURCE_BY_KEY

ARCHETYPES_KEY = "archetypes"
VERSION_KEY = "archetypes:version"
SEL_KEY = "archetype_sel:{registry_id}"
SOURCES = ("user",)
MAX_NAME = 60
MAX_DESCRIPTION = 4000
MAX_ROLES = 12
MAX_MIN_COUNT = 40
MAX_WEIGHT = 10.0
MODES = ("auto", "manual")


class ArchetypeError(ValueError):
    """Invalid archetype payload (HTTP 400)."""


class ArchetypeNotFound(KeyError):
    """No archetype with that id (HTTP 404)."""


# -- vocabulary -------------------------------------------------------------------------------

def role_vocabulary(r=None) -> List[dict]:
    """Every valid role tag: the user-editable registry (id, label, query, facet).

    With no Redis handle, returns the migrated legacy tags (same shape).
    """
    tags = role_tags.all_tags(r) if r is not None else role_tags.legacy_tags()
    return [{"tag": t["id"], "label": t["label"], "query": t.get("query", ""),
             "facet": t.get("facet"), "target_id": t.get("target_id"),
             "meaning": t.get("explanation") or t.get("query", ""),
             "source": t.get("source")} for t in tags]


def legacy_role_vocabulary() -> List[dict]:
    """The pre-ARCH-3 produces/consumes vocabulary; kept only for LLM drafting prompts."""
    out = []
    for res in RESOURCES:
        out.append({"tag": f"produces:{res.key}", "side": "produces", "key": res.key,
                    "label": f"{res.label} (produces)", "meaning": res.produce})
        out.append({"tag": f"consumes:{res.key}", "side": "consumes", "key": res.key,
                    "label": f"{res.label} (consumes)", "meaning": res.consume})
    return out


def normalize_tag(value: Any, r=None) -> Optional[str]:
    """Resolve loose input to a registry tag id ('produces: Sacrifice' -> 'legacy:produces:sacrifice',
    'bird:makes' -> 'bird:makes'), else None. Thin wrapper over role_tags.resolve_role_tag."""
    try:
        index = role_tags.tag_index(r) if r is not None else {t["id"]: t for t in role_tags.legacy_tags()}
        return role_tags.resolve_role_tag(value, index)
    except role_tags.RoleTagError:
        return None


# -- validation -------------------------------------------------------------------------------

def _num(value: Any, name: str, lo: float, hi: float, as_int: bool):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArchetypeError(f"{name} must be a number.")
    try:
        if as_int and float(value) != int(value):
            raise ArchetypeError(f"{name} must be a whole number.")
        v = int(value) if as_int else float(value)
    except (OverflowError, ValueError):
        raise ArchetypeError(f"{name} must be a finite number.")
    if not (lo <= v <= hi):
        raise ArchetypeError(f"{name} must be between {lo:g} and {hi:g}.")
    return v


def clean_roles(roles: Any, r=None) -> List[dict]:
    """Strict: unknown tags / bad numbers raise ArchetypeError. Duplicate tags are merged out.

    Tags are validated against the role-tag registry (role_tags); legacy
    ``produces:<key>``/``consumes:<key>`` spellings resolve to their ``legacy:`` ids.
    """
    if roles is None:
        return []
    if not isinstance(roles, list):
        raise ArchetypeError("required_roles must be a list.")
    if len(roles) > MAX_ROLES:
        raise ArchetypeError(f"An archetype can have at most {MAX_ROLES} required roles.")
    index = role_tags.tag_index(r) if r is not None else {t["id"]: t for t in role_tags.legacy_tags()}
    out, seen = [], set()
    for i, role in enumerate(roles, 1):
        if not isinstance(role, dict):
            raise ArchetypeError(f"Role {i} must be an object.")
        raw = role.get("tag", role.get("role"))
        try:
            tag = role_tags.resolve_role_tag(raw, index)
        except role_tags.RoleTagError as exc:
            raise ArchetypeError(f"Role {i}: {exc}") from exc
        if tag in seen:
            raise ArchetypeError(f"Role {i}: {tag} is listed twice.")
        seen.add(tag)
        out.append({
            "tag": tag,
            "min_count": _num(role.get("min_count", 1), f"Role {tag} min_count", 1, MAX_MIN_COUNT, True),
            "weight": round(_num(role.get("weight", 1), f"Role {tag} weight", 0.1, MAX_WEIGHT, False), 3),
        })
    return out


def clean_fields(body: Any, *, partial_from: Optional[dict] = None, r=None) -> dict:
    """Validated {name, description, required_roles, source}. With partial_from, missing keys keep their value."""
    if not isinstance(body, dict):
        raise ArchetypeError("Body must be a JSON object.")
    base = partial_from or {}

    def pick(k, default):
        return body[k] if k in body else base.get(k, default)

    name = pick("name", "")
    if not isinstance(name, str) or not name.strip():
        raise ArchetypeError("An archetype needs a name.")
    name = " ".join(name.split())
    if len(name) > MAX_NAME:
        raise ArchetypeError(f"Name is longer than {MAX_NAME} characters.")
    desc = pick("description", "")
    if not isinstance(desc, str):
        raise ArchetypeError("description must be text.")
    desc = desc.strip()
    if len(desc) > MAX_DESCRIPTION:
        raise ArchetypeError(f"Description is longer than {MAX_DESCRIPTION} characters.")
    return {"name": name, "description": desc, "source": "user",
            "required_roles": clean_roles(pick("required_roles", []), r)}


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower().replace("+", "plus ")).strip("-")
    return s or "archetype"


# -- description rendering (markdown subset -> sanitized HTML) -----------------------------------
#
# The stored ``description`` is the raw markdown source (no migration: legacy plain text is
# valid markdown). Rendering happens server-side only, through the same allow-list sanitizer
# as deck primers, with nl2br so single newlines survive. Archetypes are global, not
# deck-bound, so card-name hover wrapping is disabled (known_cards=None).

# List marker as python-markdown recognises it: up to 3 leading spaces, then -/*/+ or ``1.``
# followed by whitespace. Used only by the preprocessor below, never for parsing output.
_LIST_MARKER_RE = re.compile(r"^ {0,3}(?:[-*+]|\d{1,9}\.)\s")


def _blank_lines_before_lists(text: str) -> str:
    """GitHub-style lists: allow a list to start directly after a paragraph line.

    python-markdown requires a blank line before a list block, so ``"Key engines:\n- a"``
    would render the dashes as literal text. Users (and the UX spec mockups) expect the
    CommonMark/GitHub behaviour where a list may interrupt a paragraph. Insert the blank
    line python-markdown needs, but only where the marker is NOT already part of a list
    block (a lazy continuation line like ``"  more text"`` must not split the list)."""
    out: List[str] = []
    in_list = False
    prev_blank = True
    for line in text.split("\n"):
        if not line.strip():
            in_list = False
            prev_blank = True
            out.append(line)
            continue
        is_marker = bool(_LIST_MARKER_RE.match(line))
        if is_marker and not in_list and not prev_blank:
            out.append("")
        if is_marker:
            in_list = True
        prev_blank = False
        out.append(line)
    return "\n".join(out)


def render_description_html(desc: Optional[str]) -> str:
    """Sanitized HTML for a description's markdown source. Empty in -> empty out."""
    text = (desc or "").strip()
    if not text:
        return ""
    return sanitize_primer_html(
        _markdown.markdown(_blank_lines_before_lists(text), extensions=["nl2br"]),
        known_cards=None,
    )


_BLOCK_END_RE = re.compile(r"</(?:p|ul|ol|h3|h4)>")


def description_excerpt_html(html: str) -> str:
    """The first block-level element of rendered description HTML (registry list excerpt)."""
    if not html:
        return ""
    m = _BLOCK_END_RE.search(html)
    return html[: m.end()] if m else html


def with_rendered_descriptions(archetypes: Iterable[dict]) -> List[dict]:
    """Additive read-only ``description_html`` / ``description_excerpt_html`` on each item."""
    out = []
    for a in archetypes:
        html = render_description_html(a.get("description", ""))
        out.append({**a, "description_html": html, "description_excerpt_html": description_excerpt_html(html)})
    return out


# -- storage ----------------------------------------------------------------------------------

# Read-modify-write of the single ``archetypes`` JSON key: serialise writers so concurrent
# requests can't drop each other's edits (app runs as one process; the lock is process-wide).
_WRITE_LOCK = threading.RLock()


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        with _WRITE_LOCK:
            return fn(*a, **kw)
    return wrapper


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _bump(r) -> int:
    return int(r.incr(VERSION_KEY))


def get_version(r) -> int:
    ensure_seeded(r)
    try:
        return int(r.get(VERSION_KEY) or 0)
    except (TypeError, ValueError):
        return 0


def ensure_seeded(r) -> None:
    """No-op: the registry starts empty and stays empty until a user creates an archetype."""
    return


def _load(r) -> List[dict]:
    ensure_seeded(r)
    try:
        data = json.loads(r.get(ARCHETYPES_KEY) or "[]")
    except (TypeError, ValueError):
        return []
    return [a for a in data if isinstance(a, dict) and a.get("id")]


def _save(r, archetypes: List[dict]) -> int:
    r.set(ARCHETYPES_KEY, json.dumps(archetypes, ensure_ascii=False))
    return _bump(r)


def list_archetypes(r) -> List[dict]:
    return sorted(_load(r), key=lambda a: (a.get("name", "").lower(), a["id"]))


def get_archetype(r, archetype_id: str) -> dict:
    for a in _load(r):
        if a["id"] == archetype_id:
            return a
    raise ArchetypeNotFound(archetype_id)


def _unique_id(name: str, taken: Iterable[str]) -> str:
    taken, base = set(taken), slugify(name)
    aid, n = base, 2
    while aid in taken:
        aid, n = f"{base}-{n}", n + 1
    return aid


def _name_taken(archetypes: List[dict], name: str, skip_id: Optional[str] = None) -> bool:
    return any(a["id"] != skip_id and a.get("name", "").lower() == name.lower() for a in archetypes)


@_locked
def create_archetype(r, body: Any) -> Tuple[dict, int]:
    fields = clean_fields(body, r=r)
    archetypes = _load(r)
    if _name_taken(archetypes, fields["name"]):
        raise ArchetypeError(f"An archetype named {fields['name']!r} already exists.")
    archetype = {"id": _unique_id(fields["name"], (a["id"] for a in archetypes)), **fields,
                 "draft": False, "updated_at": _now()}
    archetypes.append(archetype)
    return archetype, _save(r, archetypes)


@_locked
def update_archetype(r, archetype_id: str, body: Any) -> Tuple[dict, int]:
    archetypes = _load(r)
    for i, a in enumerate(archetypes):
        if a["id"] == archetype_id:
            fields = clean_fields(body, partial_from=a, r=r)
            if _name_taken(archetypes, fields["name"], skip_id=archetype_id):
                raise ArchetypeError(f"An archetype named {fields['name']!r} already exists.")
            archetypes[i] = {"id": archetype_id, **fields, "draft": False, "updated_at": _now()}
            return archetypes[i], _save(r, archetypes)
    raise ArchetypeNotFound(archetype_id)


@_locked
def delete_archetype(r, archetype_id: str) -> int:
    archetypes = _load(r)
    kept = [a for a in archetypes if a["id"] != archetype_id]
    if len(kept) == len(archetypes):
        raise ArchetypeNotFound(archetype_id)
    return _save(r, kept)


# -- matching (whole-deck role coverage via the role-tag registry, spec §4) ---------------------

def archetype_match(deck: dict, archetypes: Iterable[dict], r=None, source=None,
                    tag_sets: Optional[Mapping[str, dict]] = None) -> List[dict]:
    """Rank archetypes by role coverage. Deterministic given the same tag evaluations.

    ``deck`` = {"cards": [{"name", "quantity" (default 1)}]}. Each role's tag is evaluated
    against the WHOLE deck: the tag's Scryfall-query name set (cached in ``tag_query_index``)
    intersected with the deck's card names; a role's count is the number of COPIES matched.
    Role coverage = min(count / min_count, 1); archetype coverage = weighted mean (0..1, 4 dp).
    Ties break on name then id. An archetype with no roles scores 0.

    Every role row carries the matched card names (``cards``) and a ``state`` of
    ok / short / empty / blocked (spec §3.5) — a failed or oversized query is ``blocked``
    with the error text, never a silent 0.

    ``tag_sets`` (from role_tags.resolve_tag_sets) lets a pure caller pre-resolve every
    tag once; otherwise evaluation reads the cache through ``r`` and falls back to
    ``source`` (default: live Scryfall) on cold entries.
    """
    cards = (deck or {}).get("cards", [])
    index = None
    if tag_sets is None:
        index = role_tags.tag_index(r)
    ranked = []
    for a in archetypes:
        hit, missing, got, total = [], [], 0.0, 0.0
        for role in a.get("required_roles", []):
            need, w, raw = role["min_count"], float(role["weight"]), role["tag"]
            if tag_sets is not None:
                res = _evaluate_from_sets(raw, cards, tag_sets, index)
                if index is None:
                    index = {}
            else:
                res = role_tags.evaluate_role(r, raw, cards, source, index=index)
            have = res["have"]
            total += w
            got += w * min(have / need, 1.0) if res["state"] != "blocked" else 0.0
            row = {"tag": res["tag"], "label": res["label"], "query": res["query"],
                   "have": have, "cards": res["cards"], "min_count": need, "weight": w}
            if have >= need:
                hit.append(row)
            else:
                state = res["state"] if res["state"] in ("empty", "blocked") else "short"
                if res.get("error"):
                    row["error"] = res["error"]
                missing.append({**row, "state": state})
        ranked.append({"archetype": {"id": a["id"], "name": a.get("name", a["id"])},
                       "coverage": round(got / total, 4) if total else 0.0,
                       "roles_hit": hit, "roles_missing": missing})
    ranked.sort(key=lambda m: (-m["coverage"], m["archetype"]["name"].lower(), m["archetype"]["id"]))
    return ranked


def _evaluate_from_sets(raw: Any, cards: list, tag_sets: Mapping[str, dict],
                        index: Optional[Mapping[str, dict]]) -> dict:
    """evaluate_role against pre-resolved name sets (review_metrics' pure path)."""
    idx = index or {}
    try:
        tag_id = role_tags.resolve_role_tag(raw, {**{k: {"id": k, **(v or {})} for k, v in tag_sets.items()}, **idx})
    except role_tags.RoleTagError as exc:
        return {"tag": str(raw), "label": str(raw), "query": "", "have": 0, "cards": [],
                "matched": 0, "state": "blocked", "error": str(exc), "total": 0, "built_at": None}
    if tag_id in tag_sets:
        info = tag_sets[tag_id]
        entry = {"names": sorted(info["names"]) if info.get("names") is not None else None,
                 "error": info.get("error"), "oversized": info.get("oversized"),
                 "total": info.get("total", 0), "built_at": info.get("built_at")}
        out = role_tags.evaluate_query(info.get("query", ""), cards, entry)
        return {"tag": tag_id, "label": info.get("label") or tag_id,
                "query": info.get("query", ""), **out}
    tag = idx.get(tag_id) or {"id": tag_id, "label": tag_id, "query": ""}
    return {"tag": tag_id, "label": tag.get("label") or tag_id, "query": tag.get("query", ""),
            "have": 0, "cards": [], "matched": 0, "state": "blocked",
            "error": "tag was not pre-resolved", "total": 0, "built_at": None}


def best_match(deck: dict, archetypes: Iterable[dict], r=None, source=None) -> Optional[dict]:
    """The top archetype_match row, or None when there are no archetypes to rank."""
    ranked = archetype_match(deck, archetypes, r, source)
    return ranked[0] if ranked else None


# -- per-deck selection (auto-derived, or a manual override) ------------------------------------

def default_selection() -> dict:
    return {"mode": "auto", "archetype_id": None, "updated_at": None}


def get_selection(r, registry_id: str) -> dict:
    raw = r.get(SEL_KEY.format(registry_id=registry_id))
    if raw is None:
        return default_selection()
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return default_selection()
    if not isinstance(data, dict) or data.get("mode") not in MODES:
        return default_selection()
    return data


@_locked
def set_selection(r, registry_id: str, body: Any) -> dict:
    """Body {mode: 'auto'} or {mode: 'manual', archetype_id}. A manual pick must name a real archetype."""
    if not isinstance(body, dict):
        raise ArchetypeError("Body must be a JSON object.")
    mode = body.get("mode")
    if mode not in MODES:
        raise ArchetypeError(f"mode must be one of {', '.join(MODES)}.")
    rec = {"mode": mode, "archetype_id": None, "updated_at": _now()}
    if mode == "manual":
        aid = body.get("archetype_id")
        if not isinstance(aid, str) or not aid.strip():
            raise ArchetypeError("archetype_id is required for a manual selection.")
        get_archetype(r, aid)  # raises ArchetypeNotFound if unknown
        rec["archetype_id"] = aid
    r.set(SEL_KEY.format(registry_id=registry_id), json.dumps(rec))
    return rec


def reset_selection(r, registry_id: str) -> dict:
    return set_selection(r, registry_id, {"mode": "auto"})


def resolve_for_deck(r, registry_id: str, deck_cards, source=None) -> dict:
    """{selection, archetype, matches}: the deck's effective archetype (manual override, else the
    best whole-deck derived match) plus the full ranking.

    ``deck_cards`` is the deck's plain card list — [{"name", "quantity"}] or a deck record
    (anything role_tags.deck_cards_from_deck accepts, i.e. a mapping with ``cards``).
    """
    archetypes = list_archetypes(r)
    if isinstance(deck_cards, Mapping):
        deck = {"cards": role_tags.deck_cards_from_deck(deck_cards)}
    else:
        deck = {"cards": deck_cards or []}
    matches = archetype_match(deck, archetypes, r, source)
    selection = get_selection(r, registry_id)
    archetype = None
    if selection["mode"] == "manual" and selection.get("archetype_id"):
        try:
            archetype = get_archetype(r, selection["archetype_id"])
        except ArchetypeNotFound:
            archetype = None
    if archetype is None and matches:
        best_id = matches[0]["archetype"]["id"]
        archetype = next((a for a in archetypes if a["id"] == best_id), None)
    return {"selection": selection, "archetype": archetype, "matches": matches}
