"""Live eval: pipeline on the golden set -> gates -> judge -> results JSON.

Fails cleanly while the review pipeline does not exist yet.
"""
from __future__ import annotations

import importlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from tests.review_evals import judge as judge_mod

HERE = Path(__file__).parent
PIPELINE_MODULE = "src.server.domain.analytics.review_pipeline.runner"


def main() -> int:
    try:
        runner = importlib.import_module(PIPELINE_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name and PIPELINE_MODULE.startswith(exc.name):
            print(f"eval: the review pipeline does not exist yet ({PIPELINE_MODULE} not found).\n"
                  "      Live evals need cards 04+; use `make eval-gates` for the offline gates.", file=sys.stderr)
            return 2
        raise
    try:
        generator, judge = judge_mod.resolve_models()
    except judge_mod.JudgeConfigError as exc:
        print(f"eval: {exc}", file=sys.stderr)
        return 2
    run = getattr(runner, "run_review", None)
    if run is None:
        print(f"eval: {PIPELINE_MODULE} has no run_review(deck, intent) entrypoint yet.", file=sys.stderr)
        return 2
    # Wiring of per-deck execution (decklist -> deck, Scryfall context, judge client) lands with the pipeline card;
    # results schema below is what eval_compare.py consumes.
    results = {"generator": generator, "judge": judge, "decks": {}, "started_at": datetime.now(timezone.utc).isoformat()}
    for d in sorted((HERE / "decks").iterdir()):
        t0 = time.monotonic()
        out = run(deck_dir=d)
        results["decks"][d.name] = {"latency_s": round(time.monotonic() - t0, 2), "cost_usd": getattr(out, "cost_usd", None),
                                    "gate_violations": [], "judge": {}}
    (HERE / "results").mkdir(exist_ok=True)
    path = HERE / "results" / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(results, indent=2))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
