"""Run the deterministic gates over recorded fixtures.

    python -m tests.review_evals.run_gates                  # good fixtures; exit 1 on any violation
    python -m tests.review_evals.run_gates --dir PATH       # any fixture dir (e.g. fixtures/bad)
    python -m tests.review_evals.run_gates --self-test      # good must pass AND each bad must trip its gate
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tests.review_evals.gates import EvalContext, GATES, fresh_fact_pack, run_gates

FIX = Path(__file__).parent / "fixtures"


def load_case(path: Path) -> tuple[dict, EvalContext]:
    case = json.loads(path.read_text())
    ctx = EvalContext(deck=case["deck"], scryfall=case["scryfall"],
                      fresh_fact_pack=fresh_fact_pack(case["deck"], case["scryfall"]))
    return case, ctx


def check_dir(d: Path) -> int:
    files = sorted(d.glob("*.json"))
    if not files:
        print(f"no fixtures found in {d}", file=sys.stderr)
        return 1
    failed = 0
    for f in files:
        case, ctx = load_case(f)
        v = run_gates(case["review"], ctx)
        if v:
            failed += 1
            print(f"FAIL {f.name}")
            for x in v:
                print(f"  {x}")
        else:
            print(f"PASS {f.name}")
    return 1 if failed else 0


def self_test() -> int:
    rc = check_dir(FIX / "good")
    bad = sorted((FIX / "bad").glob("*.json"))
    covered = {json.loads(f.read_text())["expect_gate"] for f in bad}
    missing = set(GATES) - covered
    if missing:
        print(f"SELF-TEST FAIL: gates with no failing fixture: {sorted(missing)}")
        rc = 1
    for f in bad:
        case, ctx = load_case(f)
        names = {v.gate for v in run_gates(case["review"], ctx)}
        want = case["expect_gate"]
        if want in names:
            print(f"OK   {f.name}: tripped [{want}] (all: {sorted(names)})")
        else:
            print(f"SELF-TEST FAIL {f.name}: expected [{want}], got {sorted(names)}")
            rc = 1
    return rc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=FIX / "good")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    sys.exit(self_test() if a.self_test else check_dir(a.dir))
