"""One traced, schema-enforced model call: the only way a pass talks to the model."""
from __future__ import annotations

from typing import Any, Callable

from src.server.domain.analytics.review_pipeline.llm import LLMClient, LLMRequest
from src.server.domain.analytics.review_pipeline.structured import generate_structured
from src.server.domain.analytics.review_pipeline.trace import TraceRecorder


def traced_call(client: LLMClient, request: LLMRequest, *, validate: Callable[[Any], list[str]], max_repairs: int,
                rec: TraceRecorder, pass_name: str, axis: str | None, prompt_version: str) -> tuple[Any, int]:
    """Returns (validated object, trace step index of the attempt that produced it).
    Raises StructuredOutputError / LLMTransportError / BudgetExceeded; every attempt is traced first."""
    last: dict[str, int] = {}

    def on_attempt(a):
        last["i"] = rec.record_attempt(a, pass_name=pass_name, axis=axis, prompt_version=prompt_version)["index"]

    obj, _ = generate_structured(client, request, validate=validate, max_repairs=max_repairs, on_attempt=on_attempt)
    return obj, last["i"]
