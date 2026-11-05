"""
Pillar fit (AN-3): score every card of a deck against the deck's pillars
with local embeddings (analytics/embeddings.py).

    card text  = "{name}. {type line}. {oracle text, reminder text stripped}"
    pillar     = "{name}: {description}"   (the description is what cards are embedded against)
    sim        = cosine(card, pillar);  best = the pillar with the highest sim
    score      = embedder.scale(best sim) in 0..1 (2 dp in the UI)

Buckets (UX_SPEC §3.1.9 / pillars.html):
    pillar   best sim >= embedder.on_pillar_sim and not a land
    glue     otherwise, a card in a functional composition row (lands, ramp,
             card advantage, targeted/mass disruption)
    generic  fits no pillar and isn't glue
Counts are by quantity (they add up to the deck size). on_plan_pct =
(pillar + glue) / total.

Only live pillars score: pinned, accepted and draft. Rejected ones don't.
Card vectors are cached in the Redis hash embeddings:card:{model_id}, field
= sha1(card text)[:16], value = base64 float32. Pillar vectors are cheap
(<= 5 per deck) and are embedded on every call, so an edit rescored < 1 s.
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Mapping, Optional

from . import composition as comp
from .embeddings import Embedder, decode_vector, encode_vector

LIVE_STATES = ("pinned", "accepted", "draft")
CACHE_PREFIX = "embeddings:card:"
_REMINDER = re.compile(r"\s*\([^()]*\)")


def card_text(name: str, card: Optional[dict]) -> str:
    if not card:
        return f"{name}."
    type_line = (card.get("type_line") or "").strip()
    text = _REMINDER.sub("", card.get("oracle_text") or "").replace("\n", " ").strip()
    parts = [name, type_line, text]
    return ". ".join(p.rstrip(".") for p in parts if p) + "."


def pillar_text(p: dict) -> str:
    name = (p.get("name") or "").strip()
    desc = (p.get("description") or "").strip()
    return f"{name}: {desc}" if desc else name


def live_pillars(identity: Optional[dict]) -> List[dict]:
    ps = [p for p in (identity or {}).get("pillars") or [] if p.get("state") in LIVE_STATES]
    return sorted(ps, key=lambda p: p.get("order") or 0)


def _text_key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def card_vectors(r, embedder: Embedder, texts: Dict[str, str]) -> Dict[str, List[float]]:
    """{name: vector}, embedding only texts the cache hasn't seen for this model."""
    key = CACHE_PREFIX + embedder.model_id
    names = sorted(texts)
    fields = [_text_key(texts[n]) for n in names]
    cached = r.hmget(key, fields) if names else []
    out: Dict[str, List[float]] = {}
    todo = []
    for n, f, raw in zip(names, fields, cached):
        if raw:
            try:
                out[n] = decode_vector(raw)
                continue
            except Exception:  # noqa: BLE001 - corrupt entry: re-embed
                pass
        todo.append((n, f))
    if todo:
        vecs = embedder.embed([texts[n] for n, _ in todo], kind="passage")
        mapping = {}
        for (n, f), v in zip(todo, vecs):
            out[n] = list(v)
            mapping[f] = encode_vector(v)
        r.hset(key, mapping=mapping)
    return out


def _dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def deck_fit(r, deck: dict, identity: Optional[dict], card_texts: Mapping[str, dict], embedder: Embedder,
             composition: Optional[dict] = None) -> dict:
    comp_out = composition or comp.compute_composition(deck, card_texts)
    rows_by_card = comp_out["by_card"]
    qty = dict(comp._deck_entries(deck))
    names = sorted(qty)
    total = sum(qty.values())
    pillars = live_pillars(identity)

    def is_land(n):
        return rows_by_card.get(n) == ["lands"]

    def glue(n):
        return any(row in comp.FUNCTIONAL_ROWS for row in rows_by_card.get(n, []))

    base = {
        "model": embedder.model_id,
        "thresholds": {"on_pillar_sim": embedder.on_pillar_sim, "sim_floor": embedder.sim_floor,
                       "sim_ceil": embedder.sim_ceil},
        "total_cards": total,
    }

    if not pillars or not embedder.available():
        status = "no_pillars" if not pillars else "embedder_unavailable"
        per_card = [{"name": n, "quantity": qty[n], "score": None, "similarity": None, "pillar_id": None,
                     "bucket": "glue" if glue(n) else "generic", "rows": rows_by_card.get(n, [])} for n in names]
        g = sum(row["quantity"] for row in per_card if row["bucket"] == "glue")
        return {**base, "status": status, "pillars": [], "per_card": per_card, "pillar": 0, "glue": g,
                "generic": total - g, "on_plan_pct": round(100 * g / total) if total else 0}

    texts = {n: card_text(n, card_texts.get(n)) for n in names}
    cvecs = card_vectors(r, embedder, texts)
    pvecs = embedder.embed([pillar_text(p) for p in pillars], kind="query")

    per_card = []
    sims_by_pillar: Dict[str, List[tuple]] = {p["id"]: [] for p in pillars}
    for n in names:
        sims = [_dot(cvecs[n], pv) for pv in pvecs]
        best_i = max(range(len(sims)), key=lambda i: sims[i])
        best = sims[best_i]
        pid = pillars[best_i]["id"]
        on_pillar = best >= embedder.on_pillar_sim and not is_land(n)
        bucket = "pillar" if on_pillar else ("glue" if glue(n) else "generic")
        per_card.append({
            "name": n, "quantity": qty[n], "score": embedder.scale(best), "similarity": round(best, 4),
            "pillar_id": pid, "pillar": pillars[best_i]["name"], "bucket": bucket,
            "rows": rows_by_card.get(n, []),
            "by_pillar": {p["id"]: embedder.scale(s) for p, s in zip(pillars, sims)},
        })
        if not is_land(n):
            for p, s in zip(pillars, sims):
                sims_by_pillar[p["id"]].append((s, n))
    per_card.sort(key=lambda row: (-row["score"], row["name"]))

    counts = {"pillar": 0, "glue": 0, "generic": 0}
    for row in per_card:
        counts[row["bucket"]] += row["quantity"]

    pillars_out = []
    for p in pillars:
        members = [row for row in per_card if row["bucket"] == "pillar" and row["pillar_id"] == p["id"]]
        top = [n for _, n in sorted(sims_by_pillar[p["id"]], key=lambda t: (-t[0], t[1]))[:3]]
        pillars_out.append({"id": p["id"], "name": p["name"], "state": p.get("state"), "order": p.get("order"),
                            "count": sum(m["quantity"] for m in members), "top_cards": top})

    return {**base, "status": "ok", "pillars": pillars_out, "per_card": per_card, **counts,
            "on_plan_pct": round(100 * (counts["pillar"] + counts["glue"]) / total) if total else 0}
