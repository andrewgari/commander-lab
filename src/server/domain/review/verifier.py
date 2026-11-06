"""Slice 3 verifier (ARCHITECTURE.md §6): every card name in the analysis
and in LLM output is checked against Scryfall before it is stored.

Pure module: the Scryfall check is an injected ``resolver`` callable
``name -> {"name": canonical, ...} | None`` so the verifier is testable
offline. Unknown names are rejected (dropped from adds/candidates and
recorded in the audit) — never kept, never silently renamed.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Mapping, Optional

Resolver = Callable[[str], Optional[dict]]


def verify_names(names: List[str], resolver: Resolver) -> Dict[str, dict]:
    """{input name: {resolved, canonical}} for each unique name (sorted)."""
    out: Dict[str, dict] = {}
    for name in sorted({n for n in names if n}):
        hit = resolver(name)
        out[name] = {"resolved": bool(hit), "canonical": (hit or {}).get("name")}
    return out


def verify_analysis(analysis: Mapping, resolver: Resolver) -> tuple:
    """Validate every name in candidates/adds/cuts.

    Returns (analysis', audit). Rejected names are dropped from candidates
    and adds; cuts reference the deck's own cards, so a rejected cut name is
    kept but flagged (it came from our DB, not the LLM).
    """
    names: List[str] = []
    for key in ("candidates", "adds", "cuts"):
        names += [r.get("name") for r in analysis.get(key) or [] if r.get("name")]
    checks = verify_names([n for n in names if n], resolver)
    rejected = sorted(n for n, c in checks.items() if not c["resolved"])

    def fix(row: Mapping) -> dict:
        canon = checks.get(row["name"], {}).get("canonical")
        return {**row, "name": canon or row["name"]}

    out = dict(analysis)
    out["candidates"] = [fix(r) for r in analysis.get("candidates") or []
                         if checks.get(r["name"], {}).get("resolved")]
    out["adds"] = [fix(r) for r in analysis.get("adds") or []
                   if checks.get(r["name"], {}).get("resolved")]
    out["cuts"] = [{**fix(r), "verified": checks.get(r["name"], {}).get("resolved", False)}
                   for r in analysis.get("cuts") or []]
    audit = {
        "checked": len(checks),
        "rejected": rejected,
        "renamed": {n: c["canonical"] for n, c in sorted(checks.items())
                    if c["resolved"] and c["canonical"] and c["canonical"] != n},
        "rule": "every candidate/add/cut name resolved against Scryfall; unknown names rejected",
    }
    return out, audit


def verify_explanations(explanations: Mapping[str, Mapping], allowed_names: set,
                        resolver: Resolver) -> tuple:
    """Validate the card names the LLM listed per section.

    A name is kept only if it resolves on Scryfall AND was part of the
    verified input (`allowed_names`) — the LLM may not introduce cards.
    Returns (explanations', audit).
    """
    listed: List[str] = []
    for block in explanations.values():
        listed += [n for n in (block.get("cards") or []) if n]
    checks = verify_names(listed, resolver)
    rejected, out = [], {}
    for key in sorted(explanations):
        block = explanations[key]
        kept, dropped = [], []
        for n in block.get("cards") or []:
            ok = checks.get(n, {}).get("resolved") and n in allowed_names
            (kept if ok else dropped).append(checks.get(n, {}).get("canonical") or n)
        rejected += dropped
        out[key] = {**block, "cards": kept, "rejected_cards": sorted(dropped)}
    audit = {
        "checked": len(checks),
        "rejected": sorted(set(rejected)),
        "rule": ("every LLM-listed card name must resolve on Scryfall and come from the "
                 "verified input set; anything else is dropped and recorded"),
    }
    return out, audit
