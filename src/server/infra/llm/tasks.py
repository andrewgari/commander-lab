"""Per-task OpenRouter defaults (spec t_d29ed3c9 §1.5). Any model can be overridden by env."""
from __future__ import annotations

from dataclasses import dataclass

SONNET = "anthropic/claude-sonnet-5.5"
HAIKU = "anthropic/claude-haiku-5.5"
OPUS = "anthropic/claude-opus-5.5"


@dataclass(frozen=True)
class TaskSpec:
    model: str
    timeout_s: float
    max_tokens: int


TASKS: dict[str, TaskSpec] = {
    "card_review": TaskSpec(SONNET, 180, 4000),
    "deck_review": TaskSpec(SONNET, 300, 8000),
    "sim_review": TaskSpec(SONNET, 300, 6000),
    "sim_diagnosis": TaskSpec(HAIKU, 120, 1500),
    "personality_read": TaskSpec(SONNET, 300, 6000),
    "genre_autofill": TaskSpec(HAIKU, 120, 1500),
    "deck_writeup": TaskSpec(SONNET, 300, 6000),
    "theory": TaskSpec(OPUS, 600, 16000),
    "reconstruct": TaskSpec(OPUS, 600, 16000),
    "review_writer": TaskSpec(SONNET, 300, 8000),
    "brew_review": TaskSpec(SONNET, 240, 2000),
    "brew_rules": TaskSpec(SONNET, 120, 1500),
    "review_pipeline": TaskSpec(SONNET, 300, 8000),
    "interactions": TaskSpec(HAIKU, 120, 4000),
}


@dataclass(frozen=True)
class LoopSpec:
    """Default oracle loop caps per task (spec oracle-tool-loop §3)."""
    max_turns: int
    max_tool_calls: int
    soft_cost_usd: float
    hard_cost_usd: float
    max_tokens_in: int
    deadline_s: float


LOOPS: dict[str, LoopSpec] = {
    "deck_review": LoopSpec(10, 16, 0.60, 1.00, 400_000, 1500),
    "sim_review": LoopSpec(6, 8, 0.30, 0.50, 200_000, 300),
    "sim_diagnosis": LoopSpec(4, 4, 0.05, 0.10, 60_000, 120),
    "card_review": LoopSpec(6, 8, 0.20, 0.35, 150_000, 180),
    "review_writer": LoopSpec(6, 8, 0.40, 0.70, 250_000, 300),
    "review_pipeline": LoopSpec(6, 8, 0.50, 0.90, 300_000, 600),
    "reconstruct": LoopSpec(14, 30, 1.50, 2.50, 900_000, 1200),
    "brew_review": LoopSpec(6, 8, 0.20, 0.35, 150_000, 240),
    "brew_rules": LoopSpec(2, 2, 0.05, 0.10, 40_000, 120),
}
