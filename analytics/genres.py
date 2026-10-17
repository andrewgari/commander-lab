"""Genre registry (RV-1): the user-controlled list of deck genres.

Deep Reviews label a deck ONLY with genres from this registry, so labels stay
repeatable (docs/REVIEW_SPEC.md §1). A genre is

    {id, name, description, required_roles: [{tag, min_count, weight}],
     source: "user" | "autofill", draft: bool, updated_at}

A role ``tag`` is ``"<side>:<resource>"`` in the interaction-graph vocabulary
(analytics/interactions/vocab.py): side is ``produces`` or ``consumes``, resource
is a vocabulary key, e.g. ``produces:sacrifice`` = "is a sacrifice outlet".

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
from typing import Any, Dict, Iterable, List, Optional, Tuple

from analytics.interactions.vocab import RESOURCES, RESOURCE_BY_KEY

GENRES_KEY = "genres"
VERSION_KEY = "genres:version"
SIDES = ("produces", "consumes")
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

def role_vocabulary() -> List[dict]:
    """Every valid role tag, in vocabulary order, with a human label."""
    out = []
    for res in RESOURCES:
        out.append({"tag": f"produces:{res.key}", "side": "produces", "key": res.key,
                    "label": f"{res.label} (produces)", "meaning": res.produce})
        out.append({"tag": f"consumes:{res.key}", "side": "consumes", "key": res.key,
                    "label": f"{res.label} (consumes)", "meaning": res.consume})
    return out


def normalize_tag(value: Any) -> Optional[str]:
    """'produces:sacrifice' for loose input ('Produces: Sacrifice', 'consumes/life gain'), else None."""
    if not isinstance(value, str):
        return None
    m = re.match(r"^\s*(produces?|consumes?)\s*[:/.\-\s]\s*(.+?)\s*$", value, re.I)
    if not m:
        return None
    side = "produces" if m.group(1).lower().startswith("produce") else "consumes"
    key = re.sub(r"[\s\-/]+", "_", m.group(2).strip().lower())
    if key not in RESOURCE_BY_KEY:
        for res in RESOURCES:
            if re.sub(r"[\s\-/+]+", "_", res.label.lower()) == key:
                key = res.key
                break
        else:
            return None
    return f"{side}:{key}"


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


def clean_roles(roles: Any) -> List[dict]:
    """Strict: unknown tags / bad numbers raise GenreError. Duplicate tags are merged out."""
    if roles is None:
        return []
    if not isinstance(roles, list):
        raise GenreError("required_roles must be a list.")
    if len(roles) > MAX_ROLES:
        raise GenreError(f"A genre can have at most {MAX_ROLES} required roles.")
    out, seen = [], set()
    for i, role in enumerate(roles, 1):
        if not isinstance(role, dict):
            raise GenreError(f"Role {i} must be an object.")
        raw = role.get("tag", role.get("role"))
        tag = normalize_tag(raw)
        if tag is None:
            raise GenreError(f"Role {i}: unknown tag {raw!r}. Use produces:<resource> or consumes:<resource>.")
        if tag in seen:
            raise GenreError(f"Role {i}: {tag} is listed twice.")
        seen.add(tag)
        out.append({
            "tag": tag,
            "min_count": _num(role.get("min_count", 1), f"Role {tag} min_count", 1, MAX_MIN_COUNT, True),
            "weight": round(_num(role.get("weight", 1), f"Role {tag} weight", 0.1, MAX_WEIGHT, False), 3),
        })
    return out


def clean_fields(body: Any, *, partial_from: Optional[dict] = None) -> dict:
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
            "required_roles": clean_roles(pick("required_roles", []))}


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower().replace("+", "plus ")).strip("-")
    return s or "genre"


# -- seeds ------------------------------------------------------------------------------------

def _roles(*rows) -> List[dict]:
    return [{"tag": t, "min_count": n, "weight": w} for t, n, w in rows]


SEED_GENRES: Tuple[dict, ...] = (
    {"id": "aristocrats", "name": "Aristocrats",
     "description": "Sacrifice creatures for value: outlets, death triggers and fodder, drained out as damage or life loss.",
     "required_roles": _roles(("produces:sacrifice", 3, 2), ("consumes:death", 4, 2), ("produces:tokens", 4, 1), ("produces:life_loss", 2, 1))},
    {"id": "impulse-draw-cast", "name": "Impulse draw / cast",
     "description": "Exile-and-play card advantage (impulse draw), played the same turn, backed by mana to use what you reveal.",
     "required_roles": _roles(("produces:cards", 8, 2), ("produces:mana", 6, 1), ("consumes:spells", 4, 1))},
    {"id": "storm", "name": "Storm",
     "description": "Chain many cheap spells in one turn with mana and card flow, then cash in with a payoff.",
     "required_roles": _roles(("produces:mana", 7, 2), ("consumes:spells", 10, 2), ("produces:cards", 6, 1), ("produces:spells", 10, 1))},
    {"id": "kindred-tribal", "name": "Kindred / tribal",
     "description": "One creature type is the plan. The tag vocabulary has no creature-type role, so this draft scores the generic shape (bodies in, ETB and attack payoffs). Edit it per tribe.",
     "required_roles": _roles(("produces:tokens", 4, 1), ("consumes:etb", 4, 1), ("consumes:attacks", 3, 1))},
    {"id": "spellslinger", "name": "Spellslinger",
     "description": "Instants and sorceries as the engine: magecraft and prowess payoffs, cheap spells and card flow.",
     "required_roles": _roles(("produces:spells", 12, 2), ("consumes:spells", 6, 2), ("produces:cards", 6, 1))},
    {"id": "voltron", "name": "Voltron",
     "description": "Win through one heavily equipped or enchanted attacker: attack enablers, attack payoffs, auras and equipment.",
     "required_roles": _roles(("produces:attacks", 4, 2), ("consumes:attacks", 4, 2), ("consumes:artifacts", 3, 1), ("consumes:enchantments", 3, 1))},
    {"id": "tokens", "name": "Tokens",
     "description": "Make many tokens, then go wide with anthems and token payoffs.",
     "required_roles": _roles(("produces:tokens", 10, 2), ("consumes:tokens", 5, 2), ("consumes:etb", 3, 1))},
    {"id": "plus-one-counters", "name": "+1/+1 counters",
     "description": "Put counters on things, multiply them (doublers, proliferate) and pay off having them.",
     "required_roles": _roles(("produces:counters", 8, 2), ("consumes:counters", 6, 2))},
    {"id": "reanimator", "name": "Reanimator",
     "description": "Fill the graveyard, then return big creatures to the battlefield cheaply.",
     "required_roles": _roles(("produces:graveyard", 6, 2), ("consumes:graveyard", 6, 2), ("produces:etb", 3, 1))},
    {"id": "landfall", "name": "Landfall",
     "description": "Extra land drops and fetches, paid off whenever a land enters.",
     "required_roles": _roles(("produces:lands", 8, 2), ("consumes:lands", 6, 2))},
    {"id": "superfriends", "name": "Superfriends",
     "description": "Planeswalkers as the plan. The tag vocabulary has no planeswalker role, so this draft scores protection and loyalty support (tokens, counters, card flow). Edit to taste.",
     "required_roles": _roles(("produces:tokens", 5, 1), ("produces:counters", 3, 1), ("produces:cards", 5, 1))},
    {"id": "stax", "name": "Stax",
     "description": "Tax or lock the table's resources while you run ahead on mana. The tag vocabulary has no lock role, so this draft scores the mana side only. Edit to taste.",
     "required_roles": _roles(("produces:mana", 8, 2), ("produces:artifacts", 4, 1), ("consumes:artifacts", 3, 1))},
    {"id": "group-hug", "name": "Group hug",
     "description": "Give every player cards, mana or life and steer the table with the goodwill.",
     "required_roles": _roles(("produces:cards", 6, 1), ("produces:mana", 6, 1), ("produces:life_gain", 3, 1))},
)


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
    """First read creates the registry from SEED_GENRES (editable drafts). NX so two racers seed once."""
    if r.get(GENRES_KEY) is not None:
        return
    now = _now()
    seeds = [{**s, "required_roles": [dict(x) for x in s["required_roles"]], "source": "user",
              "draft": True, "updated_at": now} for s in SEED_GENRES]
    if r.set(GENRES_KEY, json.dumps(seeds), nx=True):
        _bump(r)


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
    fields = clean_fields(body)
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
            fields = clean_fields(body, partial_from=g)
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
    vocab = "\n".join(f"- {v['tag']}: {v['meaning']}" for v in role_vocabulary())
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

def deck_from_graph(graph: dict, quantities: Optional[Dict[str, int]] = None) -> dict:
    """Adapt a deck interaction graph (service.get_deck_graph) to genre_match's input.
    Graph nodes are unique by name, so pass ``quantities`` {name: copies} for real copy counts."""
    q = quantities or {}
    return {"cards": [{"name": n.get("name", ""), "quantity": q.get(n.get("name", ""), 1), "produces": n.get("produces", []),
                       "consumes": n.get("consumes", [])} for n in (graph or {}).get("nodes", [])]}


def genre_match(deck: dict, genres: Iterable[dict]) -> List[dict]:
    """Rank genres by role coverage. Pure and deterministic.

    ``deck`` = {"cards": [{"name", "quantity" (default 1), "produces": [...], "consumes": [...]}]}.
    A role's count is the number of copies of cards carrying its tag. Role coverage =
    min(count / min_count, 1); genre coverage = weighted mean of role coverage (0..1, 4 dp).
    Ties break on name then id. A genre with no roles scores 0.
    """
    counts: Dict[str, int] = {}
    for card in (deck or {}).get("cards", []):
        q = card.get("quantity", 1)
        qty = q if isinstance(q, int) and not isinstance(q, bool) and q > 0 else 1
        for side in SIDES:
            for key in set(card.get(side) or ()):
                counts[f"{side}:{key}"] = counts.get(f"{side}:{key}", 0) + qty
    ranked = []
    for g in genres:
        hit, missing, got, total = [], [], 0.0, 0.0
        for role in g.get("required_roles", []):
            need, w, tag = role["min_count"], float(role["weight"]), role["tag"]
            have = counts.get(tag, 0)
            total += w
            got += w * min(have / need, 1.0)
            (hit if have >= need else missing).append(
                {"tag": tag, "have": have, "min_count": need, "weight": w})
        ranked.append({"genre": {"id": g["id"], "name": g.get("name", g["id"])},
                       "coverage": round(got / total, 4) if total else 0.0,
                       "roles_hit": hit, "roles_missing": missing})
    ranked.sort(key=lambda m: (-m["coverage"], m["genre"]["name"].lower(), m["genre"]["id"]))
    return ranked
