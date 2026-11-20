"""Genre registry (RV-1): the user-controlled list of deck genres.

Deep Reviews label a deck ONLY with genres from this registry, so labels stay
repeatable (docs/REVIEW_SPEC.md §1). A genre is

    {id, name, description, required_roles: [{tag, min_count, weight}],
     source: "user" | "autofill", draft: bool, updated_at}

A role ``tag`` is a role-tag registry id (analytics/role_tags.py), shared with the
archetype registry: every tag is one Scryfall query, scored by intersecting the
query's cached name set with the deck's cards. Legacy ``produces:<resource>`` /
``consumes:<resource>`` spellings resolve to ``legacy:<side>:<key>`` tags with
identical membership (docs/role-tags-spec.md §3.2).

Redis: ``genres`` (JSON list, absent until first read, which seeds it) and
``genres:version`` (integer, bumped on every create/update/delete).

``genre_match`` is a pure function: same deck + same genres -> identical output.
"""
from __future__ import annotations

import functools
import json
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from src.server.domain.analytics import role_tags

GENRES_KEY = "genres"
VERSION_KEY = "genres:version"
SOURCES = ("user", "autofill")
MAX_NAME = 60
MAX_DESCRIPTION = 1200
MAX_ROLES = 12
MAX_MIN_COUNT = 40
MAX_WEIGHT = 10.0


class GenreError(ValueError):
    """Invalid genre payload (HTTP 400)."""


class GenreNotFound(KeyError):
    """No genre with that id (HTTP 404)."""


# -- vocabulary -------------------------------------------------------------------------------
#
# The role layer is no longer duplicated here (it drifted once already; spec §6.3/OQ-7):
# genres and archetypes share the role-tag registry in analytics/role_tags.py. These
# thin wrappers keep the module's public names working.

def role_vocabulary(r=None) -> List[dict]:
    """Every valid role tag from the shared registry (id, label, query, facet)."""
    from src.server.domain.analytics import archetypes
    return archetypes.role_vocabulary(r)


def legacy_role_vocabulary() -> List[dict]:
    """The pre-ARCH-3 produces/consumes vocabulary; kept only for the autofill LLM prompt."""
    from src.server.domain.analytics import archetypes
    return archetypes.legacy_role_vocabulary()


def normalize_tag(value: Any, r=None) -> Optional[str]:
    """Resolve loose input to a registry tag id, else None (wrapper over role_tags.resolve_role_tag)."""
    try:
        index = role_tags.tag_index(r) if r is not None else {t["id"]: t for t in role_tags.legacy_tags()}
        return role_tags.resolve_role_tag(value, index)
    except role_tags.RoleTagError:
        return None


# -- validation -------------------------------------------------------------------------------

def _num(value: Any, name: str, lo: float, hi: float, as_int: bool):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GenreError(f"{name} must be a number.")
    try:
        if as_int and float(value) != int(value):
            raise GenreError(f"{name} must be a whole number.")
        v = int(value) if as_int else float(value)
    except (OverflowError, ValueError):
        raise GenreError(f"{name} must be a finite number.")
    if not (lo <= v <= hi):
        raise GenreError(f"{name} must be between {lo:g} and {hi:g}.")
    return v


def clean_roles(roles: Any, r=None) -> List[dict]:
    """Strict: unknown tags / bad numbers raise GenreError. Duplicate tags are merged out.

    Tags are validated against the shared role-tag registry; legacy
    ``produces:<key>``/``consumes:<key>`` spellings resolve to their ``legacy:`` ids.
    """
    if roles is None:
        return []
    if not isinstance(roles, list):
        raise GenreError("required_roles must be a list.")
    if len(roles) > MAX_ROLES:
        raise GenreError(f"A genre can have at most {MAX_ROLES} required roles.")
    index = role_tags.tag_index(r) if r is not None else {t["id"]: t for t in role_tags.legacy_tags()}
    out, seen = [], set()
    for i, role in enumerate(roles, 1):
        if not isinstance(role, dict):
            raise GenreError(f"Role {i} must be an object.")
        raw = role.get("tag", role.get("role"))
        try:
            tag = role_tags.resolve_role_tag(raw, index)
        except role_tags.RoleTagError as exc:
            raise GenreError(f"Role {i}: {exc}") from exc
        if tag in seen:
            raise GenreError(f"Role {i}: {tag} is listed twice.")
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
        raise GenreError("Body must be a JSON object.")
    base = partial_from or {}

    def pick(k, default):
        return body[k] if k in body else base.get(k, default)

    name = pick("name", "")
    if not isinstance(name, str) or not name.strip():
        raise GenreError("A genre needs a name.")
    name = " ".join(name.split())
    if len(name) > MAX_NAME:
        raise GenreError(f"Name is longer than {MAX_NAME} characters.")
    desc = pick("description", "")
    if not isinstance(desc, str):
        raise GenreError("description must be text.")
    desc = desc.strip()
    if len(desc) > MAX_DESCRIPTION:
        raise GenreError(f"Description is longer than {MAX_DESCRIPTION} characters.")
    source = pick("source", "user")
    if source not in SOURCES:
        raise GenreError(f"source must be one of {', '.join(SOURCES)}.")
    return {"name": name, "description": desc, "source": source,
            "required_roles": clean_roles(pick("required_roles", []), r)}


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower().replace("+", "plus ")).strip("-")
    return s or "genre"


# -- storage ----------------------------------------------------------------------------------

# Read-modify-write of the single ``genres`` JSON key: serialise writers so concurrent requests
# can't drop each other's edits (app runs as one process; the lock is process-wide).
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
    """No-op: the registry starts empty and stays empty until a user creates a genre."""
    return


def _load(r) -> List[dict]:
    ensure_seeded(r)
    try:
        data = json.loads(r.get(GENRES_KEY) or "[]")
    except (TypeError, ValueError):
        return []
    return [g for g in data if isinstance(g, dict) and g.get("id")]


def _save(r, genres: List[dict]) -> int:
    r.set(GENRES_KEY, json.dumps(genres, ensure_ascii=False))
    return _bump(r)


def list_genres(r) -> List[dict]:
    return sorted(_load(r), key=lambda g: (g.get("name", "").lower(), g["id"]))


def get_genre(r, genre_id: str) -> dict:
    for g in _load(r):
        if g["id"] == genre_id:
            return g
    raise GenreNotFound(genre_id)


def _unique_id(name: str, taken: Iterable[str]) -> str:
    taken, base = set(taken), slugify(name)
    gid, n = base, 2
    while gid in taken:
        gid, n = f"{base}-{n}", n + 1
    return gid


def _name_taken(genres: List[dict], name: str, skip_id: Optional[str] = None) -> bool:
    return any(g["id"] != skip_id and g.get("name", "").lower() == name.lower() for g in genres)


@_locked
def create_genre(r, body: Any) -> Tuple[dict, int]:
    fields = clean_fields(body, r=r)
    genres = _load(r)
    if _name_taken(genres, fields["name"]):
        raise GenreError(f"A genre named {fields['name']!r} already exists.")
    genre = {"id": _unique_id(fields["name"], (g["id"] for g in genres)), **fields,
             "draft": False, "updated_at": _now()}
    genres.append(genre)
    return genre, _save(r, genres)


@_locked
def update_genre(r, genre_id: str, body: Any) -> Tuple[dict, int]:
    genres = _load(r)
    for i, g in enumerate(genres):
        if g["id"] == genre_id:
            fields = clean_fields(body, partial_from=g, r=r)
            if _name_taken(genres, fields["name"], skip_id=genre_id):
                raise GenreError(f"A genre named {fields['name']!r} already exists.")
            genres[i] = {"id": genre_id, **fields, "draft": False, "updated_at": _now()}
            return genres[i], _save(r, genres)
    raise GenreNotFound(genre_id)


@_locked
def delete_genre(r, genre_id: str) -> int:
    genres = _load(r)
    kept = [g for g in genres if g["id"] != genre_id]
    if len(kept) == len(genres):
        raise GenreNotFound(genre_id)
    return _save(r, kept)


# -- autofill (oracle drafts, the app validates) ------------------------------------------------

AUTOFILL_SYSTEM = (
    "You are drafting a deck-genre definition for a Magic: The Gathering Commander deck tool. "
    "Reply with ONE fenced ```json block and nothing else."
)

_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.S)


def autofill_prompt(theme: str) -> str:
    vocab = "\n".join(f"- {v['tag']}: {v['meaning']}" for v in legacy_role_vocabulary())
    return (
        f"Theme: {theme}\n\n"
        "If this is a recognised MTG Commander deck theme/archetype, draft its genre. Describe it in 1-3 plain "
        "sentences and list the card roles a deck of that genre needs, using ONLY these role tags "
        "(side:resource):\n" + vocab + "\n\n"
        'JSON shape: {"known": true, "description": "...", "required_roles": '
        '[{"tag": "produces:sacrifice", "min_count": 3, "weight": 2}]}\n'
        "min_count = how many cards in a 99-card deck should carry the role (1-40); weight = importance (0.1-10). "
        'If it is not a known MTG theme, reply {"known": false}. Do not invent tags outside the list.'
    )


def parse_oracle_reply(text: str) -> dict:
    cands = _FENCE.findall(text or "") or [text or ""]
    for c in reversed(cands):
        try:
            v = json.loads(c.strip())
        except ValueError:
            continue
        if isinstance(v, dict):
            return v
    raise GenreError("oracle's reply had no parseable JSON block.")


def validate_autofill(theme: str, reply: dict) -> dict:
    """Turn oracle's draft into a savable one. Unknown tags / bad numbers are dropped and listed in
    ``flagged`` (never silently kept); nothing is saved."""
    if reply.get("known") is False:
        raise GenreError(f"oracle doesn't recognise {theme!r} as an MTG theme. Fill it in by hand.")
    desc = reply.get("description")
    desc = " ".join(desc.split())[:MAX_DESCRIPTION] if isinstance(desc, str) else ""
    roles, flagged, seen = [], [], set()
    raw_roles = reply.get("required_roles")
    for raw in raw_roles if isinstance(raw_roles, list) else []:
        label = raw.get("tag", raw.get("role")) if isinstance(raw, dict) else raw
        try:
            if not isinstance(raw, dict):
                raise GenreError("not an object")
            (role,) = clean_roles([raw])
        except GenreError as exc:
            reason = "unknown tag" if normalize_tag(label) is None else str(exc)
            flagged.append({"tag": label if isinstance(label, str) else repr(label), "reason": reason})
            continue
        if role["tag"] in seen:
            flagged.append({"tag": role["tag"], "reason": "duplicate"})
            continue
        seen.add(role["tag"])
        roles.append(role)
    if len(roles) > MAX_ROLES:
        flagged += [{"tag": x["tag"], "reason": "too many roles"} for x in roles[MAX_ROLES:]]
        roles = roles[:MAX_ROLES]
    if not roles:
        raise GenreError("oracle returned no usable roles in the app's tag vocabulary. Fill it in by hand.")
    return {"name": " ".join(theme.split())[:MAX_NAME], "description": desc, "required_roles": roles,
            "source": "autofill", "flagged": flagged}


# -- matching ---------------------------------------------------------------------------------

def genre_match(deck: dict, genres: Iterable[dict], r=None, source=None,
                tag_sets: Optional[Mapping[str, dict]] = None) -> List[dict]:
    """Rank genres by role coverage via the shared role-tag registry (spec §4).

    ``deck`` = {"cards": [{"name", "quantity" (default 1)}]}. A role's count is the number
    of COPIES of the deck's cards matched by the tag's Scryfall query. Role coverage =
    min(count / min_count, 1); genre coverage = weighted mean (0..1, 4 dp). Ties break on
    name then id. A genre with no roles scores 0. Role rows carry the matched card names
    and a state of ok / short / empty / blocked (never a silent 0).
    """
    from src.server.domain.analytics import archetypes

    rows = archetypes.archetype_match(deck, genres, r, source, tag_sets)
    out = []
    for row in rows:
        out.append({"genre": row["archetype"], "coverage": row["coverage"],
                    "roles_hit": row["roles_hit"], "roles_missing": row["roles_missing"]})
    out.sort(key=lambda m: (-m["coverage"], m["genre"]["name"].lower(), m["genre"]["id"]))
    return out
