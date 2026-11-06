"""Slice 3 candidate retrieval + recommendations (ARCHITECTURE.md §6).

Pure module. First-pass retrieval sources (pgvector switching is Slice 6):

- EDHREC: the commander page's high-inclusion cards not already in the deck.
- Tags: owned cards whose composition-role classification fills a row the
  deck is short on (deterministic; uses the same oracle-rule/composition
  code as the fact pack).

Own-first (§6 pass 5): every candidate is annotated with collection
ownership — free copies first, copies sleeved in another deck flagged
``donor_pull`` (a pull must leave the donor no worse; the replacement flow
itself is Slice 5 draft mode, here we only surface the flag), unowned = buy.

Recommendations are ranked with a deterministic score whose parts are all
visible in ``why``. Cuts come from the fact pack's stragglers (pet cards are
already protected there) and never include commanders or basics.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.server.domain.analytics import composition as comp

from .fact_pack import BASICS, name_key

MAX_ADDS = 15
MAX_CANDIDATES = 40

# deterministic score weights (visible in every candidate's why)
W_INCLUSION = 2.0
W_SYNERGY = 1.0
W_OWNED_FREE = 1.5
W_OWNED_ELSEWHERE = 0.5
W_ROLE_GAP = 0.5

OWNED_STATUSES = ("in_collection", "in_mail", "in_deck")
FREE_STATUSES = ("in_collection", "in_mail")


def owned_index(instances: Sequence[Mapping], names_by_oracle: Mapping[str, str],
                deck_names: Optional[Mapping[str, str]] = None) -> Dict[str, dict]:
    """{name_key: {name, copies, available, decks}} from card_instances rows.

    `names_by_oracle`: oracle_id -> card name (cards table). `deck_names`:
    deck_id -> deck name for the donor list.
    """
    deck_names = deck_names or {}
    out: Dict[str, dict] = {}
    for inst in instances:
        status = inst.get("status")
        name = names_by_oracle.get(inst.get("oracle_id") or "")
        if status not in OWNED_STATUSES or not name:
            continue
        key = name_key(name)
        rec = out.setdefault(key, {"name": name, "copies": 0, "available": 0, "decks": []})
        rec["copies"] += 1
        if status in FREE_STATUSES:
            rec["available"] += 1
        else:
            deck_label = deck_names.get(str(inst.get("deck_id"))) or str(inst.get("deck_id") or "")
            if deck_label and deck_label not in rec["decks"]:
                rec["decks"].append(deck_label)
    return out


def _ownership(key: str, owned: Mapping[str, Mapping], in_deck: set) -> dict:
    rec = owned.get(key)
    if not rec:
        return {"status": "unowned", "available": 0, "decks": [], "donor_pull": False}
    if (rec.get("available") or 0) > 0:
        return {"status": "free", "available": rec["available"], "decks": [], "donor_pull": False}
    decks = [d for d in (rec.get("decks") or []) if d]
    return {
        "status": "in_other_deck" if decks else "unowned",
        "available": 0,
        "decks": decks,
        "donor_pull": bool(decks) and key not in in_deck,
    }


def edhrec_candidates(page: Optional[Mapping], deck: Mapping, owned: Mapping[str, Mapping],
                      color_identity: Sequence[str],
                      card_info: Optional[Mapping[str, Mapping]] = None) -> List[dict]:
    """High-inclusion EDHREC cards not in the deck, own-first annotated.

    `card_info` ({name_key: {name, color_identity}}) comes from the caller's
    Scryfall/DB resolution (EDHREC pages carry no color identity); when it is
    missing the identity filter cannot run and says so in the audit field
    `identity_checked`.
    """
    page = page or {}
    if page.get("error") or not (page.get("cards")):
        return []
    card_info = card_info or {}
    commanders = {name_key(c) for c in deck.get("commanders") or []}
    in_deck = commanders | {name_key(c["name"]) for c in deck.get("cards") or []}
    allowed = set(color_identity or [])
    out = []
    for key, rec in (page.get("cards") or {}).items():
        name = rec.get("name")
        if not name or key in in_deck or name in BASICS:
            continue
        info = card_info.get(key) or {}
        ci = set(info.get("color_identity") or [])
        if allowed and info and not ci <= allowed:
            continue  # off-identity suggestions are never candidates
        ownership = _ownership(key, owned, in_deck)
        out.append({
            "name": info.get("name") or name,
            "identity_checked": bool(info),
            "source": "edhrec",
            "inclusion": rec.get("inclusion"),
            "synergy": round(rec["synergy"], 4) if rec.get("synergy") is not None else None,
            "salt": rec.get("salt"),
            "role_hits": [],
            "ownership": ownership,
        })
    out.sort(key=lambda c: (-(c.get("inclusion") or 0.0), c["name"]))
    return out[:MAX_CANDIDATES]


def tag_candidates(deck: Mapping, fact_pack: Mapping, collection_texts: Mapping[str, Mapping],
                   owned: Mapping[str, Mapping]) -> List[dict]:
    """Owned, free cards that fill a composition row the deck is short on."""
    short_rows = [r["key"] for r in ((fact_pack.get("composition") or {}).get("rows") or [])
                  if (r.get("delta") or 0) < 0]
    if not short_rows or not collection_texts:
        return []
    commanders = {name_key(c) for c in deck.get("commanders") or []}
    in_deck = commanders | {name_key(c["name"]) for c in deck.get("cards") or []}
    out = []
    for name in sorted(collection_texts):
        key = name_key(name)
        if key in in_deck or name in BASICS:
            continue
        rec = owned.get(key)
        if not rec or (rec.get("available") or 0) <= 0:
            continue  # own-first: tag pass only surfaces FREE copies
        rows = comp.classify_card(name, dict(collection_texts[name])) or ["plan"]
        hits = [r for r in rows if r in short_rows]
        if not hits:
            continue
        out.append({
            "name": name,
            "source": "collection_tags",
            "inclusion": None,
            "synergy": None,
            "salt": None,
            "role_hits": sorted(hits),
            "ownership": _ownership(key, owned, in_deck),
        })
    out.sort(key=lambda c: (-len(c["role_hits"]), c["name"]))
    return out[:MAX_CANDIDATES]


def _score(c: Mapping) -> float:
    parts = [
        W_INCLUSION * (c.get("inclusion") or 0.0),
        W_SYNERGY * max(c.get("synergy") or 0.0, 0.0),
        W_ROLE_GAP * len(c.get("role_hits") or []),
    ]
    status = (c.get("ownership") or {}).get("status")
    if status == "free":
        parts.append(W_OWNED_FREE)
    elif status == "in_other_deck":
        parts.append(W_OWNED_ELSEWHERE)
    return round(sum(parts), 4)


def _why(c: Mapping) -> str:
    bits = []
    if c.get("inclusion") is not None:
        bits.append(f"EDHREC inclusion {c['inclusion']:.0%}")
    if c.get("synergy") is not None:
        bits.append(f"synergy {c['synergy']:+.0%}")
    if c.get("role_hits"):
        bits.append("fills short row(s): " + ", ".join(c["role_hits"]))
    own = (c.get("ownership") or {}).get("status")
    if own == "free":
        bits.append(f"owned: {(c.get('ownership') or {}).get('available')} free copy(ies)")
    elif own == "in_other_deck":
        bits.append("owned but sleeved in " + ", ".join((c.get("ownership") or {}).get("decks") or []))
    else:
        bits.append("not owned (buy)")
    return "; ".join(bits) + "."


def recommend(fact_pack: Mapping, candidates: Sequence[Mapping]) -> dict:
    """Ranked adds/cuts, deterministic. Same inputs -> same ranking."""
    adds = []
    for c in candidates:
        row = {**c, "score": _score(c)}
        row["why"] = _why(row) + f" score={row['score']}."
        adds.append(row)
    adds.sort(key=lambda c: (-c["score"], c["name"]))
    adds = adds[:MAX_ADDS]

    strag = fact_pack.get("stragglers") or {}
    cuts = [{
        "name": s["name"],
        "score": s.get("straggler_score"),
        "reasons": list(s.get("weak_reasons") or []) + list(s.get("durdle_reasons") or []),
        "why": s.get("why"),
    } for s in strag.get("cards") or []]
    return {
        "adds": adds,
        "cuts": cuts,
        "protected_pets": [p.get("name") for p in strag.get("protected_pets") or []],
        "why": ("Add score = 2*inclusion + 1*max(synergy,0) + 0.5/short role filled "
                "+ 1.5 free owned copy / 0.5 owned elsewhere. Cuts are the fact pack's "
                "stragglers (Tuner weak score + durdle, outside every win line); pet "
                "cards are protected and listed, never cut."),
    }


def retrieve(fact_pack: Mapping, deck: Mapping, *, edhrec_page: Optional[Mapping],
             owned: Mapping[str, Mapping], collection_texts: Optional[Mapping[str, Mapping]],
             color_identity: Sequence[str],
             card_info: Optional[Mapping[str, Mapping]] = None) -> dict:
    """The analysis block: candidates from every source + ranked adds/cuts."""
    candidates = edhrec_candidates(edhrec_page, deck, owned, color_identity, card_info)
    candidates += tag_candidates(deck, fact_pack, collection_texts or {}, owned)
    recs = recommend(fact_pack, candidates)
    return {
        "candidates": sorted(candidates, key=lambda c: (c["source"], c["name"])),
        "adds": recs["adds"],
        "cuts": recs["cuts"],
        "protected_pets": recs["protected_pets"],
        "sources": {
            "edhrec": {
                "status": "ok" if (edhrec_page or {}).get("cards") and not (edhrec_page or {}).get("error")
                          else "unavailable",
                "reason": None if (edhrec_page or {}).get("cards") and not (edhrec_page or {}).get("error")
                          else str((edhrec_page or {}).get("error") or "EDHREC page not fetched")[:200],
                "decks_sampled": (edhrec_page or {}).get("decks_sampled"),
                "stale": bool((edhrec_page or {}).get("stale")),
            },
            "collection_tags": {
                "status": "ok" if collection_texts else "unavailable",
                "reason": None if collection_texts else "no owned collection cards with reference data",
                "pool": len(collection_texts or {}),
            },
        },
        "why": recs["why"],
    }
