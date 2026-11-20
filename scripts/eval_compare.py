#!/usr/bin/env python3
"""Diff two eval result files: python scripts/eval_compare.py A.json B.json

Expected shape (written by tests/review_evals/run_eval.py):
{"generator","judge","decks":{slug:{"latency_s","cost_usd","gate_violations":[...],"judge":{"scores":{dim:n}}}}}
"""
from __future__ import annotations

import json
import sys

DIMS = ("rubric_recall", "specificity", "intent_alignment", "actionability", "generic_filler")


def _num(x):
    return x if isinstance(x, (int, float)) else None


def _fmt(a, b):
    if a is None or b is None:
        return f"{'-' if a is None else a:>8} {'-' if b is None else b:>8} {'':>8}"
    return f"{a:>8.2f} {b:>8.2f} {b - a:>+8.2f}"


def compare(a: dict, b: dict) -> str:
    lines = [f"A: generator={a.get('generator')} judge={a.get('judge')}",
             f"B: generator={b.get('generator')} judge={b.get('judge')}", "",
             f"{'deck':<24}{'metric':<16}{'A':>8} {'B':>8} {'delta':>8}"]
    for slug in sorted(set(a.get("decks", {})) | set(b.get("decks", {}))):
        da, db = a.get("decks", {}).get(slug), b.get("decks", {}).get(slug)
        if da is None or db is None:
            lines.append(f"{slug:<24}only in {'B' if da is None else 'A'}")
            continue
        rows = [("gate_violations", len(da.get("gate_violations", [])), len(db.get("gate_violations", []))),
                ("cost_usd", _num(da.get("cost_usd")), _num(db.get("cost_usd"))),
                ("latency_s", _num(da.get("latency_s")), _num(db.get("latency_s")))]
        sa, sb = (da.get("judge") or {}).get("scores", {}), (db.get("judge") or {}).get("scores", {})
        rows += [(d, _num(sa.get(d)), _num(sb.get(d))) for d in DIMS if d in sa or d in sb]
        for name, x, y in rows:
            lines.append(f"{slug:<24}{name:<16}{_fmt(x, y)}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    a, b = (json.load(open(p)) for p in argv[1:])
    print(compare(a, b))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
