"""Review writer (ARCHITECTURE.md §6): prompt assembly + oracle call +
reply extraction for the Slice 3 review pipeline.

The hard rules (§6, REVIEW_SPEC core rule) shape the prompt: the LLM gets
verified facts only, writes judgment/prose only, never computes a number,
never invents a card. Schema validation of the reply JSON happens in
domain/review/writer.parse_reply (pydantic); this module is the network
edge and stays dumb.
"""
from __future__ import annotations

import json
from typing import Mapping, Optional

from src.server.domain.knowledge import build_messages
from src.server.domain.llm.port import ChatClient, LLMNotConfigured
from src.server.infra.llm.factory import build_chat_client

class ReviewWriter:
    """Callable writer for pipeline.run: payload -> raw oracle reply text."""

    def __init__(self, client: ChatClient):
        self.client = client

    @classmethod
    def from_env(cls) -> "ReviewWriter":
        """Build from the OpenRouter env (task "review_writer"); raises LLMNotConfigured."""
        return cls(build_chat_client("review_writer"))

    def __call__(self, payload: Mapping) -> str:
        facts = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        messages, _ = build_messages(
            "review_writer",
            facts_json="Facts (verified, complete):\n```json\n" + facts + "\n```",
            user_text="Write the sections now, following the rules.",
        )
        return self.client.chat(messages)


def default_writer() -> Optional[ReviewWriter]:
    """The env-configured writer, or None when the oracle is not configured
    (offline degradation is a normal path, not an error)."""
    try:
        return ReviewWriter.from_env()
    except LLMNotConfigured:
        return None
