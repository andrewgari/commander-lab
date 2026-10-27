"""Offline pipeline smoke and optional live eval for the golden set."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).parent


def _offline_smoke() -> int:
    """Exercise run_review with deterministic A-D and E callbacks, no credentials."""
    from src.server.domain.analytics.review_pipeline import config
    from src.server.domain.analytics.review_pipeline.runner import run_review

    example_path = HERE.parent / "fixtures" / "review_document" / "example.json"
    example = json.loads(example_path.read_text(encoding="utf-8"))
    commander = example["meta"]["commanders"][0]
    ctx = SimpleNamespace(
        deck_id="offline-eval-fixture", deck_name=example["meta"]["deck_name"],
        commanders=(commander["name"],), fact_pack_hash=example["meta"]["fact_pack_hash"],
        estimated_bracket=example["bracket"]["estimated"], target_bracket=example["bracket"]["target"],
        bracket_reasons=tuple(example["bracket"]["reasons"]),
    )
    verified = {key: copy.deepcopy(example[key]) for key in
                ("verdict", "strengths", "problems", "swaps", "cuts_to_consider", "top_changes", "omitted")}
    verified["axes"] = copy.deepcopy(example["scorecard"])
    fact_pack = {"charts": copy.deepcopy(example["charts"]), "cards": [commander]}
    writer_output = {
        "verdict": {**copy.deepcopy(verified["verdict"]), "headline": "steady review", "summary": "clear plan."},
        "deck_identity": {"what_it_does": "this deck builds a clear plan.", "win_paths": ["combat"],
                          "success_criteria": [], "intentional_quirks": []},
        "mana": {"summary": "mana is adequate.", "issues": []},
        "questions_for_owner": [],
    }
    verified["verdict"] = copy.deepcopy(writer_output["verdict"])

    def run_passes(*_args, **_kwargs):
        return SimpleNamespace(verified=verified, trace=None)

    document = run_review(ctx=ctx, fact_pack=fact_pack, pool=None, client=None,
                          writer=lambda _verified: json.dumps(writer_output),
                          config=config.PipelineConfig(), run_passes=run_passes)
    print(f"offline review_document pipeline smoke passed ({document['schema_version']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="run deterministic run_review assembly smoke")
    args = parser.parse_args(argv)
    if args.offline:
        try:
            return _offline_smoke()
        except Exception as exc:
            print(f"eval: offline pipeline smoke failed: {exc}", file=sys.stderr)
            return 2
    print("eval: live eval is not configured; use --offline or make eval-gates", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
