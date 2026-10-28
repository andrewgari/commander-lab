"""Tests for the eval harness itself (offline)."""
import json
import os
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
        text = (DECKS / s / "rubric.yaml").read_text()
        assert "# DRAFT" not in text
        # no yaml dependency in requirements.txt: a filled list is a header line followed by "  - " items
        assert "must_mention:\n  - " in text, f"{s}: must_mention must be filled by the owner"
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


def test_make_eval_runs_offline_pipeline_without_model_credentials():
    env = {k: v for k, v in os.environ.items() if not k.startswith("REVIEW_")}
    r = subprocess.run(["make", "eval"], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "offline review_document pipeline smoke passed" in r.stdout
    assert "Traceback" not in r.stderr


def test_eval_compare(tmp_path):
    a = {"generator": "x", "judge": "j", "decks": {"d": {"cost_usd": 1.0, "latency_s": 10, "gate_violations": [], "judge": {"scores": {"specificity": 6}}}}}
    b = json.loads(json.dumps(a)); b["decks"]["d"]["judge"]["scores"]["specificity"] = 8
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(a)); pb.write_text(json.dumps(b))
    r = subprocess.run([sys.executable, "scripts/eval_compare.py", str(pa), str(pb)], capture_output=True, text=True)
    assert r.returncode == 0 and "+2.00" in r.stdout


# ---------------------------------------------------------------- live eval wiring (all offline: fake clients and callbacks)

LIVE_ENV = {
    "REVIEW_MODEL": "openrouter:anthropic/claude-opus-5.5",
    "REVIEW_JUDGE_MODEL": "openrouter:openai/judge-x",
}
# The recorded Saffi run (tests/fixtures/review_pipeline/abcd_saffi.json.gz) was made with these per-pass models; the replay
# client pins every request hash, so the live path must build the exact same requests from the golden deck files.
RECORDED_ENV = {
    **LIVE_ENV,
    "REVIEW_MODEL_A": "openrouter:anthropic/claude-sonnet-5.5", "REVIEW_MODEL_B": "openrouter:anthropic/claude-opus-5.5",
    "REVIEW_MODEL_C": "openrouter:anthropic/claude-opus-5.5", "REVIEW_MODEL_CRITIC": "openrouter:google/gemini-3.1-pro-preview",
}


def _no_keys_env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY") and not k.startswith("REVIEW_")}
    env["INTERACTIONS_SECRETS_DIR"] = str(tmp_path)          # an empty secrets dir: nothing to find on this host either
    env["REVIEW_SPOTON_TOKEN_CMD"] = str(tmp_path / "no-such-spoton-cli") + " ai token"      # spoton-cli / claude may be on this PATH:
    env["REVIEW_CLAUDE_CMD"] = str(tmp_path / "no-such-claude")                                  # never let a unit test reach a real provider
    env.pop("GOOGLE_API_KEY", None)
    env.update(extra)
    return env


def test_live_without_any_key_exits_nonzero_and_says_missing_key(tmp_path):
    r = subprocess.run([sys.executable, "-m", "tests.review_evals.run_eval", "--live"], capture_output=True, text=True,
                       env=_no_keys_env(tmp_path, **LIVE_ENV), timeout=120)
    assert r.returncode != 0
    assert "missing API key" in r.stderr and "OPENROUTER_API_KEY" in r.stderr
    assert "Traceback" not in r.stderr


def test_live_with_nothing_configured_exits_2_and_says_how_to_set_up_every_provider(tmp_path):
    r = subprocess.run(["make", "eval-live"], capture_output=True, text=True, env=_no_keys_env(tmp_path), timeout=120)
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert "live eval is not configured" in r.stderr and "no usable provider" in r.stderr
    for hint in ("REVIEW_SPOTON_TOKEN_CMD", "claude", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        assert hint in r.stderr, hint
    assert "spoton:claude-sonnet-5-5" in r.stderr and "spoton:gpt-5-6-sol" in r.stderr, "the default tiers are named"
    assert "Traceback" not in r.stderr and "eval: live," not in r.stdout, "nothing was spent"


def test_make_eval_live_dry_defaults_use_the_spoton_tiers_with_no_model_env(tmp_path):
    env = {"REVIEW_SPOTON_TOKEN_CMD": f"{sys.executable} -c \"print('t')\""}
    from tests.review_evals import run_eval
    assert run_eval.preflight(env, None, None) == []
    cfg, writer, tier, judge = run_eval.resolve_models(env, None, None)
    assert (tier, judge, writer) == ("spoton:claude-sonnet-5-5", "spoton:gpt-5-6-sol", "spoton:claude-sonnet-5-5")
    assert [cfg.model(p) for p in "ABC"] == ["spoton:claude-sonnet-5-5"] * 3


def test_tier_aliases_planned_and_cheaper_and_explicit_env_wins():
    from tests.review_evals import run_eval
    env = {"REVIEW_SPOTON_TOKEN_CMD": f"{sys.executable} -c \"print('t')\""}
    assert run_eval.resolve_models(env, "cheaper", None)[2] == "spoton:claude-haiku-4-5"
    assert run_eval.resolve_models(env, "planned", None)[2] == "spoton:claude-sonnet-5-5"
    assert run_eval.resolve_models({**env, "REVIEW_MODEL": "spoton:claude-opus-5-5"}, None, None)[2] == "spoton:claude-opus-5-5"
    assert run_eval.resolve_models({**env, "REVIEW_JUDGE_MODEL": "spoton:grok-4-7"}, None, None)[3] == "spoton:grok-4-7"


def test_default_critic_is_a_third_model_family_the_gateway_serves_and_an_explicit_one_wins():
    from src.server.domain.analytics.review_pipeline.config import model_family
    from tests.review_evals import run_eval
    env = {"REVIEW_SPOTON_TOKEN_CMD": f"{sys.executable} -c \"print('t')\""}
    cfg, _, tier, judge = run_eval.resolve_models(env, None, None)
    fams = {model_family(tier), model_family(judge), model_family(cfg.model("critic"))}
    assert cfg.model("critic").startswith("spoton:") and len(fams) == 3, fams
    assert run_eval.resolve_models({**env, "REVIEW_MODEL_CRITIC": "openrouter:google/gemini-x"}, None, None)[0].model("critic") == "openrouter:google/gemini-x"
    assert run_eval.resolve_models({**env, "REVIEW_MODEL": "openrouter:anthropic/claude-x"}, None, None)[0].model("critic").startswith("openrouter:google/")


def test_spoton_specs_are_usable_through_any_provider_in_the_chain_and_say_what_is_missing_otherwise(tmp_path):
    from tests.review_evals import run_eval
    none = {"INTERACTIONS_SECRETS_DIR": str(tmp_path), "REVIEW_SPOTON_TOKEN_CMD": str(tmp_path / "x") + " ai token", "REVIEW_CLAUDE_CMD": str(tmp_path / "y")}
    msg = "\n".join(run_eval.preflight(none, None, None))
    assert "no usable provider for 'spoton:claude-sonnet-5-5'" in msg and "REVIEW_SPOTON_TOKEN_CMD" in msg
    assert run_eval.preflight({**none, "OPENROUTER_API_KEY": "k", "REVIEW_MODEL_CRITIC": "openrouter:google/gemini-x"}, None, None) == [], \
        "OpenRouter alone can serve the generator and the judge (the default critic is gateway-only: mistral has no route elsewhere)"
    only_or = "\n".join(run_eval.preflight({**none, "OPENROUTER_API_KEY": "k"}, None, None))
    assert "provider 'spoton' is not set up" in only_or, "the gateway-only critic is reported, not silently rerouted"
    only_claude = {**none, "REVIEW_CLAUDE_CMD": sys.executable}
    assert any("spoton:gpt-5-6-sol" in p for p in run_eval.preflight(only_claude, None, None)), "the subscription cannot serve the judge"


def test_gateway_models_carry_list_prices_so_cost_is_never_blank_and_user_prices_win():
    from src.server.domain.analytics.review_pipeline import config as cfg, llm
    from tests.review_evals import run_eval
    merged = run_eval.with_default_prices(cfg.PipelineConfig())
    meter = run_eval.MeteredClient(type("C", (), {"complete": lambda self, r: llm.LLMResponse(text="{}", usage=llm.Usage(1_000_000, 1_000_000))})(), merged.prices)
    meter.complete(llm.LLMRequest(model="spoton:claude-haiku-4-5", system_blocks=(), messages=(), schema_name="s", schema={}))
    assert meter.snapshot()["cost_usd"] == 6.0 and meter.snapshot()["cost_complete"]          # $1 in + $5 out per 1M tokens
    own = cfg.PipelineConfig(prices={"spoton:claude-haiku-4-5": {"in": 9, "out": 9}})
    assert run_eval.with_default_prices(own).prices["spoton:claude-haiku-4-5"] == {"in": 9, "out": 9}
    assert "spoton:gpt-5-6-sol" in run_eval.with_default_prices(own).prices


class LabelReplay:
    """Serves the recorded Saffi replies by call label, ignoring the model in the request (the fallback rewrites it)."""

    def __init__(self, replies):
        self.queues = {}
        for r in replies:
            self.queues.setdefault(r["label"], []).append(r)

    def complete(self, request):
        from src.server.domain.analytics.review_pipeline import llm
        r = self.queues[request.label].pop(0)
        return llm.LLMResponse(text=r["text"], usage=llm.Usage.from_dict(r["usage"]), model=request.model, latency_ms=r["latency_ms"])


def _run_with_providers(tmp_path, monkeypatch, providers, capsys, extra_env=None):
    from src.server.infra.llm import provider
    from tests.review_evals import run_eval
    rec = _recorded_saffi()
    monkeypatch.setattr(provider.RoutingClient, "from_env", classmethod(lambda cls, env=None: cls(providers(LabelReplay(rec["replies"])))))
    env = {"REVIEW_SPOTON_TOKEN_CMD": f"{sys.executable} -c \"print('t')\"", "REVIEW_CLAUDE_CMD": sys.executable, **(extra_env or {})}
    rc = run_eval.main(["--live", "--decks", "saffi-rumors", "--out", str(tmp_path)], env=env,
                       fetch_sources=lambda deck, profile: (rec["fact_pack"], rec["pool"]), writer=_golden_writer,
                       judge_complete=lambda m, p: json.dumps({**{d: 7 for d in judge.DIMENSIONS}, "notes": ""}))
    return rc, json.loads((tmp_path / "results.json").read_text())["decks"]["saffi-rumors"], capsys.readouterr()


def test_a_dead_gateway_falls_back_in_order_and_each_deck_logs_which_provider_served(tmp_path, monkeypatch, capsys):
    from src.server.infra.llm import provider
    down = provider.ProviderUnavailable("HTTP 401: unauthorized", status=401)
    unavailable = type("Down", (), {"complete": lambda self, r: (_ for _ in ()).throw(down)})()
    rc, deck, out = _run_with_providers(tmp_path, monkeypatch, lambda replay: {"spoton": unavailable, "claude": replay, "openrouter": replay}, capsys,
                                      extra_env={"REVIEW_MODEL_CRITIC": "openrouter:x-ai/grok-4.7", "OPENROUTER_API_KEY": "k"})     # the gateway-only default critic has no other route
    assert rc == 0 and deck["status"] == "ok", deck.get("error")
    served = deck["providers"]["served"]
    assert set(served) == {"claude:claude-sonnet-5-5", "openrouter:x-ai/grok-4.7"}, served    # Claude generators on the subscription, critic on OpenRouter
    assert [f["provider"] for f in deck["providers"]["failed"]] == ["spoton"], "the dead gateway is tried once, not once per call"
    assert "401" in deck["providers"]["failed"][0]["reason"]
    assert "claude:claude-sonnet-5-5" in out.out and "spoton" in out.out, "the per-deck summary line names the providers"


def test_a_healthy_gateway_serves_everything_and_nothing_falls_back(tmp_path, monkeypatch, capsys):
    rc, deck, _ = _run_with_providers(tmp_path, monkeypatch, lambda replay: {"spoton": replay, "claude": replay, "openrouter": replay}, capsys)
    assert rc == 0 and deck["providers"]["failed"] == []
    assert set(deck["providers"]["served"]) == {"spoton:claude-sonnet-5-5", "spoton:mistral-large-3"}


def test_every_provider_unreachable_fails_the_deck_naming_each_and_exits_1(tmp_path, monkeypatch, capsys):
    from src.server.infra.llm import provider
    class Down:
        def __init__(self, status): self.status = status
        def complete(self, r): raise provider.ProviderUnavailable(f"HTTP {self.status}: nope", status=self.status)
    rc, deck, _ = _run_with_providers(tmp_path, monkeypatch, lambda replay: {"spoton": Down(401), "claude": Down(None)}, capsys)
    assert rc == 1 and deck["status"] == "failed"
    assert "spoton" in deck["error"] and "claude" in deck["error"] and "openrouter" in deck["error"]


def test_live_refuses_judge_equal_to_generator_before_spending_anything(tmp_path, capsys):
    from tests.review_evals import run_eval
    env = {**LIVE_ENV, "REVIEW_JUDGE_MODEL": LIVE_ENV["REVIEW_MODEL"].upper(), "OPENROUTER_API_KEY": "k"}
    assert run_eval.main(["--live"], env=env) == 2
    assert "must differ" in capsys.readouterr().err


def test_live_refuses_same_model_reached_through_a_different_route(capsys):
    from tests.review_evals import run_eval
    env = {**LIVE_ENV, "REVIEW_MODEL": "anthropic:claude-sonnet-5.5",
           "REVIEW_JUDGE_MODEL": "openrouter:anthropic/claude-sonnet-5.5",
           "OPENROUTER_API_KEY": "k", "ANTHROPIC_API_KEY": "k"}
    assert run_eval.main(["--live"], env=env) == 2
    assert "must differ from every generator model" in capsys.readouterr().err


def test_live_refuses_judge_of_the_generators_family(capsys):
    from tests.review_evals import run_eval
    env = {**LIVE_ENV, "REVIEW_MODEL": "openrouter:anthropic/claude-opus-5.5",
           "REVIEW_JUDGE_MODEL": "anthropic:claude-opus-5", "OPENROUTER_API_KEY": "k", "ANTHROPIC_API_KEY": "k"}
    assert run_eval.main(["--live"], env=env) == 2
    err = capsys.readouterr().err
    assert "same family (anthropic)" in err and "different model family" in err


def test_live_refuses_judge_of_the_critics_family(capsys):
    from tests.review_evals import run_eval
    env = {**LIVE_ENV, "REVIEW_JUDGE_MODEL": "openrouter:google/gemini-3-flash", "OPENROUTER_API_KEY": "k"}
    assert run_eval.main(["--live"], env=env) == 2
    assert "Pass D critic" in capsys.readouterr().err


def test_judge_independence_accepts_a_third_family():
    from tests.review_evals import run_eval
    gens = {"A": "anthropic:claude-x", "B": "openrouter:anthropic/claude-y", "E": "anthropic:claude-x"}
    assert run_eval._judge_independence_problems("openrouter:openai/gpt-x", gens, "openrouter:google/gemini-x") == []


def test_live_refuses_critic_of_the_generators_family(capsys):
    from tests.review_evals import run_eval
    env = {**LIVE_ENV, "REVIEW_MODEL_CRITIC": "openrouter:anthropic/claude-x", "OPENROUTER_API_KEY": "k"}
    assert run_eval.main(["--live"], env=env) == 2
    assert "different model family" in capsys.readouterr().err


def test_live_needs_only_the_keys_of_the_providers_in_use(tmp_path, capsys):
    from tests.review_evals import run_eval
    env = {**LIVE_ENV, "REVIEW_MODEL": "anthropic:claude-opus-5-5", "REVIEW_MODEL_CRITIC": "openrouter:google/gemini-x",
           "OPENROUTER_API_KEY": "k", "INTERACTIONS_SECRETS_DIR": str(tmp_path)}
    assert run_eval.main(["--live", "--decks", "saffi-rumors"], env=env) == 2
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY" in err and "OPENROUTER_API_KEY" not in err


def test_live_key_may_come_from_a_secret_file(tmp_path, capsys):
    from tests.review_evals import run_eval
    (tmp_path / "OPENROUTER_API_KEY").write_text("from-file\n")
    problems = run_eval.preflight({**LIVE_ENV, "INTERACTIONS_SECRETS_DIR": str(tmp_path)}, tier=None, judge=None)
    assert problems == []


def test_make_eval_live_target_exists_and_offline_make_eval_is_unchanged():
    text = (Path(__file__).resolve().parents[1] / "Makefile").read_text()
    assert "eval-live:" in text and "--live" in text and "python -m tests.review_evals.run_eval --offline" in text


def _golden_writer(verified):
    """A deterministic Pass E: restates the verified verdict and the brief, adds no names or numbers."""
    return json.dumps({
        "verdict": {**verified["verdict"], "headline": "steady review", "summary": "clear plan."},
        "deck_identity": {"what_it_does": "this deck builds a clear plan.", "win_paths": ["combat"],
                          "success_criteria": [], "intentional_quirks": []},
        "mana": {"summary": "mana is adequate.", "issues": []},
        "questions_for_owner": []})


def _recorded_saffi():
    import gzip
    root = Path(__file__).resolve().parents[1]
    with gzip.open(root / "tests/fixtures/review_pipeline/abcd_saffi.json.gz", "rt", encoding="utf-8") as g:
        return json.load(g)


def _run_saffi(tmp_path, *, writer=_golden_writer, judge_scores=None):
    from tests.review_evals import run_eval
    from tests.test_review_pipeline_verify_replay import Replay
    rec = _recorded_saffi()
    replay = Replay(rec["replies"])
    judged = []
    scores = judge_scores or {d: 7 for d in judge.DIMENSIONS}

    def judge_complete(model, prompt):
        judged.append((model, prompt))
        return json.dumps({**scores, "notes": "ok"})

    rc = run_eval.main(["--live", "--decks", "saffi-rumors", "--out", str(tmp_path)], env=RECORDED_ENV, client=replay,
                       fetch_sources=lambda deck, profile: (rec["fact_pack"], rec["pool"]), writer=writer,
                       judge_complete=judge_complete)
    return rc, replay, judged, rec


def test_golden_deck_runs_through_run_review_end_to_end_with_fake_callbacks(tmp_path):
    from jsonschema import Draft202012Validator, FormatChecker
    from tests.review_document_refs import load_schema
    rc, replay, judged, rec = _run_saffi(tmp_path)
    assert len(replay.seen) == len(rec["replies"]), "every recorded Pass A-D call was made by the live path"
    results = json.loads((tmp_path / "results.json").read_text())
    deck = results["decks"]["saffi-rumors"]
    assert deck["status"] == "ok", deck.get("error")
    review = json.loads((tmp_path / "saffi-rumors.review.json").read_text())
    assert review["schema_version"] == "review_document.v1"
    assert review["meta"]["deck_id"] == "archidekt:417219"          # from the golden deck's intent.json
    assert list(Draft202012Validator(load_schema(), format_checker=FormatChecker()).iter_errors(review)) == []
    assert deck["gate_violations"] == [] and deck["gates_checked"] > 0 and rc == 0
    assert deck["cost_usd"] and deck["latency_s"] is not None and deck["calls"] == len(rec["replies"])
    assert deck["judge"]["scores"]["rubric_recall"] == 7 and deck["rubric_recall"] == 7
    assert results["generator"] == RECORDED_ENV["REVIEW_MODEL"] and results["judge"] == RECORDED_ENV["REVIEW_JUDGE_MODEL"]
    model, prompt = judged[0]
    assert model == RECORDED_ENV["REVIEW_JUDGE_MODEL"]
    assert "Karmic Guide" in prompt, "the judge is shown the golden rubric"


def test_live_review_carries_a_verdict_the_pipeline_derived_from_the_scorecard(tmp_path):
    _run_saffi(tmp_path)
    review = json.loads((tmp_path / "saffi-rumors.review.json").read_text())
    scores = [a["score"] for a in review["scorecard"]]
    mean = sum(scores) / len(scores)
    want = "A" if mean >= 8.5 else "B" if mean >= 7 else "C" if mean >= 5.5 else "D" if mean >= 4 else "F"
    assert review["verdict"]["grade"] == want
    assert json.loads((tmp_path / "results.json").read_text())["decks"]["saffi-rumors"]["verdict_source"] == "eval-bridge"


def test_a_failing_deck_is_reported_not_swallowed_and_exits_nonzero(tmp_path):
    rc, *_ = _run_saffi(tmp_path, writer=lambda verified: "not json at all")
    deck = json.loads((tmp_path / "results.json").read_text())["decks"]["saffi-rumors"]
    assert rc == 1 and deck["status"] == "failed" and deck["error"]
    assert deck["cost_usd"], "money already spent on A-D is still reported"
    assert not (tmp_path / "saffi-rumors.review.json").exists()


def test_judge_failure_does_not_lose_the_review(tmp_path):
    from tests.review_evals import run_eval
    from tests.test_review_pipeline_verify_replay import Replay
    rec = _recorded_saffi()
    rc = run_eval.main(["--live", "--decks", "saffi-rumors", "--out", str(tmp_path)], env=RECORDED_ENV, client=Replay(rec["replies"]),
                       fetch_sources=lambda deck, profile: (rec["fact_pack"], rec["pool"]), writer=_golden_writer,
                       judge_complete=lambda model, prompt: "oops")
    deck = json.loads((tmp_path / "results.json").read_text())["decks"]["saffi-rumors"]
    assert deck["status"] == "ok" and deck["judge"] is None and "score JSON" in deck["judge_error"]
    assert (tmp_path / "saffi-rumors.review.json").exists() and rc == 0


def test_unknown_deck_slug_is_a_usage_error(capsys):
    from tests.review_evals import run_eval
    env = {**LIVE_ENV, "OPENROUTER_API_KEY": "k"}
    assert run_eval.main(["--live", "--decks", "nope"], env=env, client=object()) == 2
    assert "nope" in capsys.readouterr().err


def test_results_file_is_readable_by_eval_compare(tmp_path):
    _run_saffi(tmp_path)
    r = subprocess.run([sys.executable, "scripts/eval_compare.py", str(tmp_path / "results.json"), str(tmp_path / "results.json")],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "saffi-rumors" in r.stdout and "rubric_recall" in r.stdout


def test_live_writer_path_sends_the_checked_pass_e_prompt_through_the_client(tmp_path):
    """No injected writer: Pass E goes through the same client, with the unchanged e_writer prompt and the verified JSON only."""
    from src.server.domain.analytics.review_pipeline import llm
    from tests.review_evals import run_eval
    from tests.test_review_pipeline_verify_replay import Replay

    rec = _recorded_saffi()
    replay = Replay(rec["replies"])
    seen = []

    class WithWriter:
        def complete(self, request):
            if request.label == "E":
                seen.append(request)
                reply = json.loads(_golden_writer(json.loads(request.messages[0].content.split("\n\n", 1)[1].split("\n\nVoice")[0])))
                return llm.LLMResponse(text=json.dumps(reply), usage=llm.Usage(tokens_in=100, tokens_out=50, cost_usd=0.02),
                                       model=request.model, latency_ms=5)
            return replay.complete(request)

    rc = run_eval.main(["--live", "--decks", "saffi-rumors", "--out", str(tmp_path)], env=RECORDED_ENV, client=WithWriter(),
                       fetch_sources=lambda deck, profile: (rec["fact_pack"], rec["pool"]),
                       judge_complete=lambda m, p: json.dumps({**{d: 6 for d in judge.DIMENSIONS}, "notes": ""}))
    assert rc == 0, (tmp_path / "results.json").read_text()
    assert len(seen) == 1 and seen[0].model == RECORDED_ENV["REVIEW_MODEL"]
    assert "Pass E" in seen[0].system_blocks[0] and "VERIFIED PASS D JSON" in seen[0].system_blocks[0]
    sent = seen[0].messages[0].content
    assert '"sidecar"' not in sent and '"log"' not in sent and '"swap_meta"' not in sent, "only verified sections reach the writer"
    deck = json.loads((tmp_path / "results.json").read_text())["decks"]["saffi-rumors"]
    assert deck["calls"] == len(rec["replies"]) + 1, "Pass E's call is counted in cost/latency, not only A-D"
    review = json.loads((tmp_path / "saffi-rumors.review.json").read_text())
    assert review["meta"]["models"]["E"] == RECORDED_ENV["REVIEW_MODEL"]


def test_owner_questions_come_from_pass_a_not_from_the_writer(tmp_path):
    _run_saffi(tmp_path)
    review = json.loads((tmp_path / "saffi-rumors.review.json").read_text())
    a_reply = json.loads(next(r["text"] for r in _recorded_saffi()["replies"] if r["label"] == "A"))
    assert [q["question"] for q in review["questions_for_owner"]] == [q["question"] for q in a_reply["questions_for_owner"]]
    assert all(set(q) == {"id", "question", "why_asked"} for q in review["questions_for_owner"])
    assert [q["id"] for q in review["questions_for_owner"]] == [f"q-{i}" for i in range(1, len(review["questions_for_owner"]) + 1)]


def test_derive_verdict_grade_boundaries_and_confidence():
    from tests.review_evals.run_eval import derive_verdict
    ax = lambda *s: [{"score": x} for x in s]
    f = [{"confidence": 0.5}, {"confidence": 1.0}]
    assert derive_verdict(ax(9, 8), f) == {"grade": "A", "confidence": 0.75}       # mean 8.5 -> A (boundary is inclusive)
    assert derive_verdict(ax(7, 7), f)["grade"] == "B" and derive_verdict(ax(8, 8), f)["grade"] == "B"
    assert derive_verdict(ax(5, 6), f)["grade"] == "C" and derive_verdict(ax(4, 4), f)["grade"] == "D"
    assert derive_verdict(ax(3, 3), f)["grade"] == "F" and derive_verdict(ax(5, 5), [])["confidence"] == 0.5
    with pytest.raises(Exception, match="no scorecard"):
        derive_verdict([], f)


def test_metered_client_totals_calls_latency_and_cost_and_flags_unpriced_calls():
    from src.server.domain.analytics.review_pipeline import llm
    from tests.review_evals.run_eval import MeteredClient

    class C:
        def __init__(self, costs):
            self.costs = list(costs)

        def complete(self, request):
            return llm.LLMResponse(text="{}", usage=llm.Usage(tokens_in=1000, tokens_out=0, cost_usd=self.costs.pop(0)),
                                   model="m", latency_ms=250)

    req = llm.LLMRequest(model="p:m", system_blocks=(), messages=(), schema_name="s", schema={})
    m = MeteredClient(C([0.5, 0.25]))
    m.complete(req); m.complete(req)
    assert m.snapshot() == {"calls": 2, "api_latency_s": 0.5, "cost_usd": 0.75, "cost_complete": True}
    m = MeteredClient(C([0.5, None]))
    m.complete(req); m.complete(req)
    snap = m.snapshot()
    assert snap["cost_usd"] == 0.5 and snap["cost_complete"] is False, "an unpriced call makes the total a floor, never a guess"
    m = MeteredClient(C([None]))
    m.complete(req)
    assert m.snapshot()["cost_usd"] is None


def test_gates_flag_a_review_for_a_different_deck():
    """The deterministic gates run over the assembled document: a document for another deck must produce violations."""
    from tests.review_evals import run_eval
    rec = _recorded_saffi()
    deck = run_eval.load_deck("saffi-rumors")
    ctx = run_eval.build_context(deck, rec["fact_pack"], rec["pool"], run_eval.load_profile())
    doc = json.loads((Path(__file__).resolve().parents[1] / "tests/fixtures/review_document/example.json").read_text())
    violations = run_eval.run_document_gates(doc, deck, ctx, rec["fact_pack"], rec["pool"])
    assert any("hallucinated_card_names" in v for v in violations) and any("charts_mismatch_fact_pack" in v for v in violations)


def test_load_deck_reads_every_golden_deck_from_disk():
    from tests.review_evals import run_eval
    for slug in run_eval.deck_slugs():
        deck = run_eval.load_deck(slug)
        assert deck["commanders"] and deck["meta"]["deck_id"].startswith("archidekt:") and deck["rubric"]["must_mention"]
        assert sum(q for _, q in deck["deck_cards"]) in (99, 100)


def test_a_gate_violation_is_reported_per_deck_and_fails_the_run(tmp_path, monkeypatch, capsys):
    """The review is kept and its violation is named, but the run exits 1: a violating review is not a pass."""
    from src.server.domain.analytics.review_pipeline import checks
    from src.server.domain.analytics.review_pipeline.checks import Violation

    monkeypatch.setitem(checks.CHECKS, "always_fails", lambda doc, ctx: [Violation("always_fails", "injected for the test")])
    rc, *_ = _run_saffi(tmp_path)
    deck = json.loads((tmp_path / "results.json").read_text())["decks"]["saffi-rumors"]
    assert rc == 1 and deck["status"] == "ok"
    assert deck["gate_violations"] == ["[always_fails] injected for the test"]
    assert (tmp_path / "saffi-rumors.review.json").exists()
    assert "injected for the test" in capsys.readouterr().err
