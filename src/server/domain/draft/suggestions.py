"""Draft-mode suggestions (ARCHITECTURE.md §8 Slice 5): tuner weak-performer
cuts and reconstruct tiered-pool adds, ported onto Postgres-fed data per the
§2 reuse map (analytics/tuner.py + tune_store.py scoring math keeps;
reconstruct_*.py pools/builder logic keep, session store Redis→Postgres —
the Postgres staging session is domain/draft/draft_mode.py).

Pool tiering keeps the RECONSTRUCT_POOLS.md model — a card lands in exactly
ONE tier, the lowest-numbered it qualifies for:

    Tier 1  previous_deck   cards in the draft's source decklists
    Tier 2  owned           owned copies free in the collection
                            (instances in_collection, dedicated to no deck)
    Tier 3  new             everything else (not owned)

Own-first (§6 rule 5 / tuner AN-8 steal rule): suggestions prefer owned
cards not in another deck. A copy sleeved in ANOTHER deck (a donor pull) is
only suggested when the donor is left no worse — a free replacement of at
least the same score exists — and is always labelled with the donor deck.

Scoring keeps the tuner/weak-performer formulas (analytics/weak_performers.py,
analytics/tuner.py): edges / pillar fit / EDHREC inclusion / played-with,
each 0..1, mean of the parts that exist; unknown is never weak (unavailable
parts score neutral, and the unavailable input is reported with a reason —
AGENT_RULES.md §4). Pure data-in / data-out: the API assembles the inputs
from Postgres and every signal it cannot supply arrives as None.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

from src.server.domain.analytics import weak_performers as weak_mod

TIER_PREVIOUS_DECK = "previous_deck"
TIER_OWNED = "owned"
TIER_DONOR = "donor_pull"
TIER_NEW = "new"
TIER_ORDER = (TIER_PREVIOUS_DECK, TIER_OWNED, TIER_DONOR, TIER_NEW)

# Tuner thresholds (analytics/tuner.py) — kept verbatim.
MIN_UPGRADE = 0.05
GAP_WORTH = 0.10
EDHREC_FULL = weak_mod.EDHREC_FULL


def candidate_score(edhrec_inclusion: Optional[float] = None,
                    pillar_fit: Optional[float] = None,
                    played_with: Optional[float] = None) -> Optional[float]:
    """Strength score 0..1 for an ADD candidate: the tuner swap score —
    mean of the parts that exist (EDHREC inclusion capped at EDHREC_FULL,
    pillar fit, played-with). None when no part exists (caller renders
    \"unavailable\" + reason, never a dressed-up 0)."""
    parts: List[float] = []
    if edhrec_inclusion is not None:
        parts.append(min(1.0, max(0.0, edhrec_inclusion) / EDHREC_FULL))
    if pillar_fit is not None:
        parts.append(max(0.0, min(1.0, pillar_fit)))
    if played_with is not None:
        parts.append(max(0.0, min(1.0, played_with)))
    if not parts:
        return None
    return round(sum(parts) / len(parts), 4)


def build_pools(
    source_deck_cards: Mapping[str, List[dict]],
    collection_counts: Mapping[str, int],
    donor_counts: Mapping[str, Mapping[str, int]],
) -> Dict[str, dict]:
    """Tier the candidate pool for a draft.

    source_deck_cards: {deck_id: [deck_cards rows]} — Tier 1.
    collection_counts: {oracle_id: free in_collection copies} — Tier 2.
    donor_counts:      {oracle_id: {deck_id: copies in that deck}} — pulls.
    Returns {oracle_id: {tier, qty, donors, source_decks}}.
    """
    pools: Dict[str, dict] = {}
    for deck_id, rows in source_deck_cards.items():
        for row in rows:
            oid = row["oracle_id"]
            entry = pools.setdefault(oid, {
                "tier": TIER_PREVIOUS_DECK, "qty": 0, "donors": {}, "source_decks": [],
            })
            entry["qty"] += int(row.get("qty") or 1)
            if deck_id not in entry["source_decks"]:
                entry["source_decks"].append(deck_id)
    for oid, n in collection_counts.items():
        if int(n) < 1 or oid in pools:
            continue
        pools[oid] = {"tier": TIER_OWNED, "qty": int(n), "donors": {}, "source_decks": []}
    for oid, by_deck in donor_counts.items():
        if oid in pools:
            pools[oid]["donors"] = dict(by_deck)
        else:
            pools[oid] = {
                "tier": TIER_DONOR, "qty": int(sum(by_deck.values())),
                "donors": dict(by_deck), "source_decks": [],
            }
    return pools


def suggest_adds(
    pools: Mapping[str, dict],
    facts: Mapping[str, dict],
    draft_oracle_ids: Iterable[str],
    color_identity: Iterable[str],
    scores: Optional[Mapping[str, Optional[float]]] = None,
    limit: int = 10,
) -> dict:
    """Ranked ADD suggestions for the staged deck, own-first.

    pools/facts: from build_pools + the cards table. scores: precomputed
    candidate_score per oracle_id (None = unavailable). Cards already in the
    draft and cards outside the target color identity are excluded.
    Donor pulls come last among owned options and only when the donor is
    left no worse (a free replacement scoring >= the pulled card exists);
    otherwise they are counted in ``hidden_donor_pulls`` with the reason.
    """
    scores = scores or {}
    in_draft = set(draft_oracle_ids)
    ci = {c for c in (color_identity or [])}
    rows: List[dict] = []
    hidden_donor = 0
    # Free replacements available to donors: Tier 1/2 cards not in the draft.
    free_replacements = {
        oid: (scores.get(oid) or 0.0)
        for oid, p in pools.items()
        if p["tier"] in (TIER_PREVIOUS_DECK, TIER_OWNED) and oid not in in_draft
    }
    for oid, pool in pools.items():
        if oid in in_draft:
            continue
        fact = facts.get(oid) or {}
        card_ci = {c for c in (fact.get("color_identity") or [])}
        if ci and not card_ci <= ci:
            continue
        score = scores.get(oid)
        row = {
            "oracle_id": oid,
            "name": fact.get("name") or oid,
            "tier": pool["tier"],
            "qty_available": pool["qty"],
            "score": score,
            "score_unavailable": score is None,
            "donor": None,
            "reasons": _add_reasons(pool, score),
        }
        if pool["tier"] == TIER_DONOR:
            donor_deck = sorted(pool["donors"])[0]
            # AN-8: the pull stands only when the donor is left no worse —
            # a free replacement scoring at least as well as the pulled card.
            taken = score or 0.0
            replacement = next(
                (r for r, s in free_replacements.items() if s >= taken), None
            )
            if replacement is None:
                hidden_donor += 1
                continue
            row["donor"] = {
                "deck_id": donor_deck,
                "replacement_oracle_id": replacement,
                "replacement_name": (facts.get(replacement) or {}).get("name") or replacement,
            }
            row["reasons"].append(
                f"pulled from {donor_deck}; replaced there by {row['donor']['replacement_name']}"
            )
        rows.append(row)
    tier_rank = {t: i for i, t in enumerate(TIER_ORDER)}
    rows.sort(key=lambda r: (
        tier_rank.get(r["tier"], 99),
        -(r["score"] if r["score"] is not None else -1),
        r["name"],
    ))
    return {
        "adds": rows[: max(1, int(limit))],
        "hidden_donor_pulls": hidden_donor,
        "hidden_reason": ("donor would be left worse (no free replacement "
                          "scoring at least as well)") if hidden_donor else None,
    }


def _add_reasons(pool: Mapping[str, Any], score: Optional[float]) -> List[str]:
    reasons = []
    if pool["tier"] == TIER_PREVIOUS_DECK:
        reasons.append("already in a source deck — no purchase needed")
    elif pool["tier"] == TIER_OWNED:
        reasons.append(f"owned and free ({pool['qty']} in collection)")
    elif pool["tier"] == TIER_NEW:
        reasons.append("not owned — would be a purchase")
    if score is not None:
        reasons.append(f"score {score}")
    return reasons


def weak_cuts(
    draft_cards: List[dict],
    degrees: Optional[Mapping[str, int]] = None,
    fits: Optional[Mapping[str, float]] = None,
    inclusions: Optional[Mapping[str, float]] = None,
    pets: Iterable[str] = (),
    limit: int = 10,
) -> dict:
    """Ranked CUT candidates among the staged cards — the weak-performer
    math (analytics/weak_performers.py weak_parts) run over the draft list.

    Signals arrive as {name: value} maps or None; a None signal is
    unavailable: it scores neutral (never weak) and is reported in
    ``inputs_unavailable`` with a reason (AGENT_RULES.md §4). Lands,
    commanders and pet cards are never cut candidates.
    """
    pets = set(pets or ())
    has_graph = degrees is not None
    has_pillars = fits is not None
    has_edhrec = inclusions is not None
    deg = dict(degrees or {})
    p75 = weak_mod._p75(list(deg.values())) if has_graph else 1.0

    rows: List[dict] = []
    for card in draft_cards:
        name = card.get("name") or card["oracle_id"]
        if card.get("board") == "commander" or name in pets:
            continue
        type_line = card.get("type_line") or ""
        if "Land" in type_line:
            continue
        # Unavailable graph => neutral edges (degree == p75 => part 0),
        # the same "unknown is never weak" convention as the played median.
        degree = deg.get(name, int(p75)) if has_graph else int(p75) or 1
        parts = weak_mod.weak_parts(
            degree, p75,
            (fits or {}).get(name),
            (inclusions or {}).get(name),
            has_pillars=has_pillars,
            has_edhrec=has_edhrec,
        )
        rows.append({
            "oracle_id": card["oracle_id"],
            "name": name,
            "board": card.get("board"),
            "weak_score": parts["score"],
            "parts": {k: parts[k] for k in weak_mod.PARTS},
            "reasons": _cut_reasons(parts, degree, has_graph, has_pillars, has_edhrec),
        })
    rows.sort(key=lambda r: (-r["weak_score"], r["name"]))
    unavailable = {}
    if not has_graph:
        unavailable["edges"] = "interaction graph not yet in Postgres (Slice 6)"
    if not has_pillars:
        unavailable["pillar_fit"] = "pillar identity not yet in Postgres"
    if not has_edhrec:
        unavailable["edhrec"] = "EDHREC inclusion not yet in Postgres"
    return {
        "cuts": rows[: max(1, int(limit))],
        "inputs_unavailable": unavailable,
        "weights": {"parts": list(weak_mod.PARTS), "equal": True,
                    "p75_degree": round(p75, 2), "edhrec_full_at": EDHREC_FULL},
        "note": ("Weak = few edges + off-pillar + low EDHREC, equally weighted; "
                 "unavailable signals score neutral, never weak. Lands, "
                 "commanders and pet cards are never cuts."),
    }


def _cut_reasons(parts: Mapping[str, float], degree: int,
                 has_graph: bool, has_pillars: bool, has_edhrec: bool) -> List[str]:
    out = []
    if has_graph and parts["edges"] >= 0.5:
        out.append(f"{degree} edge{'s' if degree != 1 else ''}")
    if has_pillars and parts["pillar"] >= 0.5:
        out.append("weak pillar fit")
    if has_edhrec and parts["edhrec"] >= 0.5:
        out.append("low EDHREC inclusion")
    return out
