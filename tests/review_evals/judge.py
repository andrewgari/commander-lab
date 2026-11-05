"""LLM judge for review quality (live evals only; never run in CI).

Scores five dimensions, each 1-10 (higher is better):
rubric_recall, specificity, intent_alignment, actionability, and
generic_filler (INVERTED: 10 = no filler, 1 = mostly filler).

The judge must be a different model from the generator. ``complete`` is
injected so tests run offline; the live default is wired in when the review
pipeline exists.
"""
from __future__ import annotations

import json
from typing import Callable

from tests.review_evals import config

DIMENSIONS = ("rubric_recall", "specificity", "intent_alignment", "actionability", "generic_filler")


class JudgeConfigError(RuntimeError):
    pass


def resolve_models(generator: str | None = None, judge: str | None = None) -> tuple[str, str]:
    generator = generator or config.generator_model()
    judge = judge or config.judge_model()
    if not generator:
        raise JudgeConfigError("generator model not set (REVIEW_GENERATOR_MODEL)")
    if not judge:
        raise JudgeConfigError("judge model not set (REVIEW_JUDGE_MODEL)")
    if judge.strip().lower() == generator.strip().lower():
        raise JudgeConfigError(f"judge model must differ from generator model (both are {judge!r})")
    return generator, judge


PROMPT = """You are grading a Magic: The Gathering Commander deck review against the owner's rubric.
Score each dimension 1-10 (10 best). generic_filler is INVERTED: 10 means no generic filler, 1 means mostly filler.
- rubric_recall: share of rubric.must_mention items the review surfaces; penalise anything in must_not_recommend or owner_would_reject.
- specificity: claims name cards and mechanisms.
- intent_alignment: judged against the deck's own intent, not a generic power ideal.
- actionability: swaps are concrete with trade-offs.
- generic_filler (inverted).
Respond with JSON only: {"rubric_recall": n, "specificity": n, "intent_alignment": n, "actionability": n, "generic_filler": n, "notes": "..."}

RUBRIC:
%s

INTENT:
%s

REVIEW:
%s
"""


def judge_review(review: dict, rubric: dict, intent: dict, *, generator: str | None = None, judge: str | None = None,
                 complete: Callable[[str, str], str]) -> dict:
    """``complete(model, prompt) -> str`` returns the judge's raw reply."""
    _, judge_name = resolve_models(generator, judge)
    prompt = PROMPT % (json.dumps(rubric), json.dumps(intent), json.dumps(review))
    raw = complete(judge_name, prompt)
    try:
        data = json.loads(raw)
        scores = {k: float(data[k]) for k in DIMENSIONS}
    except (ValueError, KeyError, TypeError) as exc:
        raise JudgeConfigError(f"judge reply was not valid score JSON: {exc}") from exc
    for k, v in scores.items():
        if not 1 <= v <= 10:
            raise JudgeConfigError(f"judge score {k}={v} outside 1-10")
    return {"scores": scores, "notes": data.get("notes", ""), "judge_model": judge_name}
