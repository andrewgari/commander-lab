"""Cross-reference checks for review_document v1 that JSON Schema cannot express.

Used by tests/test_review_document_schema.py and by later reviewer cards.
"""
from __future__ import annotations

import json
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "review_document.schema.json"
SEVERITY_ORDER = {"critical": 0, "high": 1, "med": 2, "low": 3}


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def _dupes(ids: list[str]) -> set[str]:
    return {i for i in ids if ids.count(i) > 1}


def check_references(doc: dict) -> list[str]:
    """Return a list of human-readable problems; empty means all references resolve."""
    errs: list[str] = []
    findings = doc.get("strengths", []) + doc.get("problems", [])
    finding_ids = [f["id"] for f in findings]
    swap_ids = [s["id"] for s in doc.get("swaps", [])]
    q_ids = [q["id"] for q in doc.get("questions_for_owner", [])]

    for label, ids in (("finding", finding_ids), ("swap", swap_ids), ("question", q_ids)):
        for d in sorted(_dupes(ids)):
            errs.append(f"duplicate {label} id: {d}")

    for row in doc.get("scorecard", []):
        for fid in row["finding_ids"]:
            if fid not in finding_ids:
                errs.append(f"scorecard[{row['axis']}].finding_ids -> unknown finding {fid}")
    for sid in doc.get("top_changes", {}).get("swap_ids", []):
        if sid not in swap_ids:
            errs.append(f"top_changes.swap_ids -> unknown swap {sid}")
    problem_ids = [p["id"] for p in doc.get("problems", [])]
    for s in doc.get("swaps", []):
        if s["resolves_finding_id"] not in finding_ids:
            errs.append(f"swap {s['id']}.resolves_finding_id -> unknown finding {s['resolves_finding_id']}")
        elif s["resolves_finding_id"] not in problem_ids:
            errs.append(f"swap {s['id']} resolves {s['resolves_finding_id']}, which is not a problem")

    sev = [SEVERITY_ORDER[p["severity"]] for p in doc.get("problems", [])]
    if sev != sorted(sev):
        errs.append("problems are not ordered by severity (most severe first)")

    v = doc.get("verdict", {})
    if len(v.get("headline", "").split()) > 20:
        errs.append("verdict.headline exceeds 20 words")
    if len(v.get("summary", "").split()) > 80:
        errs.append("verdict.summary exceeds 80 words")

    buckets = doc.get("charts", {}).get("curve", {}).get("buckets", [])
    if [b["mv"] for b in buckets] != list(range(len(buckets))):
        errs.append("charts.curve.buckets are not in mv order 0..7")
    for r in doc.get("charts", {}).get("roles", []):
        if r["target_min"] > r["target_max"]:
            errs.append(f"charts.roles[{r['role']}] target_min > target_max")
    return errs
