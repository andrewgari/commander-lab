"""Pipeline configuration: which model runs which pass, prompt versions, limits.

Model ids are config, never pass logic (DECISIONS D3: "pin model ids in config, record them in
``review_run_steps.model`` and ``review_document.meta.models``"). A model spec is
``<provider>:<model id>``; ``infra/llm/provider.py`` routes on the provider prefix.

Environment (all optional):
    REVIEW_MODEL_A, REVIEW_MODEL_B           model for Pass A / every Pass B axis
    REVIEW_MODEL_B_<AXIS_ID>                 override one Pass B axis, e.g. REVIEW_MODEL_B_MANA_CONSISTENCY
    REVIEW_PROMPT_<NAME>                     prompt version override, e.g. REVIEW_PROMPT_B_ANALYSIS=v2
    REVIEW_MAX_REPAIRS    (default 2)        repair retries per call on invalid output
    REVIEW_MAX_COST_USD   (unset = no cap)   fail the run explicitly when the running cost exceeds it
    REVIEW_MODEL_PRICES   JSON {"<spec>": {"in":..,"out":..,"cache_read":..,"cache_write":..}} USD per
                          1M tokens, used only when the provider does not report cost itself
    REVIEW_WARM_CACHE     (default 1)        run the first Pass B axis alone so the rest hit the cache
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Mapping

PIPELINE_VERSION = "review-ab-1"

DEFAULT_MODELS = {      # DECISIONS D3: Anthropic for passes A-D (mid for A, strong for B)
    "A": "anthropic:claude-sonnet-5-5",
    "B": "anthropic:claude-opus-5-5",
}
PROMPT_VERSIONS = {"a_brief": "v1", "b_analysis": "v1", "quality_standard": "v1"}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Axis:
    id: str
    label: str
    fact_prefixes: tuple[str, ...]      # which slices of the FactIndex this axis sees ('*' = one segment)


AXES: tuple[Axis, ...] = (
    Axis("plan_synergy", "Plan coherence and synergy",
         ("deck", "synergy", "roles", "derived.type_counts", "combos.complete")),
    Axis("mana_consistency", "Mana and consistency",
         ("deck", "mana", "curve", "roles.ramp", "roles.draw", "roles.tutors", "derived.type_counts")),
    Axis("interaction_resilience", "Interaction and resilience",
         ("deck", "roles", "derived.type_counts")),
    Axis("win_paths_closing", "Win paths and closing",
         ("deck", "combos", "bracket", "roles.tutors", "roles.recursion", "roles.ramp", "curve.average_mv",
          "synergy.clusters")),
    Axis("power_bracket", "Power and bracket fit",
         ("deck", "bracket", "combos.complete", "curve.average_mv", "roles.ramp", "roles.tutors", "salt",
          "edhrec.commander", "edhrec.deviation_score", "derived.price.most_expensive")),
    Axis("budget_inventory", "Budget and inventory",
         ("deck", "derived.price", "derived.inventory", "bracket.game_changers")),
    Axis("concept_execution", "Execution of the deck's central concept",
         ("deck", "synergy", "roles", "curve.average_mv", "combos.complete", "edhrec.deviation_score")),
)

# Facts shown to Pass A (a compact summary; the brief reads the card table for everything else).
SUMMARY_PREFIXES = ("deck", "curve.average_mv", "curve.nonland_cards", "mana.lands", "mana.mdfc_spell_lands",
                    "roles.*.count", "bracket", "combos.complete", "derived.type_counts", "derived.price.total_usd",
                    "edhrec.deviation_score", "edhrec.commander")


def axis_by_id(axis_id: str) -> Axis:
    for a in AXES:
        if a.id == axis_id:
            return a
    raise KeyError(axis_id)


def _int(env: Mapping[str, str], key: str, default: int, minimum: int = 0) -> int:
    raw = env.get(key)
    if raw in (None, ""):
        return default
    try:
        v = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc
    if v < minimum:
        raise ConfigError(f"{key} must be >= {minimum}, got {v}")
    return v


@dataclass(frozen=True)
class PipelineConfig:
    models: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))
    axis_models: Mapping[str, str] = field(default_factory=dict)
    prompt_versions: Mapping[str, str] = field(default_factory=lambda: dict(PROMPT_VERSIONS))
    max_repairs: int = 2
    max_cost_usd: float | None = None
    prices: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    warm_cache: bool = True
    max_tokens_a: int = 6000
    max_tokens_b: int = 8000

    def model(self, pass_name: str, axis_id: str | None = None) -> str:
        if pass_name == "B" and axis_id and axis_id in self.axis_models:
            return self.axis_models[axis_id]
        try:
            return self.models[pass_name]
        except KeyError as exc:
            raise ConfigError(f"no model configured for pass {pass_name!r}") from exc

    def prompt_version(self, name: str) -> str:
        return self.prompt_versions[name]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PipelineConfig":
        env = os.environ if env is None else env
        models = dict(DEFAULT_MODELS)
        for p in ("A", "B"):
            if env.get(f"REVIEW_MODEL_{p}"):
                models[p] = env[f"REVIEW_MODEL_{p}"]
        axis_models = {a.id: env[f"REVIEW_MODEL_B_{a.id.upper()}"] for a in AXES if env.get(f"REVIEW_MODEL_B_{a.id.upper()}")}
        for spec in [*models.values(), *axis_models.values()]:
            if ":" not in spec:
                raise ConfigError(f"model spec {spec!r} must look like '<provider>:<model id>'")
        versions = dict(PROMPT_VERSIONS)
        for n in versions:
            if env.get(f"REVIEW_PROMPT_{n.upper()}"):
                versions[n] = env[f"REVIEW_PROMPT_{n.upper()}"]
        cap = env.get("REVIEW_MAX_COST_USD")
        try:
            max_cost = float(cap) if cap not in (None, "") else None
        except ValueError as exc:
            raise ConfigError(f"REVIEW_MAX_COST_USD must be a number, got {cap!r}") from exc
        prices: dict = {}
        if env.get("REVIEW_MODEL_PRICES"):
            try:
                prices = json.loads(env["REVIEW_MODEL_PRICES"])
            except ValueError as exc:
                raise ConfigError(f"REVIEW_MODEL_PRICES is not valid JSON: {exc}") from exc
        return cls(models=models, axis_models=axis_models, prompt_versions=versions,
                   max_repairs=_int(env, "REVIEW_MAX_REPAIRS", 2), max_cost_usd=max_cost, prices=prices,
                   warm_cache=env.get("REVIEW_WARM_CACHE", "1") not in ("0", "false", "no"))
