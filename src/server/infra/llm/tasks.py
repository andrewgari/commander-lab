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
}
