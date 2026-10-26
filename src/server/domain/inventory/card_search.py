"""Shared card-name autocomplete search (spec: docs/architecture/shared-card-autocomplete.md).

One framework-neutral search behind GET /api/cards/autocomplete, with
/api/brew/commanders kept as a thin alias over the commander scope.

Response contract (no Jinja coupling; SvelteKit-consumable):

    {"results": [{"name", "uid", "image", "type_line", "color_identity",
                  "colors", "keywords", "mana_cost"}],
     "source": "scryfall|cache|local", "degraded": bool}

Cache policy is the brew commander incident lesson: results live in a
{"cached_at", "results"} envelope at cards:autocomplete:{scope}:{q.lower()};
non-empty results are served for 24h, empty results for 60s, a result derived
from a Scryfall failure is NEVER cached, and legacy bare-list entries are
ignored. On a Scryfall error the search degrades to a substring match over
local card_meta:* records (legendary-only for the commander scope) and never
raises to the page.
"""
import asyncio
import json
import time

import httpx

SCRYFALL_SEARCH = "https://api.scryfall.com/cards/search"
SCRYFALL_AUTOCOMPLETE = "https://api.scryfall.com/cards/autocomplete"
SCRYFALL_NAMED = "https://api.scryfall.com/cards/named"
SCRYFALL_CATALOG_NAMES = "https://api.scryfall.com/catalog/card-names"
CATALOG_KEY = "cards:catalog:names"
CATALOG_TTL_SECONDS = 7 * 24 * 60 * 60

TTL_NONEMPTY_SECONDS = 24 * 60 * 60  # 24h
TTL_EMPTY_SECONDS = 60               # an empty result recovers fast

DEFAULT_LIMIT = 8
MAX_LIMIT = 20


def _headers(scope):
    return {"User-Agent": f"commander-lab/{scope}", "Accept": "application/json"}


def _cache_key(scope, query):
    return f"cards:autocomplete:{scope}:{query.lower()}"


def _read_cache(r, scope, query):
    """Return cached results for the query, or None.

    Only {"cached_at", "results"} envelopes within their TTL are honored;
    legacy bare-list entries (and anything unparsable) are ignored so stale
    empty results cannot survive a rollout.
    """
    try:
        cached = r.get(_cache_key(scope, query))
    except Exception:
        return None
    if not cached:
        return None
    try:
        entry = json.loads(cached.decode() if isinstance(cached, bytes) else cached)
    except Exception:
        return None
    if not isinstance(entry, dict):
        return None
    results = entry.get("results")
    if not isinstance(results, list):
        return None
    try:
        age = time.time() - float(entry.get("cached_at", 0))
    except Exception:
        return None
    ttl = TTL_NONEMPTY_SECONDS if results else TTL_EMPTY_SECONDS
    if age >= ttl:
        return None
    return results


def _write_cache(r, scope, query, results):
    """Cache a successful result. Failure-derived (degraded) results are
    never passed here by the callers."""
    try:
        r.set(_cache_key(scope, query),
              json.dumps({"cached_at": time.time(), "results": results}))
    except Exception:
        pass


def _clamp_limit(limit):
    if limit is None:
        return None
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(n, MAX_LIMIT))


def _shape_card(c):
    """Project a Scryfall card object onto the contract shape."""
    image = ""
    iu = c.get("image_uris") or {}
    if iu:
        image = iu.get("art_crop") or iu.get("small") or ""
    elif c.get("card_faces"):
        image = ((c["card_faces"][0].get("image_uris") or {}).get("small")) or ""
    return {
        "name": c.get("name", ""), "uid": c.get("id", ""), "image": image,
        "colors": c.get("colors") or [], "color_identity": c.get("color_identity") or [],
        "type_line": c.get("type_line", ""), "keywords": c.get("keywords") or [],
        "mana_cost": c.get("mana_cost", ""),
    }


def _local_meta(r, name):
    try:
        raw = r.get(f"card_meta:{name}")
        if raw:
            return json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    except Exception:
        pass
    return {}


def _shape_local(name, meta):
    color = meta.get("color") or ""
    colors = [c for c in color if c in "WUBRGC"]
    return {
        "name": name, "uid": "", "image": "",
        "type_line": meta.get("type") or "",
        "color_identity": colors, "colors": colors,
        "keywords": meta.get("keywords") or [], "mana_cost": "",
    }


def _local_matches(r, query, limit, legendary_only=False):
    """Degraded path: case-insensitive substring match over local card_meta:*
    names (legendary-only when the caller wants commanders). Best-effort —
    any storage error just means fewer matches, never an exception."""
    ql = query.casefold()
    out = []
    try:
        keys = r.keys("card_meta:*")
    except Exception:
        return out
    for key in sorted(keys):
        if isinstance(key, bytes):
            key = key.decode()
        name = key[len("card_meta:"):]
        if ql not in name.casefold():
            continue
        meta = _local_meta(r, name)
        if legendary_only and "legendary" not in (meta.get("type") or "").casefold():
            continue
        out.append(_shape_local(name, meta))
        if limit is not None and len(out) >= limit:
            break
    return out


async def search_commanders(r, query, limit=None):
    """Scryfall-backed, ranked commander search with a short-lived cache.

    Search by rules text (including card types), then use Scryfall fuzzy-name
    search as a typo-tolerant fallback. Paginate results, but bound work per
    request so a broad type query cannot monopolize the app.

    This is the /api/brew/commanders logic extracted verbatim and
    parameterized by scope; the cache envelope + degraded fallback follow the
    shared autocomplete policy.
    """
    scope = "commanders"
    limit = _clamp_limit(limit)

    cached = _read_cache(r, scope, query)
    if cached is not None:
        results = cached[:limit] if limit is not None else cached
        return {"results": results, "source": "cache", "degraded": False}

    headers = _headers(scope)
    candidates = {}
    failed = False
    params = {"q": f"{query} is:commander legal:commander", "unique": "cards", "order": "name"}
    url = SCRYFALL_SEARCH
    fetched = 0
    pages_fetched = 0
    page_urls = set()
    try:
        while url and fetched < 40 and pages_fetched < 20 and url not in page_urls:
            page_urls.add(url)
            pages_fetched += 1
            resp = httpx.get(url, params=params, headers=headers, timeout=8.0)
            params = None
            if resp.status_code != 200:
                break
            payload = resp.json()
            for card in payload.get("data") or []:
                cid = card.get("id") or card.get("name")
                if cid:
                    candidates[cid] = card
                    fetched += 1
                    if fetched >= 40:
                        break
            url = payload.get("next_page") if payload.get("has_more") and fetched < 40 else None
        if url:
            params = None  # Scryfall next_page is a complete URL

        if not candidates:
            import difflib
            import re

            # Scryfall returns 404 for an unrecognized query/name. Treat that as
            # an empty attempt, then recover candidates by progressive prefix
            # shortening: name:"..." search is substring-based, so a
            # right-truncated query finds the intended card whenever the typo
            # sits at or after the truncation point. Short prefixes return
            # broad pools, so every candidate is re-ranked by edit similarity
            # against the user's ORIGINAL spelling below (no per-name special
            # cases; the mechanism is fully general).
            fuzzy = httpx.get(
                SCRYFALL_NAMED,
                params={"fuzzy": query}, headers=headers, timeout=8.0,
            )
            direct_fuzzy_ids = []
            if fuzzy.status_code == 200:
                card = fuzzy.json()
                if (card.get("legalities") or {}).get("commander") == "legal":
                    cid = card.get("id") or card.get("name")
                    candidates[cid] = card
                    # Preserve existing behavior for a successful fuzzy-name
                    # match; only gather broader candidates after a miss.
                    direct_fuzzy_ids.append(cid)

            if not candidates and len(query) >= 4:
                prefix = query
                while len(prefix) > 3:
                    prefix = prefix[:-1]
                    await asyncio.sleep(0.1)  # stay under Scryfall rate limits
                    retry = httpx.get(
                        SCRYFALL_SEARCH,
                        params={"q": f'name:"{prefix}" is:commander legal:commander',
                                "unique": "cards", "order": "name"},
                        headers=headers, timeout=8.0,
                    )
                    if retry.status_code == 404:
                        continue
                    if retry.status_code != 200:
                        break
                    for card in (retry.json().get("data") or []):
                        if (card.get("legalities") or {}).get("commander") == "legal":
                            candidates[card.get("id") or card.get("name")] = card
                    if candidates:
                        break

            # Last resort, same recovery the default scope uses: rank Scryfall's
            # card-name catalog by per-word similarity ("buscuit" -> Biscuit...),
            # then keep only the commander-legal ones via an exact-name lookup.
            if not candidates:
                fuzzy_names = _fuzzy_names(r, query, 8, headers)
                if fuzzy_names:
                    clauses = " or ".join(
                        '!"%s"' % n.replace("\\", "").replace('"', "") for n in fuzzy_names)
                    rescue = httpx.get(
                        SCRYFALL_SEARCH,
                        params={"q": f"({clauses}) is:commander legal:commander",
                                "unique": "cards", "order": "name"},
                        headers=headers, timeout=8.0,
                    )
                    if rescue.status_code == 200:
                        for card in (rescue.json().get("data") or []):
                            candidates[card.get("id") or card.get("name")] = card

            # A successful direct fuzzy-name match preserves pre-existing
            # behavior exactly: return it without any similarity gate, so a
            # short query against a long card name (e.g. "atraxa" -> "Atraxa,
            # Praetors' Voice") is never discarded.
            direct_fuzzy_hit = any(
                cid in candidates for cid in direct_fuzzy_ids
            ) if direct_fuzzy_ids else False

            if candidates:
                original = query.casefold()

                def similarity(name):
                    # Best alignment of the query against the card name: score
                    # the whole name AND each individual word, so the match is
                    # found wherever it occurs (e.g. "gitrg" aligns with
                    # "gitrog" inside "The Gitrog Monster") instead of only
                    # against the name's leading characters.
                    name_cf = (name or "").casefold()
                    best = difflib.SequenceMatcher(None, original, name_cf).ratio()
                    for word in re.split(r"[^a-z0-9]+", name_cf):
                        if word:
                            ratio = difflib.SequenceMatcher(None, original, word).ratio()
                            if ratio > best:
                                best = ratio
                    return best

                ranked = sorted(
                    candidates.items(),
                    key=lambda item: similarity(item[1].get("name")),
                    reverse=True,
                )
                if direct_fuzzy_hit:
                    candidates = dict(ranked[:5])
                else:
                    best_score = similarity(ranked[0][1].get("name"))
                    # Anti-flooding gate, driven by the data rather than fixed
                    # cases: an absolute floor discards pools whose best match
                    # is only loosely similar to the query (gibberish), and a
                    # relative band below the best score keeps only names in
                    # the same family so broad prefix pools cannot leak
                    # near-misses (e.g. "kenith" keeps Kenrith, drops Kenessos).
                    if best_score >= 0.7:
                        floor = max(0.7, best_score - 0.15)
                        candidates = dict(
                            item for item in ranked[:5]
                            if similarity(item[1].get("name")) >= floor
                        )
                    else:
                        candidates = {}
    except Exception:
        candidates = {}
        failed = True

    if failed:
        # Degraded path: never cache a failure-derived result, never raise.
        return {
            "results": _local_matches(r, query, limit, legendary_only=True),
            "source": "local", "degraded": True,
        }

    def relevant(card):
        text = f"{card.get('name', '')} {card.get('type_line', '')}".casefold()
        ql = query.casefold()
        if ql in text:
            return 0
        words = ql.split()
        if words and all(word in text for word in words):
            return 1
        return 2

    results = []
    for c in sorted(candidates.values(), key=lambda card: (relevant(card), (card.get("name") or "").casefold())):
        results.append(_shape_card(c))

    _write_cache(r, scope, query, results)
    if limit is not None:
        results = results[:limit]
    return {"results": results, "source": "scryfall", "degraded": False}


def _enrich_from_scryfall(names, headers):
    """Fetch full Scryfall card objects for exact names in ONE request (the
    same card objects the commander scope shapes). Returns {casefolded name
    or front-face name: card}. Raises on any HTTP/parse error so the caller
    can skip caching a thinly-enriched result."""
    if not names:
        return {}
    clauses = ['!"%s"' % n.replace("\\", "").replace('"', "") for n in names]
    resp = httpx.get(
        SCRYFALL_SEARCH,
        params={"q": " or ".join(clauses), "unique": "cards", "order": "name"},
        headers=headers, timeout=8.0,
    )
    if resp.status_code != 200:
        raise ValueError(f"scryfall enrichment status {resp.status_code}")
    by_name = {}
    for card in resp.json().get("data") or []:
        if not isinstance(card, dict):
            continue
        full = (card.get("name") or "").casefold()
        if full:
            by_name.setdefault(full, card)
            by_name.setdefault(full.split(" // ")[0], card)
    return by_name


def _catalog_names(r, headers):
    """Scryfall's card-name catalog (~35k names), cached 7 days in Redis.
    Returns [] on any failure and never caches a failure."""
    try:
        raw = r.get(CATALOG_KEY)
        if raw:
            names = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
            if isinstance(names, list) and names:
                return names
    except Exception:
        pass
    try:
        resp = httpx.get(SCRYFALL_CATALOG_NAMES, headers=headers, timeout=15.0)
        if resp.status_code != 200:
            return []
        names = [n for n in (resp.json().get("data") or []) if isinstance(n, str)]
    except Exception:
        return []
    if names:
        try:
            r.set(CATALOG_KEY, json.dumps(names), ex=CATALOG_TTL_SECONDS)
        except TypeError:
            r.set(CATALOG_KEY, json.dumps(names))
        except Exception:
            pass
    return names


def _fuzzy_names(r, query, limit, headers):
    """Typo recovery for the default scope: when Scryfall's prefix autocomplete
    finds nothing, rank catalog names by per-word edit similarity to the
    query (\"spidr\" -> Spider cards, \"atraxxa\" -> Atraxa). Gibberish stays
    empty: a word must reach 0.75 similarity to count."""
    import difflib
    import re

    names = _catalog_names(r, headers)
    if not names:
        return []
    words = {}
    for n in names:
        for w in re.split(r"[^a-z0-9']+", n.casefold()):
            if len(w) >= 3:
                words.setdefault(w, []).append(n)
    tokens = [t for t in re.split(r"[^a-z0-9']+", query.casefold()) if t]
    if not tokens:
        return []
    vocab = list(words)
    scores = {}
    for tok in tokens:
        for w in difflib.get_close_matches(tok, vocab, n=10, cutoff=0.75):
            ratio = difflib.SequenceMatcher(None, tok, w).ratio()
            for n in words[w]:
                scores[n] = scores.get(n, 0.0) + ratio
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))
    return [n for n, _ in ranked[:limit]]


async def search_cards(r, query, limit=DEFAULT_LIMIT):
    """Default (any-card) autocomplete: Scryfall GET /cards/autocomplete for
    names, then ONE Scryfall card lookup so every row carries the full contract
    (uid/image/type_line/colors/...) -- the same shaping the commander scope
    uses. A name Scryfall does not return falls back to local card_meta; if the
    lookup itself fails the rows are locally enriched and NOT cached.
    Degrades to a pure local substring match on autocomplete error."""
    scope = "cards"
    limit = _clamp_limit(limit)

    cached = _read_cache(r, scope, query)
    if cached is not None:
        return {"results": cached[:limit], "source": "cache", "degraded": False}

    headers = _headers(scope)
    try:
        resp = httpx.get(
            SCRYFALL_AUTOCOMPLETE,
            params={"q": query}, headers=headers, timeout=8.0,
        )
        if resp.status_code != 200:
            raise ValueError(f"scryfall autocomplete status {resp.status_code}")
        names = (resp.json().get("data") or [])[:limit]
        if not names:
            names = _fuzzy_names(r, query, limit, headers)
    except Exception:
        # Degraded path: never cache a failure-derived result, never raise.
        return {
            "results": _local_matches(r, query, limit),
            "source": "local", "degraded": True,
        }

    enriched = True
    try:
        cards = _enrich_from_scryfall(names, headers)
    except Exception:
        cards, enriched = {}, False

    results = []
    for name in names:
        card = cards.get(name.casefold())
        if card:
            row = _shape_card(card)
            row["name"] = name
            results.append(row)
        else:
            results.append(_shape_local(name, _local_meta(r, name)))

    if enriched:
        _write_cache(r, scope, query, results)
    return {"results": results, "source": "scryfall", "degraded": False}


async def autocomplete(r, q, limit=DEFAULT_LIMIT, commanders_only=False):
    """The one entry point behind GET /api/cards/autocomplete (and the
    /api/brew/commanders alias). Never raises for upstream/storage problems.

    limit=None means "no slicing" (the brew alias preserves its historical
    full-ranked-list behavior).
    """
    query = (q or "").strip()
    if len(query) < 2:
        return {"results": [], "source": "local", "degraded": False}
    if commanders_only:
        return await search_commanders(r, query, limit=limit)
    return await search_cards(r, query, limit=limit if limit is not None else MAX_LIMIT)
