"""Judge-vs-human agreement report.

    python tests/review_evals/calibration/report.py [DIR]

Reads every ``*.json`` in DIR (default: this directory):
    {"review": {...}, "rubric": {...}, "intent": {...},
     "human": {"rubric_recall": 7, ...}, "judge": {"rubric_recall": 6, ...}}
``judge`` is filled by running judge.py on the case (``--fill`` is a TODO for
when a live judge exists). Prints per-dimension mean absolute error, within-1
agreement and Pearson correlation. Empty directory is fine: reports no cases.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

DIMS = ("rubric_recall", "specificity", "intent_alignment", "actionability", "generic_filler")


def _pearson(a, b):
    if len(a) < 3 or len(set(a)) < 2 or len(set(b)) < 2:
        return None
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / den


def main(argv: list[str]) -> int:
    d = Path(argv[1]) if len(argv) > 1 else Path(__file__).parent
    cases = [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))]
    cases = [c for c in cases if "human" in c and "judge" in c]
    if not cases:
        print(f"No calibration cases with both 'human' and 'judge' scores in {d}. See README.md to add one.")
        return 0
    print(f"{len(cases)} calibration case(s)")
    print(f"{'dimension':<18}{'MAE':>6}{'within1':>9}{'pearson':>9}")
    for dim in DIMS:
        pairs = [(c["human"][dim], c["judge"][dim]) for c in cases if dim in c["human"] and dim in c["judge"]]
        if not pairs:
            continue
        h, j = zip(*pairs)
        mae = statistics.fmean(abs(x - y) for x, y in pairs)
        w1 = sum(abs(x - y) <= 1 for x, y in pairs) / len(pairs)
        r = _pearson(h, j)
        print(f"{dim:<18}{mae:>6.2f}{w1:>9.0%}{'n/a' if r is None else f'{r:.2f}':>9}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
