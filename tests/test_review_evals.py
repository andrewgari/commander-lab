"""Tests for the eval harness itself (offline)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.review_evals import judge
from tests.review_evals.gates import GATES, run_gates
from tests.review_evals.run_gates import FIX, load_case

DECKS = Path(__file__).parent / "review_evals" / "decks"
BAD = sorted((FIX / "bad").glob("*.json"))


def test_good_fixture_has_zero_violations():
    case, ctx = load_case(FIX / "good" / "case.json")
    assert run_gates(case["review"], ctx) == []


def test_every_gate_has_a_failing_fixture():
    assert {json.loads(f.read_text())["expect_gate"] for f in BAD} == set(GATES)


@pytest.mark.parametrize("path", BAD, ids=lambda p: p.stem)
def test_bad_fixture_names_its_gate(path):
    case, ctx = load_case(path)
    assert case["expect_gate"] in {v.gate for v in run_gates(case["review"], ctx)}


def test_golden_decks_complete():
    slugs = {"darien-frozen-throne", "celes-ebbing-away", "lara-uncharted", "saffi-rumors"}
    assert {p.name for p in DECKS.iterdir()} == slugs
    for s in slugs:
        lines = [l for l in (DECKS / s / "decklist.txt").read_text().splitlines() if l and not l.startswith("#")]
        assert sum(int(l.split(" ", 1)[0]) for l in lines) in (99, 100)
        assert "must_mention: []" in (DECKS / s / "rubric.yaml").read_text()
        json.loads((DECKS / s / "intent.json").read_text())


def test_judge_refuses_same_model():
    with pytest.raises(judge.JudgeConfigError, match="differ"):
        judge.resolve_models("m-1", "M-1")
    with pytest.raises(judge.JudgeConfigError):
        judge.resolve_models("m-1", None)


def test_judge_scores_with_injected_client():
    reply = json.dumps({d: 7 for d in judge.DIMENSIONS})
    out = judge.judge_review({}, {}, {}, generator="a", judge="b", complete=lambda m, p: reply)
    assert out["judge_model"] == "b" and out["scores"]["generic_filler"] == 7


def test_make_eval_fails_cleanly_without_pipeline():
    r = subprocess.run([sys.executable, "-m", "tests.review_evals.run_eval"], capture_output=True, text=True)
    assert r.returncode == 2 and "pipeline does not exist yet" in r.stderr


def test_eval_compare(tmp_path):
    a = {"generator": "x", "judge": "j", "decks": {"d": {"cost_usd": 1.0, "latency_s": 10, "gate_violations": [], "judge": {"scores": {"specificity": 6}}}}}
    b = json.loads(json.dumps(a)); b["decks"]["d"]["judge"]["scores"]["specificity"] = 8
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(a)); pb.write_text(json.dumps(b))
    r = subprocess.run([sys.executable, "scripts/eval_compare.py", str(pa), str(pb)], capture_output=True, text=True)
    assert r.returncode == 0 and "+2.00" in r.stdout
