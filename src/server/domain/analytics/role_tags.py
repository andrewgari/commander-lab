"""Role tag registry (ARCH-3): user-defined role tags, each backed by ONE Scryfall query.

Spec: docs/role-tags-spec.md. A *tag* is ``{id, target_id, facet, label, query,
explanation, source, created_at, updated_at}``; a *target* is a grouping noun
(``{id, label, kind, signal}``). Archetype/genre Required Roles point at tag ids;
scoring evaluates each tag's query against the whole deck via a persistent
Scryfall result cache (``tag_query_index``) and intersects card names.

Redis layout (all new keys; nothing existing is touched):

    role_tags            JSON list of Tag records (source of truth)
    role_tags:version    int, bumped on any tag/target write
    role_targets         JSON list of Target records
    tag_query_index      JSON {query_hash: {names, total, error, oversized, built_at}}

Legacy ``produces:<key>`` / ``consumes:<key>`` roles resolve at read time to
``legacy:produces:<key>`` / ``legacy:consumes:<key>`` tags whose queries are the
verbatim ``otag:`` unions from analytics/interactions/vocab.py, so a saved
archetype scores identically before and after this change (spec §3.2). The one
deliberate exception is ``produces:spells``, which scored 0 for every deck
(empty ``produce_otags``) and now maps to ``t:instant or t:sorcery``.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.server.domain.analytics.interactions.vocab import RESOURCES, RESOURCE_BY_KEY

TAGS_KEY = "role_tags"
TARGETS_KEY = "role_targets"
VERSION_KEY = "role_tags:version"
QUERY_INDEX_KEY = "tag_query_index"

TAG_MAX_RESULTS = 5000        # spec §2.6: above this a tag is oversized and excluded from scoring
MAX_QUERY_LEN = 400
MAX_TAGS = 200
MAX_TARGETS = 60
MAX_LABEL = 60
MAX_EXPLANATION = 500
REFRESH_SKIP_SECONDS = 20 * 3600  # spec §2.5: tags refreshed inside a 20h window are skipped

FACETS = ("IS", "TALKS", "MAKES", "AMPLIFIES", "FREE")
TARGET_KINDS = ("subtype", "type", "color", "land", "keyword", "concept", "enemy")
TAG_SOURCES = ("user", "template", "library", "migrated")

LIBRARY_PATH = os.path.join(os.path.dirname(__file__), "interactions", "role_library.json")

# Categories excluded from role scoring (spec OQ-5: mainboard + commander).
EXCLUDED_CATEGORIES = {"maybeboard", "considering", "sideboard"}


class RoleTagError(ValueError):
    """Invalid tag/target payload or unknown role tag (HTTP 400)."""


class RoleTagNotFound(KeyError):
    """No tag/target with that id (HTTP 404)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# -- query normalization + hashing (spec §2.4) -------------------------------------------------

_WS_RE = re.compile(r"\s+")


def normalize_query(query: Any) -> str:
    """Whitespace-collapsed, trimmed query text. Order is never changed (or/and are positional)."""
    if not isinstance(query, str):
        raise RoleTagError("query must be text.")
    q = _WS_RE.sub(" ", query).strip()
    if q.endswith(";"):
        q = q[:-1].strip()
    return q


def query_hash(query: str) -> str:
    """First 16 hex of sha256 of the normalized query; the tag_query_index key."""
    return hashlib.sha256(normalize_query(query).encode("utf-8")).hexdigest()[:16]


def slugify(name: str) -> str:
    # Colons survive: tag ids are namespaced ("bird:is", "legacy:produces:tokens").
    s = re.sub(r"[^a-z0-9:]+", "-", (name or "").lower().replace("+", "plus ")).strip("-")
    return s or "tag"


# -- storage ------------------------------------------------------------------------------------

_WRITE_LOCK = threading.RLock()


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        with _WRITE_LOCK:
            return fn(*a, **kw)
    return wrapper


# Process-local stand-in when no Redis handle is available (pure/test paths). Matches the
# tiny get/set/incr surface the registry uses.
class _MemStore:
    def __init__(self):
        self.store: Dict[str, str] = {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.store:
            return None
        self.store[k] = v
        return True

    def incr(self, k):
        self.store[k] = str(int(self.store.get(k, 0)) + 1)
        return int(self.store[k])


_MEM = _MemStore()


def _store(r):
    return r if r is not None else _MEM


def _bump(r) -> int:
    return int(_store(r).incr(VERSION_KEY))


def get_version(r) -> int:
    try:
        return int(_store(r).get(VERSION_KEY) or 0)
    except (TypeError, ValueError):
        return 0


def _load_raw(r, key) -> List[dict]:
    try:
        data = json.loads(_store(r).get(key) or "[]")
    except (TypeError, ValueError):
        return []
    return [t for t in data if isinstance(t, dict) and t.get("id")]


def load_tags(r) -> List[dict]:
    ensure_registry(r)
    return _load_raw(r, TAGS_KEY)


def load_targets(r) -> List[dict]:
    ensure_registry(r)
    return _load_raw(r, TARGETS_KEY)


def all_tags(r) -> List[dict]:
    """Every tag, sorted by (target, facet, label); the editor/validation index source."""
    order = {f: i for i, f in enumerate(FACETS)}
    return sorted(load_tags(r),
                  key=lambda t: ((t.get("target_id") or ""), order.get(t.get("facet") or "", 99),
                                 (t.get("label") or "").lower(), t["id"]))


def tag_index(r) -> Dict[str, dict]:
    return {t["id"]: t for t in load_tags(r)}


def target_index(r) -> Dict[str, dict]:
    return {t["id"]: t for t in load_targets(r)}


# -- legacy migration (spec §3.2) -----------------------------------------------------------------

def _otag_union(otags: Sequence[str]) -> str:
    return "(" + " or ".join(f"otag:{o}" for o in otags) + ")"


def legacy_tags() -> List[dict]:
    """The 30 migrated tags: one per (resource, side), query = the verbatim otag union.

    ``produces:spells`` has an empty produce_otags list (it scored 0 for every deck);
    it maps to ``t:instant or t:sorcery`` so the role starts working (spec §3.2).
    """
    out = []
    for res in RESOURCES:
        for side, otags, meaning in (
            ("produces", res.produce_otags, res.produce),
            ("consumes", res.consume_otags, res.consume),
        ):
            if side == "produces" and res.key == "spells":
                query = "t:instant or t:sorcery"
            elif not otags:
                query = ""  # never happens today; kept defensive — see spec §3.2
            else:
                query = _otag_union(otags)
            out.append({
                "id": f"legacy:{side}:{res.key}",
                "target_id": None,
                "facet": "FREE",
                "label": f"{res.label} ({side})",
                "query": query,
                "explanation": meaning,
                "source": "migrated",
                "created_at": None,
                "updated_at": None,
            })
    return out


# Resource keys that read as types, seeded as targets (spec §3.2).
_LEGACY_TARGET_KINDS = {"artifacts": "type", "enchantments": "type", "lands": "land"}


def _legacy_targets() -> List[dict]:
    out = []
    for res in RESOURCES:
        kind = _LEGACY_TARGET_KINDS.get(res.key)
        if kind:
            out.append({"id": res.key, "label": res.label, "kind": kind,
                        "signal": res.key.rstrip("s"), "updated_at": None})
    return out


_LEGACY_SEED_FLAG = "role_tags:legacy_seeded"


@_locked
def _ensure_legacy_tags(r) -> None:
    """Seed the 30 migrated tags once. Idempotent; never overwrites user edits (spec §3.2)."""
    store = _store(r)
    if store.get(_LEGACY_SEED_FLAG):
        return
    existing = {t["id"] for t in _load_raw(r, TAGS_KEY)}
    tags = _load_raw(r, TAGS_KEY) + [t for t in legacy_tags() if t["id"] not in existing]
    store.set(TAGS_KEY, json.dumps(tags, ensure_ascii=False))
    existing_targets = {t["id"] for t in _load_raw(r, TARGETS_KEY)}
    targets = _load_raw(r, TARGETS_KEY) + [t for t in _legacy_targets() if t["id"] not in existing_targets]
    store.set(TARGETS_KEY, json.dumps(targets, ensure_ascii=False))
    store.set(_LEGACY_SEED_FLAG, "1")
    _bump(r)


@_locked
def seed_library(r, path: Optional[str] = None) -> int:
    """Seed tags/targets from analytics/interactions/role_library.json when it exists.

    Idempotent: an id already in the registry (user-edited or previously seeded) is
    left untouched. Returns the number of new records added.
    """
    path = path or LIBRARY_PATH
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0
    store = _store(r)
    added = 0
    targets = _load_raw(r, TARGETS_KEY)
    have_targets = {t["id"] for t in targets}
    for raw in data.get("targets") or []:
        if isinstance(raw, dict) and raw.get("id") and raw["id"] not in have_targets:
            targets.append(raw)
            have_targets.add(raw["id"])
            added += 1
    tags = _load_raw(r, TAGS_KEY)
    have_tags = {t["id"] for t in tags}
    for raw in data.get("tags") or []:
        if isinstance(raw, dict) and raw.get("id") and raw["id"] not in have_tags:
            raw.setdefault("source", "library")
            tags.append(raw)
            have_tags.add(raw["id"])
            added += 1
    if added:
        store.set(TARGETS_KEY, json.dumps(targets, ensure_ascii=False))
        store.set(TAGS_KEY, json.dumps(tags, ensure_ascii=False))
        _bump(r)
    return added


def ensure_registry(r) -> None:
    """Lazy startup seed: legacy tags + optional role_library.json. Both steps are
    idempotent (flag-checked / file-checked), so running them on every read is cheap."""
    _ensure_legacy_tags(r)
    seed_library(r)


# -- validation + CRUD -------------------------------------------------------------------------

def _clean_label(value: Any, what: str = "label") -> str:
    if not isinstance(value, str) or not value.strip():
        raise RoleTagError(f"A tag needs a {what}.")
    label = " ".join(value.split())
    if len(label) > MAX_LABEL:
        raise RoleTagError(f"{what.capitalize()} is longer than {MAX_LABEL} characters.")
    return label


def _clean_query(value: Any) -> str:
    q = normalize_query(value)
    if not q:
        raise RoleTagError("A tag needs a Scryfall query.")
    if len(q) > MAX_QUERY_LEN:
        raise RoleTagError(f"Query is longer than {MAX_QUERY_LEN} characters; split the tag.")
    return q


def _unique_id(base: str, taken: Iterable[str]) -> str:
    taken, base = set(taken), slugify(base)
    tid, n = base, 2
    while tid in taken:
        tid, n = f"{base}-{n}", n + 1
    return tid


def create_target(r, body: Any) -> dict:
    if not isinstance(body, dict):
        raise RoleTagError("Body must be a JSON object.")
    label = _clean_label(body.get("label") or body.get("id") or "", "target label")
    kind = body.get("kind") if body.get("kind") in TARGET_KINDS else "concept"
    signal = body.get("signal")
    signal = " ".join(signal.split()).lower() if isinstance(signal, str) and signal.strip() else slugify(label)
    targets = load_targets(r)
    if len(targets) >= MAX_TARGETS:
        raise RoleTagError(f"At most {MAX_TARGETS} targets.")
    tid = _unique_id(body.get("id") or signal, (t["id"] for t in targets))
    target = {"id": tid, "label": label, "kind": kind, "signal": signal, "updated_at": _now()}
    targets.append(target)
    _store(r).set(TARGETS_KEY, json.dumps(targets, ensure_ascii=False))
    _bump(r)
    return target


def create_tag(r, body: Any, *, source: str = "user") -> dict:
    if not isinstance(body, dict):
        raise RoleTagError("Body must be a JSON object.")
    tags = load_tags(r)
    if len(tags) >= MAX_TAGS:
        raise RoleTagError(f"At most {MAX_TAGS} tags.")
    label = _clean_label(body.get("label"))
    query = _clean_query(body.get("query"))
    facet = body.get("facet")
    if facet not in FACETS:
        raise RoleTagError(f"facet must be one of {', '.join(FACETS)}.")
    target_id = body.get("target_id")
    if target_id is not None:
        if target_id not in target_index(r):
            raise RoleTagError(f"Unknown target {target_id!r}.")
    explanation = body.get("explanation") or ""
    if not isinstance(explanation, str):
        raise RoleTagError("explanation must be text.")
    if source not in TAG_SOURCES:
        source = "user"
    base = body.get("id") or (f"{target_id}:{facet.lower()}" if target_id else f"free:{slugify(label)}")
    tid = _unique_id(base, (t["id"] for t in tags))
    now = _now()
    tag = {"id": tid, "target_id": target_id, "facet": facet, "label": label,
           "query": query, "explanation": explanation.strip()[:MAX_EXPLANATION],
           "source": source, "created_at": now, "updated_at": now}
    tags.append(tag)
    _store(r).set(TAGS_KEY, json.dumps(tags, ensure_ascii=False))
    _bump(r)
    return tag


def update_tag(r, tag_id: str, body: Any) -> dict:
    if not isinstance(body, dict):
        raise RoleTagError("Body must be a JSON object.")
    tags = load_tags(r)
    for i, t in enumerate(tags):
        if t["id"] == tag_id:
            updated = dict(t)
            if "label" in body:
                updated["label"] = _clean_label(body["label"])
            if "query" in body:
                updated["query"] = _clean_query(body["query"])
            if "explanation" in body:
                if not isinstance(body["explanation"], str):
                    raise RoleTagError("explanation must be text.")
                updated["explanation"] = body["explanation"].strip()[:MAX_EXPLANATION]
            if "facet" in body:
                if body["facet"] not in FACETS:
                    raise RoleTagError(f"facet must be one of {', '.join(FACETS)}.")
                updated["facet"] = body["facet"]
            updated["updated_at"] = _now()
            tags[i] = updated
            _store(r).set(TAGS_KEY, json.dumps(tags, ensure_ascii=False))
            _bump(r)
            return updated
    raise RoleTagNotFound(tag_id)


def delete_tag(r, tag_id: str, *, in_use_by: Sequence[str] = ()) -> None:
    """Refuse to delete a tag still referenced by an archetype/genre role."""
    if in_use_by:
        names = ", ".join(sorted(in_use_by))
        raise RoleTagError(f"Tag {tag_id!r} is used by {names}; remove those roles first.")
    tags = load_tags(r)
    kept = [t for t in tags if t["id"] != tag_id]
    if len(kept) == len(tags):
        raise RoleTagNotFound(tag_id)
    _store(r).set(TAGS_KEY, json.dumps(kept, ensure_ascii=False))
    _bump(r)


# -- facet seed templates (spec §1.3 / §1.4) -----------------------------------------------------

def seed_query(target: Optional[Mapping], facet: str) -> str:
    """The pre-fill query for a (target, facet) pair. AMPLIFIES/FREE seed blank."""
    if target is None:
        return ""
    kind, signal = target.get("kind"), (target.get("signal") or "").strip()
    if not signal:
        return ""
    cap = signal[:1].upper() + signal[1:]
    if facet == "IS":
        if kind == "keyword":
            return f"keyword:{signal}"
        if kind == "color":
            return f"id:{signal}"
        return f"t:{signal}"
    if facet == "TALKS":
        if kind == "color":
            return f"o:/{signal}/"
        return f"o:{signal}" if kind != "keyword" else f'o:"{signal}"'
    if facet == "MAKES":
        return f"o:/creates? a [^.]*{cap} creature token/"
    return ""


# Starter queries in the FREE facet, keyed by target kind (spec §1.4: DESTROYS/WIPES are
# starters, not facets). Returned as (label, query) pairs for the editor to offer.
def starter_queries(target: Mapping) -> List[Tuple[str, str]]:
    kind, signal = target.get("kind"), (target.get("signal") or "").strip()
    if not signal:
        return []
    if kind in ("subtype", "enemy", "type"):
        return [
            (f"Destroys {signal}s", f"o:/destroy target [^.]*{signal}/"),
            (f"Wipes {signal}s", f"o:/destroy all [^.]*{signal}/"),
        ]
    if kind == "land":
        return [("Lands matter", "o:/lands? you control enter/")]
    return []


def target_payload(target: Mapping) -> dict:
    """Target record plus its facet seed queries and FREE-facet starters, for the editor."""
    return {**dict(target),
            "seeds": {facet: seed_query(target, facet) for facet in FACETS},
            "starters": [[label, q] for label, q in starter_queries(target)]}


# -- Scryfall search source (spec §2.1 / §2.3 / §2.6) --------------------------------------------
#
# A *source* is a callable ``source(query) -> {names, total, error, oversized}``:
#   names:     list of card names (None when error)
#   total:     Scryfall total_cards (len(names) when capped)
#   error:     None, or a message (400 bad syntax, network, 429 exhaustion)
#   oversized: True when total > TAG_MAX_RESULTS (tag is stored but excluded from scoring)

SearchSource = Callable[[str], dict]

SCRYFALL_SEARCH_URL = "https://api.scryfall.com/cards/search"
_PAGE_SLEEP_S = 0.075
_MAX_429_RETRIES = 3


def scryfall_search(query: str) -> dict:
    """Default source: page /cards/search with unique=cards; 404 = empty, 400 = syntax error."""
    import httpx  # local import: the module must import cleanly without network deps loaded

    names: List[str] = []
    total = 0
    url: Optional[str] = SCRYFALL_SEARCH_URL
    params: Optional[dict] = {"q": query, "unique": "cards"}
    retries = 0
    try:
        with httpx.Client(timeout=20.0) as client:
            while url:
                resp = client.get(url, params=params)
                if resp.status_code == 404:
                    return {"names": [], "total": 0, "error": None, "oversized": False}
                if resp.status_code == 429 and retries < _MAX_429_RETRIES:
                    retries += 1
                    try:
                        wait = float(resp.headers.get("Retry-After") or 2.0)
                    except ValueError:
                        wait = 2.0
                    time.sleep(min(max(wait, 1.0), 60.0))
                    continue
                if resp.status_code == 400:
                    detail = ""
                    try:
                        detail = resp.json().get("details") or ""
                    except ValueError:
                        pass
                    return {"names": None, "total": 0, "oversized": False,
                            "error": f"Scryfall rejected the query: {detail or 'bad syntax'}"}
                if resp.status_code != 200:
                    return {"names": None, "total": 0, "oversized": False,
                            "error": f"Scryfall returned HTTP {resp.status_code}."}
                data = resp.json()
                total = int(data.get("total_cards") or 0)
                for card in data.get("data") or []:
                    if card.get("name"):
                        names.append(card["name"])
                if total > TAG_MAX_RESULTS:
                    return {"names": None, "total": total, "oversized": True, "error": None}
                if not data.get("has_more"):
                    break
                url, params = data.get("next_page"), None
                time.sleep(_PAGE_SLEEP_S)
    except Exception as exc:  # noqa: BLE001 — network must never crash scoring
        return {"names": None, "total": 0, "oversized": False, "error": f"Scryfall unreachable: {exc}"}
    return {"names": names, "total": total or len(names), "error": None, "oversized": False}


# -- query index cache (spec §2.5) ----------------------------------------------------------------

def _load_index(r) -> Dict[str, dict]:
    try:
        data = json.loads(_store(r).get(QUERY_INDEX_KEY) or "{}")
    except (TypeError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def _save_index(r, index: Mapping[str, dict]) -> None:
    _store(r).set(QUERY_INDEX_KEY, json.dumps(index, ensure_ascii=False))


def query_index(r, query: str, source: Optional[SearchSource] = None, *, refresh: bool = False) -> dict:
    """Cached name-set for a query. Cold entries are evaluated once via the source, then
    persist with no TTL; ``refresh=True`` forces re-evaluation. Fresh entries (<20h) are
    returned as-is even with refresh (spec §2.5)."""
    h = query_hash(query)
    index = _load_index(r)
    entry = index.get(h)
    if entry is not None and refresh:
        built_at = entry.get("built_at") or ""
        try:
            age = time.time() - datetime.fromisoformat(built_at.replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            age = REFRESH_SKIP_SECONDS + 1
        if age < REFRESH_SKIP_SECONDS:
            return {**entry, "query_hash": h}
        entry = None
    if entry is None:
        result = (source or scryfall_search)(normalize_query(query))
        entry = {
            "names": result.get("names") if result.get("names") is not None else None,
            "total": int(result.get("total") or 0),
            "error": result.get("error"),
            "oversized": bool(result.get("oversized")),
            "built_at": _now(),
        }
        index[h] = entry
        _save_index(r, index)
    return {**entry, "query_hash": h}


# -- role resolution (spec §3.4) -------------------------------------------------------------------

_LEGACY_TAG_RE = re.compile(r"^\s*(produces?|consumes?)\s*[:/.\-\s]\s*(.+?)\s*$", re.I)


def _legacy_id(value: str) -> Optional[str]:
    m = _LEGACY_TAG_RE.match(value)
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
    return f"legacy:{side}:{key}"


def looks_like_query(value: str) -> bool:
    return bool(re.match(r"^\s*[a-z]+:", value)) and not value.startswith("legacy:")


def resolve_role_tag(raw: Any, index: Mapping[str, dict]) -> str:
    """Map a stored role tag to a registry tag id. Accepts tag ids verbatim and legacy
    ``produces:<key>``/``consumes:<key>`` aliases (deprecated); anything else is refused."""
    if not isinstance(raw, str) or not raw.strip():
        raise RoleTagError("A role needs a tag id.")
    tag = raw.strip()
    if tag in index:
        return tag
    legacy = _legacy_id(tag)
    if legacy and legacy in index:
        return legacy
    if looks_like_query(tag):
        raise RoleTagError(f"{tag!r} looks like a Scryfall query; create a tag first.")
    raise RoleTagError(f"Unknown role tag {raw!r}.")


# -- evaluation (spec §2.3 / §3.5) ------------------------------------------------------------------

def deck_cards_from_deck(deck: Mapping) -> List[dict]:
    """[{name, quantity}] for scoring: mainboard + commander, maybeboard/considering excluded."""
    out: Dict[str, int] = {}
    for c in (deck or {}).get("cards") or []:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        cat = str(c.get("category") or c.get("slot") or "").strip().lower()
        if cat in EXCLUDED_CATEGORIES:
            continue
        try:
            qty = max(int(c.get("quantity") or 1), 1)
        except (TypeError, ValueError):
            qty = 1
        out[c["name"]] = out.get(c["name"], 0) + qty
    return [{"name": n, "quantity": q} for n, q in sorted(out.items())]


def _quantities(deck_cards: Iterable[Mapping]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for c in deck_cards or []:
        if not isinstance(c, Mapping) or not c.get("name"):
            continue
        q = c.get("quantity", 1)
        qty = q if isinstance(q, int) and not isinstance(q, bool) and q > 0 else 1
        out[str(c["name"])] = out.get(str(c["name"]), 0) + qty
    return out


def evaluate_query(query: str, deck_cards: Iterable[Mapping], entry: Mapping) -> dict:
    """Intersect a query-index entry with deck cards. Pure."""
    qty = _quantities(deck_cards)
    if entry.get("error") or entry.get("oversized") or entry.get("names") is None:
        reason = entry.get("error") or (
            f"query matches {entry.get('total', 0)} cards (over the {TAG_MAX_RESULTS} cap)"
            if entry.get("oversized") else "query not evaluated")
        return {"have": 0, "cards": [], "matched": 0, "state": "blocked", "error": reason,
                "total": entry.get("total", 0), "built_at": entry.get("built_at")}
    names = set(entry.get("names") or [])
    cards = [{"name": n, "qty": q} for n, q in sorted(qty.items()) if n in names]
    have = sum(c["qty"] for c in cards)
    return {"have": have, "cards": cards, "matched": len(cards),
            "state": "ok" if have else "empty", "error": None,
            "total": entry.get("total", 0), "built_at": entry.get("built_at")}


def evaluate_tag(r, tag: Mapping, deck_cards: Iterable[Mapping],
                 source: Optional[SearchSource] = None) -> dict:
    """Full per-role result for one tag against a deck (spec §4.1/§4.3)."""
    entry = query_index(r, tag.get("query", ""), source)
    out = evaluate_query(tag.get("query", ""), deck_cards, entry)
    return {"tag": tag.get("id"), "label": tag.get("label") or tag.get("id"),
            "query": tag.get("query", ""), **out}


def evaluate_role(r, raw_tag: Any, deck_cards: Iterable[Mapping],
                  source: Optional[SearchSource] = None,
                  index: Optional[Mapping[str, dict]] = None) -> dict:
    """evaluate_tag with legacy-alias resolution; unknown tags score ``blocked``, never crash."""
    idx = index if index is not None else tag_index(r)
    try:
        tag_id = resolve_role_tag(raw_tag, idx)
    except RoleTagError as exc:
        return {"tag": str(raw_tag), "label": str(raw_tag), "query": "",
                "have": 0, "cards": [], "matched": 0, "state": "blocked", "error": str(exc),
                "total": 0, "built_at": None}
    return evaluate_tag(r, idx[tag_id], deck_cards, source)


def preview(r, query: str, deck_cards: Iterable[Mapping],
            source: Optional[SearchSource] = None) -> dict:
    """Editor preview: the same code path scoring uses (spec §5.1 step 4)."""
    try:
        q = _clean_query(query)
    except RoleTagError as exc:
        return {"error": str(exc), "have": 0, "cards": [], "matched": 0, "state": "blocked"}
    entry = query_index(r, q, source)
    return {"query": q, **evaluate_query(q, deck_cards, entry)}


def resolve_tag_sets(r, tags: Iterable[Mapping], source: Optional[SearchSource] = None) -> Dict[str, dict]:
    """Batch-resolve tag ids to name sets + metadata (one pass for pure callers like
    review_metrics that receive pre-resolved inputs instead of a Redis handle)."""
    out: Dict[str, dict] = {}
    for tag in tags:
        entry = query_index(r, tag.get("query", ""), source)
        out[tag["id"]] = {"names": set(entry.get("names") or []) if entry.get("names") is not None else None,
                          "error": entry.get("error"), "oversized": entry.get("oversized"),
                          "total": entry.get("total", 0), "built_at": entry.get("built_at"),
                          "query": tag.get("query", ""), "label": tag.get("label") or tag["id"]}
    return out
