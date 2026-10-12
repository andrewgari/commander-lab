"""
Weak performers (AN-3): computed only, no game logs (ANALYTICS_SPEC Layer 3).

Three equally weighted parts, each 0..1 where 1 = weak:

    edges   1 - min(1, degree / p75)   degree in the AN-1 deck graph, ignoring
                                       edges whose only reasons are mana/lands
                                       (every rock "feeds" every spell); p75 =
                                       75th percentile of that degree over the
                                       deck's scored cards (min 1)
    pillar  1 - pillar fit score       (0 when the deck has no live pillars yet:
                                       unknown is not weak)
    edhrec  1 - min(1, inclusion / 0.50)   not listed on the commander's EDHREC
                                       page = 0% inclusion; 0 for every card when
                                       EDHREC is unavailable

weak_score = mean of the three, rounded to 3 dp. Lands and commanders are
not scored. Pet cards (passed in; the Journal owns them) keep their score but
are flagged and sorted after non-pets, and the weakest pet becomes the single
`pet_notice` row (UX_SPEC D7).
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Optional

from . import composition as comp

GLUE_REASONS = {"mana", "lands"}
EDHREC_FULL = 0.50
PARTS = ("edges", "pillar", "edhrec")


def _p75(values: List[int]) -> float:
    if not values:
        return 1.0
    s = sorted(values)
    k = 0.75 * (len(s) - 1)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return max(1.0, s[lo] + (s[hi] - s[lo]) * (k - lo))


def weak_parts(degree: int, p75: float, fit: Optional[float], inclusion: Optional[float],
               has_pillars: bool, has_edhrec: bool = True) -> Dict[str, float]:
    edges = 1.0 - min(1.0, degree / p75) if p75 else 1.0
    pillar = (1.0 - max(0.0, min(1.0, fit if fit is not None else 0.0))) if has_pillars else 0.0
    edhrec = (1.0 - min(1.0, (inclusion or 0.0) / EDHREC_FULL)) if has_edhrec else 0.0
    parts = {"edges": round(edges, 4), "pillar": round(pillar, 4), "edhrec": round(edhrec, 4)}
    parts["score"] = round(sum(parts[k] for k in PARTS) / len(PARTS), 4)
    return parts


def plan_degrees(graph: dict) -> Dict[str, int]:
    """Per-card degree counting only edges with at least one non-mana/lands reason."""
    deg: Dict[str, int] = {n["name"]: 0 for n in graph.get("nodes") or []}
    for e in graph.get("edges") or []:
        if set(e.get("reasons") or []) - GLUE_REASONS:
            deg[e["source"]] = deg.get(e["source"], 0) + 1
            deg[e["target"]] = deg.get(e["target"], 0) + 1
    return deg


def _reasons(parts: Dict[str, float], degree: int, fit_row: Optional[dict], inclusion: Optional[float],
             listed: bool) -> List[str]:
    out = []
    if parts["edges"] >= 0.5:
        out.append(f"{degree} edge{'s' if degree != 1 else ''}")
    if parts["pillar"] >= 0.5:
        out.append("off-pillar" if fit_row and fit_row.get("bucket") != "pillar" else "weak pillar fit")
    if parts["edhrec"] >= 0.5:
        out.append("not on EDHREC" if not listed else f"EDHREC {round(100 * (inclusion or 0))}%")
    return out


def compute_weak_performers(deck: dict, graph: dict, fit: dict, inclusion: dict, pets: Iterable[str] = (),
                            card_texts: Optional[Mapping[str, dict]] = None, limit: Optional[int] = None) -> dict:
    pets = set(pets or ())
    commanders = set(deck.get("commanders") or [])
    comp_rows = comp.compute_composition(deck, card_texts or {})["by_card"] if card_texts is not None else {}
    fit_by = {row["name"]: row for row in fit.get("per_card") or []}
    inc_by = {row["name"]: row for row in inclusion.get("per_card") or []}
    has_pillars = fit.get("status") == "ok"
    has_edhrec = inclusion.get("status", "ok") == "ok" and bool(inc_by)
    deg = plan_degrees(graph)
    raw_deg = {n["name"]: n.get("degree", 0) for n in graph.get("nodes") or []}

    def is_land(n):
        if comp_rows:
            return comp_rows.get(n) == ["lands"]
        row = inc_by.get(n)
        return bool(row and row.get("land")) or n in ("Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes")

    names = sorted(n for n in (set(deg) | set(fit_by)) if n not in commanders and not is_land(n))
    p75 = _p75([deg.get(n, 0) for n in names])
    rows = []
    for n in names:
        f = fit_by.get(n)
        i = inc_by.get(n) or {}
        parts = weak_parts(deg.get(n, 0), p75, f.get("score") if f else None, i.get("inclusion"),
                           has_pillars, has_edhrec)
        rows.append({
            "name": n,
            "weak_score": parts["score"],
            "parts": {k: parts[k] for k in PARTS},
            "edges": deg.get(n, 0),
            "edges_all": raw_deg.get(n, 0),
            "fit": f.get("score") if f else None,
            "pillar": f.get("pillar") if f and f.get("bucket") == "pillar" else None,
            "fit_bucket": f.get("bucket") if f else None,
            "edhrec_inclusion": i.get("inclusion"),
            "edhrec_listed": bool(i.get("listed")),
            "rows": comp_rows.get(n, []),
            "pet": n in pets,
            "reasons": _reasons(parts, deg.get(n, 0), f, i.get("inclusion"), bool(i.get("listed"))),
        })
    rows.sort(key=lambda row: (row["pet"], -row["weak_score"], row["name"]))
    pet_rows = sorted([row for row in rows if row["pet"]], key=lambda row: -row["weak_score"])
    pet_notice = None
    if pet_rows:
        top = pet_rows[0]
        pet_notice = {"name": top["name"], "weak_score": top["weak_score"],
                      "text": f"♥ {top['name']} · pet · weakest pet card · never cut"}
    return {
        "deck": deck.get("registry_id") or deck.get("id"),
        "cards": rows[:limit] if limit else rows,
        "count": len(rows),
        "pet_notice": pet_notice,
        "inputs": {"pillars": has_pillars, "edhrec": has_edhrec, "graph_edges": len(graph.get("edges") or [])},
        "weights": {"parts": list(PARTS), "equal": True, "p75_degree": round(p75, 2),
                    "edhrec_full_at": EDHREC_FULL, "ignored_edge_reasons": sorted(GLUE_REASONS)},
        "note": "Weak = few edges + off-pillar + low EDHREC, equally weighted. No game logs.",
    }
