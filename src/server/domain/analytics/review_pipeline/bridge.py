"""Production wiring between Passes A-D and the Pass E writer (deck reviewer card 11).

Pass D's output has no ``verdict`` and the Pass E checker requires one (grade and confidence are preserved exactly),
so a live run derives it from the verified scorecard here. This is the same bridge the live eval harness uses
(``tests/review_evals/run_eval.py``, ``verdict_source: eval-bridge``); it lives in the pipeline package so the
application can run a real review without importing test code. Pure domain code: the LLM client and the writer
callback are injected, nothing here touches the network.
"""
from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

# The grade is the mean 1-10 scorecard score mapped through these floors (descending).
GRADE_FLOORS = (("A", 8.5), ("B", 7.0), ("C", 5.5), ("D", 4.0))


class BridgeError(RuntimeError):
    """The verified result cannot be turned into a Pass E input."""


def derive_verdict(axes: Sequence[Mapping[str, Any]], findings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Grade from the mean scorecard score; confidence is the mean finding confidence (0.5 if none)."""
    if not axes:
        raise BridgeError("verified result has no scorecard axes; cannot derive a verdict")
    mean = sum(a["score"] for a in axes) / len(axes)
    grade = next((g for g, floor in GRADE_FLOORS if mean >= floor), "F")
    confs = [f["confidence"] for f in findings if isinstance(f.get("confidence"), (int, float))]
    return {"grade": grade, "confidence": round(sum(confs) / len(confs), 2) if confs else 0.5}


def bridge_questions(result: Any) -> list[dict[str, Any]]:
    return [{k: q[k] for k in ("id", "question", "why_asked")} for q in result.brief["questions_for_owner"]]


def bridge_verified(result: Any, commanders: Sequence[str] = ()) -> dict[str, Any]:
    """The Pass E input: the verified Pass D sections plus what the brief supplies, and nothing else (no trace/log).
    The brief's owner questions are NOT included: they are Pass A output, not prose, and the writer checker would flag
    the sentence-initial words of a question as unverified names. ``attach_questions`` sets them from code."""
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
    """``run_passes`` for ``run_review``: the real Passes A-D, then the verdict bridge. ``sink['questions']`` gets
    Pass A's questions so the caller can ``attach_questions`` afterwards."""
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
