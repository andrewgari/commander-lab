"""Structured output enforcement: JSON-schema validation with a bounded repair loop.

``generate_structured`` never returns partial results: it returns a validated object or raises
``StructuredOutputError`` carrying every attempt (so the trace shows what the model said).
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator

from src.server.domain.analytics.review_pipeline.llm import (
    LLMClient, LLMRequest, LLMResponse, LLMTransportError, Message)

DEFAULT_MAX_REPAIRS = 2

# Keywords strict structured-output modes commonly reject. We strip them from the schema sent to the
# model and restate them as description text; our own validator still enforces the original schema.
_UNSUPPORTED = ("minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                "pattern", "format", "minItems", "maxItems", "uniqueItems", "default", "multipleOf")


class StructuredOutputError(RuntimeError):
    def __init__(self, message: str, attempts: Sequence["Attempt"]):
        super().__init__(message)
        self.attempts = list(attempts)


@dataclass
class Attempt:
    index: int
    request: LLMRequest
    response: LLMResponse | None = None
    verdict: str = "ok"            # ok | retry | failed
    errors: list[str] = field(default_factory=list)
    error: str | None = None       # transport error text


def portable_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Deep copy of ``schema`` without keywords structured-output APIs reject; the dropped
    constraints are appended to ``description`` so the model still sees them."""
    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(x) for x in node]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        notes = []
        for k, v in node.items():
            if k in _UNSUPPORTED and not isinstance(v, (dict, list)):
                notes.append(f"{k} {v}")
            elif k in ("properties", "$defs", "definitions") and isinstance(v, dict):
                out[k] = {name: walk(sub) for name, sub in v.items()}
            else:
                out[k] = walk(v)
        if notes:
            base = out.get("description", "")
            out["description"] = (base + " " if base else "") + "(" + ", ".join(notes) + ")"
        return out
    return walk(copy.deepcopy(dict(schema)))


def validate_schema(obj: Any, schema: Mapping[str, Any]) -> list[str]:
    """All JSON-schema violations of ``obj`` as short readable strings (empty = valid)."""
    v = Draft202012Validator(schema)
    errs = sorted(v.iter_errors(obj), key=lambda e: [str(p) for p in e.absolute_path])
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message[:200]}" for e in errs[:12]]


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def parse_json(text: str) -> Any:
    """Parse a model reply as JSON, tolerating a surrounding code fence or leading/trailing prose."""
    s = text.strip()
    m = _FENCE.match(s)
    if m:
        s = m.group(1)
    try:
        return json.loads(s)
    except ValueError:
        i, j = s.find("{"), s.rfind("}")
        if i != -1 and j > i:
            return json.loads(s[i:j + 1])
        raise


def generate_structured(client: LLMClient, request: LLMRequest, *, validate: Callable[[Any], list[str]],
                        max_repairs: int = DEFAULT_MAX_REPAIRS,
                        on_attempt: Callable[[Attempt], None] | None = None) -> tuple[Any, list[Attempt]]:
    """Call the model, validate, and on invalid output re-ask with the error up to ``max_repairs`` times.

    ``validate(obj) -> list[str]`` must include schema validation plus any semantic checks.
    Transport errors are not repaired: they are recorded and re-raised. After the last attempt fails
    the function raises ``StructuredOutputError``; there is no partial result.
    """
    attempts: list[Attempt] = []
    current = request
    for i in range(max_repairs + 1):
        att = Attempt(index=i, request=current)
        attempts.append(att)
        try:
            att.response = client.complete(current)
        except LLMTransportError as exc:
            att.verdict, att.error = "failed", str(exc)
            if on_attempt:
                on_attempt(att)
            raise
        text = att.response.text
        try:
            obj = parse_json(text)
            att.errors = list(validate(obj))
        except ValueError as exc:
            obj, att.errors = None, [f"reply is not valid JSON ({str(exc)[:120]})"]
        if not att.errors:
            att.verdict = "ok"
            if on_attempt:
                on_attempt(att)
            return obj, attempts
        last = i == max_repairs
        att.verdict = "failed" if last else "retry"
        if on_attempt:
            on_attempt(att)
        if last:
            break
        bullet = "\n".join(f"- {e}" for e in att.errors)
        fix = ("Your previous reply was rejected:\n" + bullet +
               "\nReply again with ONLY the corrected JSON object that satisfies the schema. "
               "Keep everything that was valid; fix only what is listed.")
        current = request.with_messages(request.messages + (
            Message("assistant", text if text.strip() else "(empty reply)"), Message("user", fix)))
    raise StructuredOutputError(
        f"{request.schema_name}: no valid output after {len(attempts)} attempt(s): {'; '.join(attempts[-1].errors)[:400]}",
        attempts)
