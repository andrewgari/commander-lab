"""Temporary EDHREC data for one Brew build (stage 1, "skeleton").

A build downloads the commander's EDHREC page(s) once and keeps a compact, merged pool
({name: inclusion in the commander's decks, inclusion in the chosen archetype's decks, synergy,
EDHREC card-list tags}) that stages 2 and 3 read instead of hitting EDHREC again.

Lifecycle
  * Fetch: `fetch_pool` reads the commander page and, per theme the user asked for, the matching
    EDHREC archetype ("theme") page, through `card_eval.edhrec_data.get_page`
    (7-day shared cache, throttling/backoff, negative caching of 404s).
  * Temp copy: the merged pool is stored at `brew:edhrec_pool:{session_id}` with a TTL
    (`POOL_TTL_SECONDS`) so an abandoned/crashed build cannot leak it, and `discard_pool` removes it
    when the build ends (success or failure).
  * Partners / backgrounds: all commanders go into one slug (`commander_slug` joins them with `-`),
    exactly as EDHREC's own URLs do.
  * Archetype: the first of the session's themes that EDHREC has a page for under this commander.
    EDHREC themes are free-form slugs (counters, tokens, +1-1-counters ...); a theme EDHREC does not
    know is skipped and reported, never fatal.
  * Degradation: with EDHREC down the pool is `{"cards": {}, "degraded": "<why>"}`; the build carries
    on from the other pools (the gate still refills) and says so in its stage notes.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from src.server.domain.analytics.card_eval import edhrec_data
from src.server.domain.analytics.edhrec_inclusion import name_key
from src.server.domain.analytics.providers.edhrec import EDHRecError, slugify_card_name

log = logging.getLogger(__name__)

POOL_PREFIX = "brew:edhrec_pool:"
POOL_TTL_SECONDS = 6 * 3600
# Cards on at least this share of decks, in BOTH the commander's and the archetype's decks, are core.
CORE_INCLUSION = 0.40


def _slug_variants(theme: str) -> List[str]:
    t = slugify_card_name(theme)
    return [t] if t else []


def _page(r: Any, commanders: Sequence[str], client: Any, theme: Optional[str]) -> Optional[dict]:
    try:
        return edhrec_data.get_page(r, commanders, client=client, theme=theme)
    except edhrec_data.CommanderPageMissing:
        return None


def fetch_pool(r: Any, commanders: Sequence[str], themes: Sequence[str] = (), *, client: Any = None) -> dict:
    """The merged EDHREC pool for `commanders` (+ the first matching archetype of `themes`).

    {"commanders": [...], "theme": str|None, "themes_skipped": [...], "decks_sampled": int,
     "theme_decks_sampled": int, "stale": bool, "degraded": str|None,
     "cards": {name_key: {"name", "inclusion", "theme_inclusion", "synergy", "tags"}}}
    Never raises: any EDHREC failure is reported in `degraded`.
    """
    out: Dict[str, Any] = {"commanders": list(commanders), "theme": None, "themes_skipped": [],
                           "decks_sampled": 0, "theme_decks_sampled": 0, "stale": False,
                           "degraded": None, "cards": {}}
    try:
        base = _page(r, commanders, client, None)
    except EDHRecError as exc:
        out["degraded"] = f"EDHREC unavailable ({str(exc)[:120]})"
        return out
    except Exception as exc:  # noqa: BLE001 - EDHREC is context, never fatal
        log.warning("brew edhrec pool: %s", exc)
        out["degraded"] = f"EDHREC unavailable ({str(exc)[:120]})"
        return out
    if base is None:
        out["degraded"] = "EDHREC has no page for this commander"
        return out
    out["decks_sampled"] = int(base.get("decks_sampled") or 0)
    out["stale"] = bool(base.get("stale"))
    for k, v in (base.get("cards") or {}).items():
        if not v.get("name"):  # legacy cache entries without a display name cannot be added to a deck
            continue
        out["cards"][k] = {"name": v["name"], "inclusion": v.get("inclusion") or 0.0, "theme_inclusion": None,
                           "synergy": v.get("synergy"), "tags": list(v.get("tags") or [])}

    for theme in themes:
        if not str(theme or "").strip():
            continue
        try:
            tp = _page(r, commanders, client, theme)
        except EDHRecError:
            out["themes_skipped"].append(theme)
            continue
        if tp is None:
            out["themes_skipped"].append(theme)
            continue
        out["theme"] = theme
        out["theme_decks_sampled"] = int(tp.get("decks_sampled") or 0)
        for k, v in (tp.get("cards") or {}).items():
            if not v.get("name"):
                continue
            cur = out["cards"].get(k)
            if cur is None:
                out["cards"][k] = {"name": v["name"], "inclusion": 0.0, "theme_inclusion": v.get("inclusion") or 0.0,
                                   "synergy": v.get("synergy"), "tags": list(v.get("tags") or [])}
            else:
                cur["theme_inclusion"] = v.get("inclusion") or 0.0
                if v.get("synergy") is not None and (cur["synergy"] is None or v["synergy"] > cur["synergy"]):
                    cur["synergy"] = v["synergy"]
                for t in v.get("tags") or []:
                    if t not in cur["tags"]:
                        cur["tags"].append(t)
        break  # one archetype per build
    return out


def store_pool(r: Any, session_id: str, pool: dict) -> None:
    """Persist the temp copy (TTL'd). Best effort: the build keeps its in-memory copy regardless."""
    if r is None or not session_id:
        return
    try:
        try:
            r.set(POOL_PREFIX + session_id, json.dumps(pool), ex=POOL_TTL_SECONDS)
        except TypeError:  # minimal clients without TTL support: the end-of-build discard still cleans up
            r.set(POOL_PREFIX + session_id, json.dumps(pool))
    except Exception as exc:  # noqa: BLE001
        log.warning("brew edhrec pool: could not store temp copy (%s)", exc)


def load_pool(r: Any, session_id: str) -> Optional[dict]:
    try:
        raw = r.get(POOL_PREFIX + session_id) if r is not None and session_id else None
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


def discard_pool(r: Any, session_id: str) -> None:
    try:
        if r is not None and session_id:
            r.delete(POOL_PREFIX + session_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("brew edhrec pool: could not discard temp copy (%s)", exc)


def core_cards(pool: dict, threshold: float = CORE_INCLUSION) -> List[dict]:
    """Cards at/above `threshold` for the commander AND (when an archetype was found) the archetype,
    most-included first."""
    has_theme = bool(pool.get("theme"))
    out = []
    for v in (pool.get("cards") or {}).values():
        if (v.get("inclusion") or 0.0) < threshold:
            continue
        if has_theme and (v.get("theme_inclusion") or 0.0) < threshold:
            continue
        out.append(v)
    out.sort(key=lambda v: (-(v.get("inclusion") or 0.0), -(v.get("theme_inclusion") or 0.0), v["name"].casefold()))
    return out


def by_tag(pool: dict, *tags: str) -> List[dict]:
    """Pool cards carrying any of the EDHREC card-list `tags`, best first (synergy, then inclusion)."""
    want = {t.lower() for t in tags}
    hits = [v for v in (pool.get("cards") or {}).values() if want & {t.lower() for t in v.get("tags") or []}]
    hits.sort(key=lambda v: (-(v.get("synergy") or 0.0), -(v.get("inclusion") or 0.0), v["name"].casefold()))
    return hits


__all__ = ["fetch_pool", "store_pool", "load_pool", "discard_pool", "core_cards", "by_tag", "name_key",
           "CORE_INCLUSION", "POOL_PREFIX", "POOL_TTL_SECONDS"]
