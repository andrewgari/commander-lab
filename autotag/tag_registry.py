"""Auto Tag vocabulary registry (Vocab cleanup PR4).

FUNCTION_TAGS and THEME_TAGS used to be hardcoded in autotag/engine.py. They
are now a Redis-backed, user-editable registry: Andrew can add, rename, and
delete tag *names* (the vocabulary the heuristic engine is allowed to output)
from the UI, with no hardcoded, undeletable entries.

Design notes (read before changing matching logic):

* The engine's regex ``RULES`` (engine.py §3/§4) are written against fixed
  literal tag strings (e.g. ``{"tag": "ramp", ...}``). Those rules are NOT
  reflowed through this registry -- that is out of scope here (it would mean
  rewriting hundreds of hand-tuned regexes). Instead this registry governs
  which tag *names are currently valid output*: ``engine.run_auto_tag``
  filters its results to intersect the live registry, so deleting a tag here
  makes the engine stop emitting it even though its matching rule still fires
  internally (the rule's evidence is simply dropped at selection time).
* Builtin ids are seeded ONCE from the original hardcoded lists so existing
  tagged decks don't lose data. After that first seed this module never
  reseeds -- a user-deleted tag stays deleted, forever, even across restarts.
* An id is permanent once created; "rename" only changes the ``name`` field
  used for seeding order/labels. Internally the engine's RULES always refer
  to the *id*, so renaming a builtin tag whose id matches a wired-in rule
  (e.g. renaming "ramp" to "mana_ramp") detaches it from that rule -- the old
  rule keeps firing under the literal "ramp" string, which is no longer a
  registered id, so it gets silently dropped by the output filter. This is
  intentional and documented rather than hidden: renaming a rule-backed tag
  is effectively "stop auto-assigning it, keep it in the pick-list for manual
  use going forward." New user-created tags have no matching rule at all, so
  they are manual-only entries by construction -- also intentional, since
  authoring new heuristic regexes is out of scope for a vocabulary edit.
* PRIORITY_ORDER (the §6.3 tie-break order) is derived from registry order,
  not hardcoded: function tags first (registry order), then ``kindred:*``,
  then theme tags (registry order). New tags sort after existing ones of
  the same category; deleting one just removes it from the order.

Redis layout:
    autotag:tags          JSON list of {id, name, category, builtin, updated_at}
    autotag:tags:seeded   presence flag; set once, never cleared
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional, Tuple

REGISTRY_KEY = "autotag:tags"
SEEDED_KEY = "autotag:tags:seeded"
CATEGORIES = ("function", "theme")
MAX_NAME = 40

# The original hardcoded taxonomy (engine.py, pre-PR4). Used only to seed the
# registry the first time it's read; never consulted again afterwards.
BUILTIN_FUNCTION_TAGS: List[str] = [
    "ramp", "card_advantage", "removal", "board_wipe", "counterspell",
    "finisher", "tutor", "recursion", "protection", "mana_fixing",
]

BUILTIN_THEME_TAGS: List[str] = [
    "lands_matter", "tokens", "counters", "sacrifice", "graveyard",
    "spellslinger", "artifacts", "enchantments", "lifegain", "voltron", "blink",
]


class TagRegistryError(ValueError):
    """Invalid tag payload (HTTP 400)."""


class TagNotFound(KeyError):
    """No tag with that id (HTTP 404)."""


_WRITE_LOCK = threading.RLock()


def _locked(fn):
    def wrapper(*a, **kw):
        with _WRITE_LOCK:
            return fn(*a, **kw)
    return wrapper


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return s or "tag"


def ensure_seeded(r) -> None:
    """Seed the registry from the builtin lists exactly once; never again."""
    if r.get(SEEDED_KEY) is not None:
        return
    with _WRITE_LOCK:
        if r.get(SEEDED_KEY) is not None:
            return
        if r.get(REGISTRY_KEY) is None:
            now = _now()
            seed = (
                [{"id": t, "name": t, "category": "function", "builtin": True, "updated_at": now}
                 for t in BUILTIN_FUNCTION_TAGS]
                + [{"id": t, "name": t, "category": "theme", "builtin": True, "updated_at": now}
                   for t in BUILTIN_THEME_TAGS]
            )
            r.set(REGISTRY_KEY, json.dumps(seed, ensure_ascii=False))
        r.set(SEEDED_KEY, "1")


def _load(r) -> List[dict]:
    ensure_seeded(r)
    try:
        data = json.loads(r.get(REGISTRY_KEY) or "[]")
    except (TypeError, ValueError):
        return []
    return [t for t in data if isinstance(t, dict) and t.get("id") and t.get("category") in CATEGORIES]


def _save(r, tags: List[dict]) -> None:
    r.set(REGISTRY_KEY, json.dumps(tags, ensure_ascii=False))


def list_tags(r) -> List[dict]:
    """All tags, registry (insertion) order -- this IS the priority order."""
    return _load(r)


def get_function_ids(r) -> List[str]:
    return [t["id"] for t in _load(r) if t["category"] == "function"]


def get_theme_ids(r) -> List[str]:
    return [t["id"] for t in _load(r) if t["category"] == "theme"]


def get_all_ids(r) -> set:
    return {t["id"] for t in _load(r)}


def _unique_id(name: str, taken: Iterable[str]) -> str:
    taken, base = set(taken), slugify(name)
    tid, n = base, 2
    while tid in taken:
        tid, n = f"{base}_{n}", n + 1
    return tid


def _check_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise TagRegistryError("A tag needs a name.")
    name = " ".join(name.split())
    if len(name) > MAX_NAME:
        raise TagRegistryError(f"Name is longer than {MAX_NAME} characters.")
    return name


@_locked
def create_tag(r, name: Any, category: Any) -> dict:
    if category not in CATEGORIES:
        raise TagRegistryError(f"category must be one of {', '.join(CATEGORIES)}.")
    clean_name = _check_name(name)
    tags = _load(r)
    if any(t["name"].lower() == clean_name.lower() for t in tags):
        raise TagRegistryError(f"A tag named {clean_name!r} already exists.")
    tag = {"id": _unique_id(clean_name, (t["id"] for t in tags)), "name": clean_name,
           "category": category, "builtin": False, "updated_at": _now()}
    tags.append(tag)
    _save(r, tags)
    return tag


@_locked
def rename_tag(r, tag_id: str, new_name: Any) -> dict:
    """Changes only the display ``name``. The ``id`` -- what the engine's RULES and any
    existing card_tags data reference -- never changes once created (see module docstring)."""
    clean_name = _check_name(new_name)
    tags = _load(r)
    for i, t in enumerate(tags):
        if t["id"] == tag_id:
            if any(o["id"] != tag_id and o["name"].lower() == clean_name.lower() for o in tags):
                raise TagRegistryError(f"A tag named {clean_name!r} already exists.")
            tags[i] = {**t, "name": clean_name, "updated_at": _now()}
            _save(r, tags)
            return tags[i]
    raise TagNotFound(tag_id)


@_locked
def delete_tag(r, tag_id: str) -> dict:
    """Removes the tag from the registry. Does NOT touch existing card_tags/deck_tags data in
    Redis -- those records simply stop containing anything the UI treats as a valid option, and
    the engine stops emitting this id on future runs."""
    tags = _load(r)
    for t in tags:
        if t["id"] == tag_id:
            _save(r, [x for x in tags if x["id"] != tag_id])
            return t
    raise TagNotFound(tag_id)
