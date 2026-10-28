"""Deck-reviewer eval runner: an offline pipeline smoke, and the live golden-deck eval.

    python -m tests.review_evals.run_eval --offline          # `make eval`: deterministic, no credentials, no network
    python -m tests.review_evals.run_eval --live             # `make eval-live`: real model calls, costs money
    python -m tests.review_evals.run_eval --live --tier cheaper --decks saffi-rumors

``--live`` runs every deck in ``tests/review_evals/decks/*`` through ``run_review`` (Passes A-E) with live provider calls.
Per deck it builds the fact pack and candidate pool from ``decklist.txt`` (Scryfall, EDHREC, Commander Spellbook), the
ReviewContext from those plus ``intent.json`` and the profile seed, then writes the review JSON, trace, gate results, cost,
latency and judge scores to ``--out`` (default ``tests/review_evals/results/<timestamp>-<tier>/``, git-ignored).
``rubric_recall`` is the judge's score for that dimension against ``rubric.yaml``. ``results.json`` is the file
``scripts/eval_compare.py`` diffs.

Configuration is environment only; nothing secret is read from, or written to, the repo. With nothing set, ``--live`` uses
the SpotOn gateway and needs no pasted key:

    planned tier (generator)   spoton:claude-sonnet-5-5        --tier planned   (the default)
    cheaper tier               spoton:claude-haiku-4-5         --tier cheaper
    judge                      spoton:gpt-5-6-sol              (a different vendor family from every generator)
    Pass D critic              spoton:grok-4-7                 (a third family; D3 needs one that is not a generator's)

    REVIEW_MODEL          generator tier, ``<provider>:<model id>`` (``--tier`` overrides; ``planned``/``cheaper`` are aliases):
                          Passes A, B, C and the Pass E writer. REVIEW_MODEL_A/B/C/E override one pass; REVIEW_MODEL_CRITIC
                          sets the Pass D critic. The rest is ``review_pipeline.config``.
    REVIEW_JUDGE_MODEL    judge, ``<provider>:<model id>``; must differ from every generator model.
    REVIEW_SPOTON_URL / REVIEW_SPOTON_TOKEN_CMD
                          the SpotOn gateway base URL and the command that prints its short-lived bearer token
                          (default ``spoton-cli ai token``, fetched at run time, never stored).
    REVIEW_CLAUDE_CMD     the Claude CLI used for the subscription fallback (default ``claude``).
    GEMINI_API_KEY / OPENROUTER_API_KEY / ANTHROPIC_API_KEY
                          one per provider in use, as an env var or a file in $INTERACTIONS_SECRETS_DIR
                          (default /run/commander-lab-secrets, the Docker secret mount).

A ``spoton:<model>`` spec falls back when SpotOn is unreachable, rejects the token, or has no route for the model: to the Claude
subscription (Claude models), then Gemini (Gemini models), then OpenRouter, in that order, skipping providers that are not set up
here. Each deck's row in ``results.json`` (``providers``) and its summary line say which provider served its calls and which
failed. A spec with an explicit provider (``openrouter:...``, ``anthropic:...``) is never rerouted.

Configuration and keys are checked before the first model call, so a misconfigured run spends nothing. Exit codes:
0 every deck produced a review with zero gate violations, 1 a deck failed or violated a gate, 2 bad configuration or usage.

Pass D's output has no verdict, and the Pass E checker requires one (grade and confidence must be preserved exactly), so the
live path derives it from the verified scorecard (``derive_verdict``) and records ``verdict_source: eval-bridge`` in the
results. The pipeline itself is unchanged; producing a verdict in production is a separate decision.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
DECKS_DIR = HERE / "decks"
RESULTS_DIR = HERE / "results"
PROFILE_SEED = ROOT / "tests" / "fixtures" / "review_pipeline" / "profile_seed.json"
DEFAULT_SECRETS_DIR = "/run/commander-lab-secrets"
INTENT_META_KEYS = ("deck_id", "deck_name", "commanders", "TODO")     # intent.json keys that are not UserDeckIntent fields

# Eval-bridge verdict: the grade is the mean 1-10 scorecard score mapped through these floors (descending).
GRADE_FLOORS = (("A", 8.5), ("B", 7.0), ("C", 5.5), ("D", 4.0))


class LiveEvalError(RuntimeError):
    """A deck could not be evaluated (degraded inputs, bad deck files). Reported per deck; the run continues."""


# --------------------------------------------------------------------------------------------------- offline smoke

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




# ------------------------------------------------------------------------------------------------- configuration

def _providers(specs: Sequence[str]) -> list[str]:
    return sorted({s.split(":", 1)[0] for s in specs if ":" in s})


DEFAULT_TIERS = {"planned": "spoton:claude-sonnet-5-5", "cheaper": "spoton:claude-haiku-4-5"}
DEFAULT_TIER = DEFAULT_TIERS["planned"]
DEFAULT_JUDGE = "spoton:gpt-5-6-sol"
DEFAULT_SPOTON_CRITIC = "spoton:grok-4-7"
# USD per 1M tokens. List prices of the underlying models (OpenRouter's published rates), used only when the provider does not
# report cost itself, so a gateway run has a cost estimate. Override per spec with REVIEW_MODEL_PRICES.
DEFAULT_PRICES = {
    "spoton:claude-sonnet-5-5": {"in": 2.0, "out": 10.0, "cache_read": 0.2, "cache_write": 2.5},
    "spoton:claude-haiku-4-5": {"in": 1.0, "out": 5.0, "cache_read": 0.1, "cache_write": 1.25},
    "spoton:claude-opus-5-5": {"in": 5.0, "out": 25.0, "cache_read": 0.5, "cache_write": 6.25},
    "spoton:gpt-5-6-sol": {"in": 4.0, "out": 20.0, "cache_read": 0.4, "cache_write": 5.0},
    "spoton:grok-4-7": {"in": 2.0, "out": 6.0, "cache_read": 0.5},
}


def with_default_prices(config: Any) -> Any:
    import dataclasses

    return dataclasses.replace(config, prices={**DEFAULT_PRICES, **dict(config.prices)})


def _env_models(env: Mapping[str, str], tier: str | None):
    """(PipelineConfig, writer model): the tier fills Passes A, B, C unless that pass is set explicitly. On the SpotOn gateway
    the Pass D critic defaults to a third model family (the pipeline default needs an OpenRouter key)."""
    from src.server.domain.analytics.review_pipeline import config as cfg

    cfg_env = dict(env)
    if tier:
        for p in ("A", "B", "C"):
            if not cfg_env.get(f"REVIEW_MODEL_{p}"):
                cfg_env[f"REVIEW_MODEL_{p}"] = tier
        if tier.startswith("spoton:") and not cfg_env.get("REVIEW_MODEL_CRITIC"):
            cfg_env["REVIEW_MODEL_CRITIC"] = DEFAULT_SPOTON_CRITIC
    config = with_default_prices(cfg.PipelineConfig.from_env(cfg_env))
    return config, env.get("REVIEW_MODEL_E") or tier or config.model("C")


def resolve_models(env: Mapping[str, str], tier: str | None, judge: str | None):
    """(PipelineConfig, writer model, tier, judge) with the defaults applied: ``--tier`` > REVIEW_MODEL > the planned tier."""
    tier = tier or env.get("REVIEW_MODEL") or DEFAULT_TIER
    tier = DEFAULT_TIERS.get(tier.strip().lower(), tier)
    judge = judge or env.get("REVIEW_JUDGE_MODEL") or DEFAULT_JUDGE
    config, writer = _env_models(env, tier if ":" in tier else None)
    return config, writer, tier, judge


def _norm_model(spec: str) -> str:
    """Route-independent model identity: ``anthropic:claude-x`` == ``openrouter:anthropic/claude-x`` == ``spoton:claude-x``."""
    from src.server.domain.analytics.review_pipeline.config import model_family

    model = spec.partition(":")[2].strip().lower().replace(".", "-")
    return model.split("/", 1)[1] if "/" in model and model_family(spec) == model.split("/", 1)[0] else model


def _judge_independence_problems(judge: str, generators: Mapping[str, str], critic: str) -> list[str]:
    """The judge must be a different model *and* a different vendor family than every generator and the critic,
    however each is routed (direct vs OpenRouter), or its scores would grade its own family's work."""
    from src.server.domain.analytics.review_pipeline.config import model_family

    fam = model_family(judge)
    same_model = sorted(p for p, m in generators.items() if m.strip().lower() == judge.strip().lower() or _norm_model(m) == _norm_model(judge))
    same_family = sorted(p for p, m in generators.items() if model_family(m) == fam and p not in same_model)
    out: list[str] = []
    if same_model:
        out.append(f"judge model must differ from every generator model ({judge!r} is also the model for pass "
                   f"{', '.join(same_model)}, possibly via a different route)")
    if same_family:
        out.append(f"judge model {judge!r} is the same family ({fam}) as the generator for pass {', '.join(same_family)}; "
                   f"the judge must come from a different model family")
    if model_family(critic) == fam or _norm_model(critic) == _norm_model(judge):
        out.append(f"judge model {judge!r} must not be the same model family ({fam}) as the Pass D critic {critic!r}")
    return out


KEY_PROVIDERS = ("openrouter", "anthropic", "gemini")


def _spec_provider_problem(spec: str, env: Mapping[str, str]) -> str | None:
    """None when some provider in the spec's fallback chain is set up here, else why the spec cannot run."""
    from src.server.infra.llm import provider

    status = provider.provider_status(env)
    chain = provider.fallback_chain(spec)
    if any(status.get(c.split(":", 1)[0], "unknown provider") is None for c in chain):
        return None
    prov = spec.split(":", 1)[0]
    if len(chain) > 1:
        return f"no usable provider for {spec!r}; tried in order: {', '.join(c.split(':', 1)[0] for c in chain)}"
    if prov in KEY_PROVIDERS:
        return f"missing API key: {prov.upper()}_API_KEY"
    return f"provider {prov!r} is not set up: {status.get(prov, 'unknown provider')}"


def preflight(env: Mapping[str, str], tier: str | None, judge: str | None) -> list[str]:
    """Everything that can be checked without spending money; returns human-readable problems (empty = go)."""
    from src.server.domain.analytics.review_pipeline import config as cfg
    from src.server.infra.llm import provider

    problems: list[str] = []
    tier = tier or env.get("REVIEW_MODEL") or DEFAULT_TIER
    tier = DEFAULT_TIERS.get(tier.strip().lower(), tier)
    judge = judge or env.get("REVIEW_JUDGE_MODEL") or DEFAULT_JUDGE
    if ":" not in tier:
        problems.append(f"REVIEW_MODEL {tier!r} must look like '<provider>:<model id>' (or planned / cheaper)")
    if ":" not in judge:
        problems.append(f"REVIEW_JUDGE_MODEL {judge!r} must look like '<provider>:<model id>'")

    specs: list[str] = []
    try:
        config, writer_model = _env_models(env, tier if ":" in tier else None)
    except (cfg.ConfigError, ValueError) as exc:
        problems.append(f"pipeline model configuration: {exc}")
    else:
        generators = {p: config.model(p) for p in cfg.GENERATOR_PASSES} | {"E": writer_model}
        specs = [*generators.values(), *config.axis_models.values(), config.model("critic")]
        if ":" in judge:
            specs.append(judge)
            problems.extend(_judge_independence_problems(judge, generators, config.model("critic")))
        try:
            config.check_critic_is_independent()
        except cfg.ConfigError as exc:
            problems.append(str(exc))

    unusable = list(dict.fromkeys(p for s in dict.fromkeys(specs) if (p := _spec_provider_problem(s, env))))
    problems.extend(unusable)
    if any(p.startswith("no usable provider") for p in unusable):
        status = provider.provider_status(env)
        problems.append("set up at least one provider (tried in this order): " +
                        "; ".join(f"{n}: {status[n]}" for n in provider.FALLBACK_ORDER if status[n]))
    if unusable:
        problems.append(f"API keys are env vars or files in $INTERACTIONS_SECRETS_DIR (default {DEFAULT_SECRETS_DIR}); "
                        f"keys and tokens are never read from the repo")
    return problems


# ------------------------------------------------------------------------------------------------------ deck files

def deck_slugs() -> list[str]:
    return sorted(p.name for p in DECKS_DIR.iterdir() if p.is_dir())


def parse_decklist(path: Path) -> tuple[list[dict], list[dict]]:
    """``decklist.txt`` -> (commanders, mainboard) as [{"name", "quantity"}]; sections are the ``#`` comment headers."""
    cmd: list[dict] = []
    cards: list[dict] = []
    section = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            section = "cmd" if "Commander" in line and ":" in line else ("main" if "Mainboard" in line else section)
            continue
        m = re.match(r"(\d+)\s+(.+)", line.strip())
        if m:
            (cmd if section == "cmd" else cards).append({"name": m.group(2), "quantity": int(m.group(1))})
    if not cmd:
        raise LiveEvalError(f"{path}: no commander found (expected a '# Commander:' section)")
    return cmd, cards


def load_deck(slug: str) -> dict[str, Any]:
    """A golden deck on disk: decklist.txt, intent.json (plus any UserDeckIntent fields it carries) and rubric.yaml."""
    from src.server.domain.intake.user_intent import UserDeckIntent, parse_user_intent

    try:
        import yaml
    except ImportError as exc:       # not in requirements.txt; the eval CI step installs it
        raise LiveEvalError("PyYAML is required to read rubric.yaml (pip install pyyaml)") from exc
    d = DECKS_DIR / slug
    cmd, cards = parse_decklist(d / "decklist.txt")
    meta = json.loads((d / "intent.json").read_text(encoding="utf-8"))
    fields = {k: v for k, v in meta.items() if k not in INTENT_META_KEYS}
    intent = parse_user_intent(fields, strict_coherence=False) if fields else UserDeckIntent()
    rubric = yaml.safe_load((d / "rubric.yaml").read_text(encoding="utf-8")) or {}
    return {"slug": slug, "meta": meta, "intent": intent, "rubric": rubric, "commanders": cmd, "cards": cards,
            "deck": {"commanders": cmd, "cards": cmd + cards},
            "deck_cards": [(c["name"], c["quantity"]) for c in cmd + cards]}


def load_profile():
    from src.server.domain.profile import player_profile as pp

    return pp.profile_from_seed(json.loads(PROFILE_SEED.read_text(encoding="utf-8")))


def _pool_cards(pool: Any) -> list[dict]:
    return list(pool.get("cards") if isinstance(pool, Mapping) else pool.cards)


def build_context(deck: Mapping[str, Any], fact_pack: Any, pool: Any, profile: Any):
    from src.server.domain.analytics.review_pipeline import context

    return context.ReviewContext.build(
        deck_id=deck["meta"]["deck_id"], deck_name=deck["meta"]["deck_name"], fact_pack=fact_pack,
        deck_cards=deck["deck_cards"], commanders=[c["name"] for c in deck["commanders"]], profile=profile,
        intent=deck["intent"], owned=None, extra_foreign_names=[c["name"] for c in _pool_cards(pool)])


# --------------------------------------------------------------------------------------------------- live sources

_retry_installed = False


def live_fetch_sources(deck: Mapping[str, Any], profile: Any):
    """Fact pack and candidate pool from the real providers. Refuses degraded inputs: a review built on a Scryfall
    rate-limit gap or an empty pool says nothing about the model (same guard as scripts/review_live_abcd.py)."""
    global _retry_installed
    from scripts.review_live_abcd import _retry_on_429
    from src.server.domain.analytics import candidates
    from src.server.domain.analytics.fact_pack import build_fact_pack
    from src.server.domain.analytics.fact_sources import default_fetchers, scryfall_search
    from src.server.domain.analytics.providers.edhrec import EDHRecClient

    if not _retry_installed:
        _retry_on_429()
        _retry_installed = True
    pack = build_fact_pack(deck["deck"])
    fetchers = default_fetchers()
    pool = candidates.candidate_pool(
        deck["deck"], deck["intent"], profile, owned=None,
        sources=candidates.CandidateSources(edhrec=lambda names: EDHRecClient().get_commander_page(list(names)),
                                            scryfall_batch=fetchers["scryfall_batch"], scryfall_search=scryfall_search))
    no_identity = [c["name"] for c in pack["cards"] if isinstance(c.get("color_identity"), dict)]
    if not pool.cards or len(no_identity) > max(6, len(pack["cards"]) // 10):
        raise LiveEvalError(f"degraded inputs: pool has {len(pool.cards)} cards, {len(no_identity)} deck cards lack Scryfall "
                            f"color identity; pool notes: {pool.notes}. Wait for the rate limit to clear and rerun.")
    return pack, pool


# ----------------------------------------------------------------------------------------------- metering / clients

class MeteredClient:
    """Wraps an LLMClient and totals calls, latency and cost, including calls the trace does not see (Pass E, judge)."""

    def __init__(self, inner: Any, prices: Mapping[str, Mapping[str, float]] | None = None):
        self.inner, self.prices = inner, prices or {}
        self.reset()

    def reset(self) -> None:
        self.calls = 0
        self.latency_ms = 0
        self.cost_usd = 0.0
        self.unpriced_calls = 0

    def complete(self, request):
        from src.server.domain.analytics.review_pipeline.trace import _cost

        resp = self.inner.complete(request)
        self.calls += 1
        self.latency_ms += resp.latency_ms or 0
        cost = _cost(resp.usage, request.model, self.prices)
        if cost is None:
            self.unpriced_calls += 1
        else:
            self.cost_usd += cost
        return resp

    def snapshot(self) -> dict[str, Any]:
        priced = self.calls - self.unpriced_calls
        return {"calls": self.calls, "api_latency_s": round(self.latency_ms / 1000, 3),
                "cost_usd": round(self.cost_usd, 6) if priced else None,
                "cost_complete": self.calls > 0 and self.unpriced_calls == 0}


def derive_verdict(axes: Sequence[Mapping[str, Any]], findings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Eval-bridge verdict: grade from the mean scorecard score; confidence is the mean finding confidence (0.5 if none)."""
    if not axes:
        raise LiveEvalError("verified result has no scorecard axes; cannot derive a verdict")
    mean = sum(a["score"] for a in axes) / len(axes)
    grade = next((g for g, floor in GRADE_FLOORS if mean >= floor), "F")
    confs = [f["confidence"] for f in findings if isinstance(f.get("confidence"), (int, float))]
    return {"grade": grade, "confidence": round(sum(confs) / len(confs), 2) if confs else 0.5}


def bridge_questions(result: Any) -> list[dict[str, Any]]:
    return [{k: q[k] for k in ("id", "question", "why_asked")} for q in result.brief["questions_for_owner"]]


def bridge_verified(result: Any, commanders: Sequence[str] = ()) -> dict[str, Any]:
    """The Pass E input: the verified Pass D sections plus what the brief supplies, and nothing else (no trace/log/sidecar).
    The brief's owner questions are NOT included: they are Pass A output, not prose, and the writer checker flags the
    sentence-initial words of a question ("What", "Is") as unverified names. ``attach_questions`` sets them from code."""
    v, brief = asdict(result.verified), result.brief
    cards = list(dict.fromkeys([*commanders, *brief["central_concept"]["key_cards"],
                                *(c for w in brief["win_paths"] for c in w["key_cards"]),
                                *(c for q in brief["intentional_quirks"] for c in q["cards"])]))
    return {
        "verdict": derive_verdict(v["axes"], [*v["strengths"], *v["problems"]]),
        "deck_identity": {"what_it_does": brief["what_it_does"], "win_paths": [w["path"] for w in brief["win_paths"]],
                          "success_criteria": list(brief["success_criteria"]),
                          "intentional_quirks": [q["quirk"] for q in brief["intentional_quirks"]],
                          "key_cards": [{"name": n} for n in cards]},
        "axes": v["axes"], "strengths": v["strengths"], "problems": v["problems"], "swaps": v["swaps"],
        "cuts_to_consider": v["cuts_to_consider"], "top_changes": v["top_changes"], "omitted": v["omitted"],
    }


def attach_questions(review: dict[str, Any], questions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Set ``questions_for_owner`` from Pass A (code-owned, like charts) and re-validate the whole document."""
    from src.server.domain.analytics.review_pipeline.runner import validate_review_document

    return validate_review_document({**review, "questions_for_owner": [dict(q) for q in questions]})


def make_run_passes(sink: dict[str, Any]):
    """``run_passes`` for run_review: the real Passes A-D, then the verdict bridge. ``sink['questions']`` gets Pass A's questions."""
    from src.server.domain.analytics.review_pipeline import runner

    def run_passes(ctx, client, pool, config, on_trace=None):
        result = runner.run_abcd(ctx, client, pool, config, on_trace=on_trace)
        sink["questions"] = bridge_questions(result)
        return SimpleNamespace(verified=bridge_verified(result, ctx.commanders), trace=result.trace)
    return run_passes


_STRING = {"type": "string", "minLength": 1}
WRITER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "deck_identity", "mana", "questions_for_owner"],
    "properties": {
        "verdict": {"type": "object", "additionalProperties": False, "required": ["headline", "summary", "grade", "confidence"],
                    "properties": {"headline": _STRING, "summary": _STRING, "grade": {"enum": ["A", "B", "C", "D", "F"]},
                                   "confidence": {"type": "number", "minimum": 0, "maximum": 1}}},
        "deck_identity": {"type": "object", "additionalProperties": False,
                          "required": ["what_it_does", "win_paths", "success_criteria", "intentional_quirks"],
                          "properties": {"what_it_does": _STRING, "win_paths": {"type": "array", "items": _STRING},
                                         "success_criteria": {"type": "array", "items": _STRING},
                                         "intentional_quirks": {"type": "array", "items": _STRING}}},
        "mana": {"type": "object", "additionalProperties": False, "required": ["summary", "issues"],
                 "properties": {"summary": _STRING, "issues": {"type": "array", "items": _STRING}}},
        "questions_for_owner": {"type": "array", "maxItems": 0, "items": {}},
    },
}
WRITER_FORMAT_NOTE = (
    "Output format for this run: a JSON object with exactly verdict {headline, summary, grade, confidence}, deck_identity "
    "{what_it_does, win_paths[], success_criteria[], intentional_quirks[]} (what_it_does goes inside deck_identity), mana "
    "{summary, issues[]} and questions_for_owner, which you always return as an empty list (the owner questions are attached by "
    "code). Do not add other fields.")
JUDGE_DIMENSIONS = ("rubric_recall", "specificity", "intent_alignment", "actionability", "generic_filler")
JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": [*JUDGE_DIMENSIONS, "notes"],
    "properties": {**{d: {"type": "number", "minimum": 1, "maximum": 10} for d in JUDGE_DIMENSIONS}, "notes": {"type": "string"}},
}


def make_live_writer(client: Any, model: str, review_preferences: str = "") -> Callable[[Mapping[str, Any]], str]:
    """Pass E over the injected client: the checked e_writer prompt (unchanged) plus the output-format note above."""
    from src.server.domain.analytics.review_pipeline import e_writer, prompts
    from src.server.domain.analytics.review_pipeline.llm import LLMRequest, Message
    from src.server.domain.analytics.review_pipeline.structured import portable_schema

    system = prompts.load("e_writer").render("system.role")
    schema = portable_schema(WRITER_SCHEMA)

    def writer(verified: Mapping[str, Any]) -> str:
        user = e_writer.render_user_input(verified, review_preferences) + "\n\n" + WRITER_FORMAT_NOTE
        return client.complete(LLMRequest(
            model=model, system_blocks=(system,), messages=(Message("user", user),), schema_name="review_prose",
            schema=schema, max_tokens=6000, label="E")).text
    return writer


def make_live_judge(client: Any) -> Callable[[str, str], str]:
    from src.server.domain.analytics.review_pipeline.llm import LLMRequest, Message
    from src.server.domain.analytics.review_pipeline.structured import portable_schema

    schema = portable_schema(JUDGE_SCHEMA)

    def complete(model: str, prompt: str) -> str:
        return client.complete(LLMRequest(
            model=model, system_blocks=("You are a strict, calibrated grader of Magic: The Gathering deck reviews.",),
            messages=(Message("user", prompt),), schema_name="judge_scores", schema=schema, max_tokens=2000, label="judge")).text
    return complete


# -------------------------------------------------------------------------------------------------------- gates

def run_document_gates(doc: Mapping[str, Any], deck: Mapping[str, Any], ctx: Any, fact_pack: Any, pool: Any) -> list[str]:
    """Every deterministic check (the same functions as ``make eval-gates``) over the assembled review document."""
    from src.server.domain.analytics.fact_pack import FactPack
    from src.server.domain.analytics.review_pipeline import checks

    def facts(card: Mapping[str, Any]) -> dict[str, Any]:
        ci, leg = card.get("color_identity"), card.get("legalities")
        return {"color_identity": list(ci) if isinstance(ci, list) else [], "legalities": dict(leg) if isinstance(leg, Mapping) else {}}

    scryfall = {c["name"]: facts(c) for c in fact_pack["cards"]}
    pool_cards = _pool_cards(pool)
    scryfall.update({c["name"]: facts(c) for c in pool_cards})
    names: list[str] = []
    for name, qty in deck["deck_cards"]:
        names += [name] * qty
    gdeck = {"commanders": [c["name"] for c in deck["commanders"]], "cards": names, "pet_cards": list(ctx.pet_cards),
             "budget_hard_cap": ctx.hard_cap_usd, "rejected_swaps": [list(p) for p in sorted(ctx.rejected_swaps)],
             "candidate_pool": sorted({c["name"] for c in pool_cards}),
             "game_changers": list(ctx.game_changers) if ctx.game_changers is not None else None}
    fresh = fact_pack if isinstance(fact_pack, FactPack) else FactPack(dict(fact_pack))
    return [str(v) for v in checks.run_checks(dict(doc), checks.CheckContext(deck=gdeck, scryfall=scryfall, fresh_fact_pack=fresh))]


# ------------------------------------------------------------------------------------------------------ live run

def _review_preferences(profile: Any) -> str:
    rp = getattr(profile, "review_preferences", None)
    return ", ".join(str(p) for p in (getattr(rp, "tone", None), *(getattr(rp, "skip", None) or [])) if p)


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def evaluate_deck(deck: Mapping[str, Any], *, client: MeteredClient, judge_meter: MeteredClient, config: Any, writer_model: str,
                  judge_model: str, generator: str, fetch_sources, writer, judge_complete, out: Path, profile: Any) -> dict[str, Any]:
    """One deck. A deck-level problem is a result row (status, error, what it cost), never an exception."""
    from src.server.domain.analytics.review_pipeline import checks, runner
    from tests.review_evals import judge as judge_mod

    slug = deck["slug"]
    row: dict[str, Any] = {"status": "failed", "error": None, "verdict_source": "eval-bridge", "gate_violations": [],
                           "gates_checked": 0, "judge": None, "judge_error": None, "rubric_recall": None, "cost_usd": None,
                           "cost_complete": False, "latency_s": None, "calls": 0, "review_file": None}
    client.reset()
    judge_meter.reset()
    _drain_providers(client)                                   # a fresh provider log per deck
    traces: list = []
    bridge: dict[str, Any] = {}
    try:
        started = time.monotonic()
        fact_pack, pool = fetch_sources(deck, profile)
        row["fetch_s"] = round(time.monotonic() - started, 2)
        ctx = build_context(deck, fact_pack, pool, profile)
        started = time.monotonic()
        try:
            review = runner.run_review(
                ctx=ctx, fact_pack=fact_pack, pool=pool, client=client, config=config, on_trace=traces.append,
                writer=writer or make_live_writer(client, writer_model, _review_preferences(profile)),
                run_passes=make_run_passes(bridge))
            review = attach_questions(review, bridge.get("questions", []))
        finally:
            row["latency_s"] = round(time.monotonic() - started, 2)
    except Exception as exc:                                   # a deck-level failure is a result, not a crash
        row["error"] = f"{type(exc).__name__}: {exc}"[:500]
        trace = getattr(exc, "trace", None) or (traces[-1] if traces else None)
        if trace is not None:
            _write_json(out / f"{slug}.trace.json", trace.to_dict())
        row.update(client.snapshot())
        row["providers"] = _drain_providers(client)
        return row
    review["meta"]["models"]["E"] = writer_model              # assemble_review_document labels Pass E 'oracle-profile'; this run used this model
    row.update(client.snapshot())
    _write_json(out / f"{slug}.review.json", review)
    row["review_file"] = f"{slug}.review.json"
    if traces:
        _write_json(out / f"{slug}.trace.json", traces[-1].to_dict())
    row["gates_checked"] = len(checks.CHECKS)
    row["gate_violations"] = run_document_gates(review, deck, ctx, fact_pack, pool)
    row["status"] = "ok"
    intent = {k: v for k, v in deck["meta"].items() if k != "TODO"}
    try:
        scored = judge_mod.judge_review(review, deck["rubric"], intent, generator=generator, judge=judge_model,
                                        complete=judge_complete)
        row["judge"], row["rubric_recall"] = scored, scored["scores"]["rubric_recall"]
    except Exception as exc:                                   # the review is kept; only its scoring is missing
        row["judge_error"] = f"{type(exc).__name__}: {exc}"[:300]
    row["judge_cost_usd"] = judge_meter.snapshot()["cost_usd"]
    row["providers"] = _drain_providers(client)
    return row


def _drain_providers(meter: "MeteredClient") -> dict[str, Any] | None:
    """Which provider served this deck's calls, and which failed, when the client is a fallback chain (else None)."""
    drain = getattr(meter.inner, "drain_log", None)
    return drain() if callable(drain) else None


def _providers_note(row: Mapping[str, Any]) -> str:
    log = row.get("providers")
    if not log or not (log["served"] or log["failed"]):
        return ""
    served = ", ".join(f"{spec} x{n}" for spec, n in sorted(log["served"].items())) or "none"
    failed = "; ".join(f"{f['provider']} unavailable ({f['reason'][:80]})" for f in log["failed"])
    return f"  served by: {served}" + (f"  fell back: {failed}" if failed else "")


def _money(v: float | None) -> str:
    return "$?" if v is None else f"${v:.4f}"


def _summary_line(slug: str, row: Mapping[str, Any]) -> str:
    if row["status"] != "ok":
        return f"{slug}: FAILED  {row['error']}  (spent {_money(row.get('cost_usd'))}){_providers_note(row)}"
    recall = "n/a" if row["rubric_recall"] is None else f"{row['rubric_recall']:g}/10"
    return (f"{slug}: ok  {len(row['gate_violations'])} gate violation(s) of {row['gates_checked']} checks  "
            f"cost {_money(row['cost_usd'])}{'' if row['cost_complete'] else ' (floor)'}  latency {row['latency_s']}s  "
            f"rubric recall {recall}{_providers_note(row)}")


def run_live(args: argparse.Namespace, env: Mapping[str, str], *, client=None, fetch_sources=None, writer=None,
             judge_complete=None) -> int:
    slugs = args.decks or deck_slugs()
    unknown = [s for s in slugs if s not in deck_slugs()]
    if unknown:
        print(f"eval: unknown deck(s) {', '.join(unknown)}; available: {', '.join(deck_slugs())}", file=sys.stderr)
        return 2
    config, writer_model, tier, judge_model = resolve_models(env, args.tier, None)
    problems = preflight(env, tier, judge_model)
    if client is not None:                                   # an injected client carries its own credentials
        problems = [p for p in problems if not p.startswith(("missing API key", "no usable provider", "provider ", "set up at least", "API keys are"))]
    if problems:
        print("eval: live eval is not configured:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2
    if client is None:
        from src.server.infra.llm.provider import FallbackClient, RoutingClient
        client = FallbackClient(RoutingClient.from_env(env))
    metered, judge_meter = MeteredClient(client, config.prices), MeteredClient(client, config.prices)
    judge_complete = judge_complete or make_live_judge(judge_meter)
    fetch_sources = fetch_sources or live_fetch_sources
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or RESULTS_DIR / f"{stamp}-{re.sub(r'[^A-Za-z0-9._-]+', '_', tier)}"
    out.mkdir(parents=True, exist_ok=True)
    profile = load_profile()
    print(f"eval: live, generator {tier}, judge {judge_model}, critic {config.model('critic')}, {len(slugs)} deck(s) -> {out}")
    results: dict[str, Any] = {
        "generator": tier, "judge": judge_model, "critic": config.model("critic"), "writer": writer_model,
        "models": {p: config.model(p) for p in ("A", "B", "C", "critic")}, "pipeline_version": _pipeline_version(),
        "max_cost_usd": config.max_cost_usd, "started_at": stamp, "decks": {}}
    for slug in slugs:
        try:
            deck = load_deck(slug)
            row = evaluate_deck(deck, client=metered, judge_meter=judge_meter, config=config, writer_model=writer_model,
                                judge_model=judge_model, generator=tier, fetch_sources=fetch_sources, writer=writer,
                                judge_complete=judge_complete, out=out, profile=profile)
        except Exception as exc:                             # bad deck files: report, keep going
            row = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:500], "gate_violations": [], "judge": None,
                   "cost_usd": None, "latency_s": None}
        results["decks"][slug] = row
        print(_summary_line(slug, row), flush=True)
        _write_json(out / "results.json", results)           # progress survives a crash on a later deck
    bad = [s for s, r in results["decks"].items() if r["status"] != "ok" or r["gate_violations"]]
    for s in bad:
        for v in results["decks"][s]["gate_violations"]:
            print(f"  {s}: {v}", file=sys.stderr)
    costs = [r["cost_usd"] for r in results["decks"].values() if r.get("cost_usd") is not None]
    print(f"eval: results in {out / 'results.json'}; generator cost {_money(sum(costs) if costs else None)}"
          + (f"; {len(bad)} deck(s) failed or violated a gate: {', '.join(bad)}" if bad else ""))
    return 1 if bad else 0


def _pipeline_version() -> str:
    from src.server.domain.analytics.review_pipeline import config as cfg
    return cfg.PIPELINE_VERSION


def main(argv: list[str] | None = None, *, env: Mapping[str, str] | None = None, client=None, fetch_sources=None,
         writer=None, judge_complete=None) -> int:
    """The keyword arguments are test seams: ``env`` replaces os.environ; ``client``, ``fetch_sources``, ``writer`` and
    ``judge_complete`` replace the network edges so the whole golden-deck path runs offline."""
    import os

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", help="run deterministic run_review assembly smoke")
    mode.add_argument("--live", action="store_true", help="run the golden decks through run_review with real model calls (costs money)")
    parser.add_argument("--tier", help="generator model '<provider>:<model id>' (default: $REVIEW_MODEL)")
    parser.add_argument("--decks", nargs="+", metavar="SLUG", help="golden decks to run (default: all)")
    parser.add_argument("--out", type=Path, help="output directory (default: tests/review_evals/results/<timestamp>-<tier>/)")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    if args.offline:
        try:
            return _offline_smoke()
        except Exception as exc:
            print(f"eval: offline pipeline smoke failed: {exc}", file=sys.stderr)
            return 2
    if args.live:
        return run_live(args, env, client=client, fetch_sources=fetch_sources, writer=writer, judge_complete=judge_complete)
    print("eval: choose --offline (make eval) or --live (make eval-live); gates alone: make eval-gates", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
