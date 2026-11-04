"""Orchestration behind GET /api/v2/cards/search (docs/ui/card-search.md §5).

Modes (§5.2):

* ``any``       Scryfall page (cached), each hit annotated with ownership.
* ``owned``     Local Postgres only. Never asks Scryfall for results.
* ``not_owned`` Scryfall page, owned rows dropped. A short page stays short;
                ``total`` is hidden because Scryfall's count includes owned cards.

Cache policy is copied from ``domain/inventory/card_search.py``: an envelope
``{cached_at, results}`` per (normalized Scryfall query, upstream page);
non-empty results live 24h, empty results 60s, and a result produced by a
failure is NEVER cached. The envelope holds card FACTS only: ownership,
commander eligibility and pairing are recomputed on every request.

Scryfall failure -> the same filters run against the local ``cards`` mirror and
the response says ``degraded: true``. The endpoint never raises for upstream
trouble.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

import httpx

from src.server.domain.cards import commander_set
from src.server.domain.cards.search_query import (
    SearchFilters,
    build_scryfall_query,
    card_info,
    eligibility_row,
    facts_from_scryfall,
    map_page,
    pair_clause,
    pair_sql_hints,
    pairing_label,
    public_hit,
)
from src.server.domain.draft.deck_validator import check_partner_pair, partner_profile
from src.server.infra.db import card_search_repo as pg

SCRYFALL_SEARCH = "https://api.scryfall.com/cards/search"
TTL_NONEMPTY_SECONDS = 24 * 60 * 60
TTL_EMPTY_SECONDS = 60
SPACING_SECONDS = 0.1            # between upstream calls (Scryfall's 10 req/s guidance)
TIMEOUT_SECONDS = 8.0
BUILD_TIMEOUT_SECONDS = 4.0      # the eligible-set pull must not stall a request
_HEADERS = {"User-Agent": "commander-lab/card-search", "Accept": "application/json"}

_last_call = 0.0
_sleep = time.sleep


class UpstreamError(Exception):
    """Scryfall could not answer (transport error or non-200/404 status)."""


class PairNotAllowed(ValueError):
    """The first commander has no pairing ability."""


class FirstCardNotFound(LookupError):
    """``pair`` names an oracle id we cannot resolve."""


# ---------------------------------------------------------------------------
# upstream + cache
# ---------------------------------------------------------------------------

def _space() -> None:
    global _last_call
    wait = SPACING_SECONDS - (time.monotonic() - _last_call)
    if wait > 0:
        _sleep(wait)
    _last_call = time.monotonic()


def _get(params: dict, timeout: float) -> dict:
    """One /cards/search call. 404 is Scryfall's "no matches" and is a valid,
    empty answer; any other non-200 or transport error raises UpstreamError."""
    _space()
    try:
        resp = httpx.get(SCRYFALL_SEARCH, params=params, headers=_HEADERS, timeout=timeout)
    except Exception as e:
        raise UpstreamError(str(e)) from e
    if resp.status_code == 404:
        return {"data": [], "has_more": False, "total_cards": 0}
    if resp.status_code != 200:
        raise UpstreamError(f"scryfall status {resp.status_code}")
    try:
        return resp.json()
    except Exception as e:
        raise UpstreamError("scryfall returned unparsable JSON") from e


def _fetch_upstream(query: str, page: int) -> dict:
    body = _get({"q": query, "unique": "cards", "order": "name", "page": page}, TIMEOUT_SECONDS)
    facts = [facts_from_scryfall(c) for c in (body.get("data") or []) if isinstance(c, dict)]
    return {"results": facts, "has_more": bool(body.get("has_more")),
            "total": body.get("total_cards", len(facts))}


def _cache_key(query: str, page: int) -> str:
    return f"cards:search:{page}:{' '.join(query.lower().split())}"


def _read_cache(r, query: str, page: int) -> Optional[dict]:
    try:
        raw = r.get(_cache_key(query, page))
    except Exception:
        return None
    if not raw:
        return None
    try:
        env = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        results = env["results"]
        age = time.time() - float(env.get("cached_at", 0))
    except Exception:
        return None
    if not isinstance(results, list):
        return None
    if age >= (TTL_NONEMPTY_SECONDS if results else TTL_EMPTY_SECONDS):
        return None
    return env


def _write_cache(r, query: str, page: int, env: dict) -> None:
    ttl = TTL_NONEMPTY_SECONDS if env["results"] else TTL_EMPTY_SECONDS
    try:
        r.set(_cache_key(query, page),
              json.dumps({"cached_at": time.time(), **env}), ex=ttl)
    except Exception:
        pass


def _eligible_page_fetcher():
    def fetch(page: int):
        body = _get({"q": commander_set.ELIGIBLE_QUERY, "unique": "cards",
                     "order": "name", "page": page}, BUILD_TIMEOUT_SECONDS)
        return (body.get("data") or []), bool(body.get("has_more"))
    return fetch


# ---------------------------------------------------------------------------
# pairing
# ---------------------------------------------------------------------------

_OID = re.compile(r"^[0-9A-Za-z-]{1,64}$")


def _resolve_first_card(session, oracle_id: str) -> dict:
    facts = pg.get_card_facts(session, oracle_id) if _OID.match(oracle_id or "") else None
    if facts is None and _OID.match(oracle_id or ""):
        try:
            found = _fetch_upstream(f"oracleid:{oracle_id}", 1)["results"]
        except UpstreamError as e:
            raise FirstCardNotFound(f"card {oracle_id!r} is not local and Scryfall is unreachable") from e
        facts = found[0] if found else None
    if facts is None:
        raise FirstCardNotFound(f"no card with oracle_id {oracle_id!r}")
    return facts


def _legal_pairs(first_info, hits: list) -> list:
    return [h for h in hits if check_partner_pair(first_info, card_info(h)) is None]


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def _finish(hits, *, session, elig, owned_mode, pair_first_info, pair_oid):
    """Facts -> public hits: drop the first card / illegal pairs, join ownership
    (fresh every time), then apply not_owned."""
    if pair_first_info is not None:
        hits = [h for h in hits if h["oracle_id"] != pair_oid]
        hits = _legal_pairs(pair_first_info, hits)
    counts = pg.owned_counts(session, [h["oracle_id"] for h in hits])
    out = []
    for h in hits:
        owned, free = counts.get(h["oracle_id"], (0, 0))
        if owned_mode == "not_owned" and owned > 0:
            continue
        out.append(public_hit(
            h, owned_qty=owned, free_qty=free,
            can_be_commander=elig(h),
            pairing=pairing_label(partner_profile(card_info(h)))))
    return out


def search(session, r, f: SearchFilters, *, page: int, page_size: int) -> dict:
    pair_first_info = None
    clause = None
    if f.pair:
        first = _resolve_first_card(session, f.pair)
        pair_first_info = card_info(first)
        profile = partner_profile(pair_first_info)
        clause = pair_clause(profile, first["name"])
        if clause is None:
            raise PairNotAllowed("This commander can't have a partner")
    query = build_scryfall_query(f, clause)
    hints = pair_sql_hints(partner_profile(pair_first_info)) if pair_first_info else ()

    def response(results, *, has_more, total, source, degraded):
        return {"results": results, "page": page, "has_more": has_more, "total": total,
                "query": query, "source": source, "degraded": degraded}

    # -- owned: local Postgres only ------------------------------------------------
    if f.owned == "owned":
        return _local(session, r, f, query, page, page_size, pair_first_info, hints,
                      response, source="local", degraded=False, build_set=True)

    # -- any / not_owned: Scryfall, with a degraded local fallback -----------------
    upstream_page, offset = map_page(page, page_size)
    env = _read_cache(r, query, upstream_page)
    source = "cache"
    if env is None:
        try:
            env = _fetch_upstream(query, upstream_page)
        except UpstreamError:
            return _local(session, r, f, query, page, page_size, pair_first_info, hints,
                          response, source="local", degraded=True, build_set=False)
        _write_cache(r, query, upstream_page, env)      # successes only
        source = "scryfall"

    facts = env["results"]
    chunk = facts[offset:offset + page_size]
    has_more = offset + page_size < len(facts) or bool(env.get("has_more"))
    # With cmdr=1 the query itself was is:commander, so Scryfall already decided.
    e = (commander_set.AlwaysEligible() if f.cmdr else commander_set.Eligibility(
        r, commander_set.ensure(r, _eligible_page_fetcher())))
    elig = lambda h: e.check(eligibility_row(h))                       # noqa: E731
    results = _finish(chunk, session=session, elig=elig, owned_mode=f.owned,
                      pair_first_info=pair_first_info, pair_oid=f.pair)
    total = env.get("total") if (f.owned == "any" and not f.pair) else None
    return response(results, has_more=has_more, total=total, source=source,
                    degraded=e.used_fallback)


def _local(session, r, f, query, page, page_size, pair_first_info, hints, response, *,
           source, degraded, build_set):
    cands = pg.local_candidates(session, f, owned_mode=f.owned, pair_hints=hints)
    avail = commander_set.ensure(r, _eligible_page_fetcher(), build=build_set)
    e = commander_set.Eligibility(r, avail, bulk=len(cands) > 200)
    elig = lambda h: e.check(eligibility_row(h))                       # noqa: E731
    if f.cmdr:
        cands = [h for h in cands if elig(h)]
    if pair_first_info is not None:
        cands = [h for h in cands if h["oracle_id"] != f.pair]
        cands = _legal_pairs(pair_first_info, cands)
    start = (page - 1) * page_size
    chunk = cands[start:start + page_size]
    results = _finish(chunk, session=session, elig=elig, owned_mode="any",
                      pair_first_info=None, pair_oid=None)
    return response(results, has_more=start + page_size < len(cands),
                    total=None if f.pair else len(cands), source=source,
                    degraded=degraded or e.used_fallback)
