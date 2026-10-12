"""
AN-3 deck insights: the I/O layer over composition, identity, pillar fit,
EDHREC inclusion and weak performers. Everything here reads Redis caches;
only `generate` (explicit) and the rebuild hook call the LLM.

Card text comes from the AN-1 tag cache (card_interactions:{name}.card:
type_line / oracle_text / mana_cost), falling back to card_meta:{name}.

Redis keys added by AN-3:
    composition:template:{registry_id}   per-deck template override (JSON)
    identity:deck:{registry_id}          thesis + pillars (identity.py)
    embeddings:card:{model_id}           hash: sha1(card text)[:16] -> base64 float32
    edhrec:commander:{slug}              parsed EDHREC commander page (7-day freshness)
    journal:deck:{registry_id}           read only, for pet_cards (written by the Journal card, AN-5)
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Callable, Dict, List, Optional

from . import composition as comp
from . import edhrec_inclusion
from . import embeddings
from . import identity
from . import pillar_fit
from . import weak_performers as weak

log = logging.getLogger("commander_lab.insights")

JOURNAL_PREFIX = "journal:deck:"


def _registry_id(deck: dict) -> str:
    if deck.get("registry_id"):
        return deck["registry_id"]
    return f"{deck.get('source', 'archidekt')}:{deck.get('source_id', deck.get('id'))}"


def card_texts(r, deck: dict) -> Dict[str, dict]:
    names = identity.deck_names(deck)
    if not names:
        return {}
    mget = r.mget if hasattr(r, "mget") else (lambda ks: [r.get(k) for k in ks])
    out: Dict[str, dict] = {}
    for n, raw in zip(names, mget([f"card_interactions:{n}" for n in names])):
        if not raw:
            continue
        try:
            card = (json.loads(raw) or {}).get("card")
        except json.JSONDecodeError:
            continue
        if card and (card.get("type_line") or card.get("oracle_text")):
            out[n] = card
    for n in names:
        if n in out:
            continue
        raw = r.get(f"card_meta:{n}")
        if not raw:
            continue
        try:
            m = json.loads(raw)
        except json.JSONDecodeError:
            continue
        out[n] = {"type_line": " ".join((m.get("super_types") or []) + [m.get("type") or ""]).strip(),
                  "oracle_text": m.get("oracle_text") or "", "mana_cost": ""}
    return out


def pet_cards(r, deck: dict) -> List[str]:
    raw = r.get(JOURNAL_PREFIX + _registry_id(deck))
    if not raw:
        return []
    try:
        return [p for p in (json.loads(raw) or {}).get("pet_cards") or [] if isinstance(p, str)]
    except (json.JSONDecodeError, AttributeError):
        return []


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def deck_composition(r, deck: dict, texts: Optional[Dict[str, dict]] = None) -> dict:
    texts = texts if texts is not None else card_texts(r, deck)
    out = comp.compute_composition(deck, texts, template=comp.get_template(r, deck))
    out["deck"] = {"registry_id": _registry_id(deck), "name": deck.get("name"),
                   "commanders": deck.get("commanders") or []}
    return out


def deck_fit(r, deck: dict, ident: Optional[dict] = None, texts: Optional[Dict[str, dict]] = None,
             embedder: Optional[embeddings.Embedder] = None) -> dict:
    texts = texts if texts is not None else card_texts(r, deck)
    ident = ident if ident is not None else identity.load_identity(r, deck)
    emb = embedder or embeddings.default_embedder()
    t0 = time.monotonic()
    out = pillar_fit.deck_fit(r, deck, ident, texts, emb)
    out["elapsed_ms"] = round(1000 * (time.monotonic() - t0))
    return out


def identity_view(r, deck: dict, include_cards: bool = True) -> dict:
    """GET /identity payload (UX_SPEC §6): thesis, pillars (with count +
    top_cards), rejected, fit {pillar, glue, generic, per_card}, proposal."""
    ident = identity.load_identity(r, deck)
    texts = card_texts(r, deck)
    fit = pillar_fit_safe(r, deck, ident, texts)
    by_id = {p["id"]: p for p in fit.get("pillars") or []}
    pillars = []
    for p in sorted((ident or {}).get("pillars") or [], key=lambda x: (x.get("state") == "rejected",
                                                                       x.get("order") or 0)):
        stats = by_id.get(p["id"], {})
        pillars.append({**p, "count": stats.get("count", 0), "top_cards": stats.get("top_cards", [])})
    per_card = fit.get("per_card") or []
    return {
        "deck": {"registry_id": _registry_id(deck), "name": deck.get("name"),
                 "commanders": deck.get("commanders") or []},
        "thesis": (ident or {}).get("thesis"),
        "pillars": [p for p in pillars if p.get("state") != "rejected"],
        "rejected": [p for p in pillars if p.get("state") == "rejected"],
        "generator": (ident or {}).get("generator"),
        "proposal": (ident or {}).get("proposal"),
        "stale": bool(ident and ident.get("cards_sig") and ident["cards_sig"] != identity.cards_signature(deck)),
        "fit": {k: fit.get(k) for k in ("status", "model", "pillar", "glue", "generic", "on_plan_pct",
                                        "total_cards", "thresholds", "elapsed_ms")}
               | {"per_card": per_card if include_cards else []},
    }


def pillar_fit_safe(r, deck: dict, ident: Optional[dict], texts: Dict[str, dict]) -> dict:
    try:
        return deck_fit(r, deck, ident, texts)
    except Exception as exc:  # noqa: BLE001 - a broken model must not take the page down
        log.exception("pillar fit failed")
        return {"status": "error", "error": str(exc)[:200], "per_card": [], "pillars": []}


def deck_edhrec(r, deck: dict, refresh: bool = False, texts: Optional[Dict[str, dict]] = None) -> dict:
    texts = texts if texts is not None else card_texts(r, deck)
    return edhrec_inclusion.deck_inclusion(r, deck, card_texts=texts, refresh=refresh)


def deck_weak_performers(r, deck: dict, graph: dict, limit: Optional[int] = None) -> dict:
    texts = card_texts(r, deck)
    ident = identity.load_identity(r, deck)
    fit = pillar_fit_safe(r, deck, ident, texts)
    inc = deck_edhrec(r, deck, texts=texts)
    out = weak.compute_weak_performers(deck, graph, fit, inc, pets=pet_cards(r, deck), card_texts=texts,
                                       limit=limit)
    out["inputs"].update({"fit_status": fit.get("status"), "edhrec_status": inc.get("status"),
                          "edhrec_stale": inc.get("stale"), "pets": len(pet_cards(r, deck))})
    return out


# ---------------------------------------------------------------------------
# LLM (explicit generate + rebuild hook)
# ---------------------------------------------------------------------------


def _engine_names(r, deck: dict) -> List[str]:
    try:
        from .interactions import service as interactions_service

        eng = interactions_service.get_deck_engines(r, deck)
        return [f"{e.get('name')} ({e.get('summary') or ''})".strip() for e in eng.get("engines") or []][:8]
    except Exception:  # noqa: BLE001 - engines are context only
        return []


def generate(r, deck: dict, chain=None, on_call: Optional[Callable[[dict], None]] = None) -> dict:
    from .interactions.llm import default_chain

    chain = chain if chain is not None else default_chain()
    calls: List[dict] = []

    def _log(c):
        calls.append(c)
        if on_call:
            on_call(c)

    emb = embeddings.default_embedder()
    out = identity.generate_identity(r, deck, chain, card_texts(r, deck), embedder=emb if emb.available() else None,
                                     on_call=_log, engines=_engine_names(r, deck))
    out["llm_calls"] = calls
    _record_generate_run(r, deck, out, calls)
    return out


def _record_generate_run(r, deck: dict, out: dict, calls: List[dict]) -> None:
    """Explicit generations land in the same run log as rebuilds (interactions:runs)."""
    try:
        from .interactions import service as interactions_service

        run = interactions_service.RunLog("identity")
        for c in calls:
            run.add_llm_call(c)
        run.status = "ok" if out.get("status") in ("generated", "proposed") else out.get("status", "error")
        run.extra["identity"] = {k: out.get(k) for k in ("status", "deck", "added", "dropped", "provider")}
        interactions_service._record_run(r, run.to_dict())
    except Exception:  # noqa: BLE001
        log.exception("could not record identity run")


def rebuild_hook(r, decks: List[dict], tags: Dict[str, dict], chain, run) -> dict:
    """Called by the interaction-graph rebuild after the deck graphs are built:
    (re)draft identities for decks whose cards changed (capped per rebuild)."""
    if os.getenv("IDENTITY_ON_REBUILD", "1") == "0":
        return {"skipped": "IDENTITY_ON_REBUILD=0"}
    texts = {n: t.get("card") for n, t in tags.items() if t.get("card")}
    emb = embeddings.default_embedder()
    try:
        max_decks = int(os.getenv("IDENTITY_MAX_DECKS_PER_REBUILD", identity.DEFAULT_MAX_DECKS_PER_REBUILD))
    except ValueError:
        max_decks = identity.DEFAULT_MAX_DECKS_PER_REBUILD
    return identity.generate_for_rebuild(r, decks, chain, texts, embedder=emb if emb.available() else None,
                                         on_call=run.add_llm_call, max_decks=max_decks)
