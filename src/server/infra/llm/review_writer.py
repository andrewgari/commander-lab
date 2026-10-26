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

from src.server.domain.review.doc import WRITER_SECTIONS
from src.server.infra.llm.client import (
    OracleClient,
    OracleNotConfigured,
)

SYSTEM_PROMPT = (
    "You are the Commander Lab review writer. You explain a Commander deck "
    "review to its owner. HARD RULES: (1) never compute or estimate a number — "
    "every number you write must appear verbatim in the facts JSON; (2) never "
    "name a card that is not in the facts JSON; (3) list every card you "
    "reference in that section's `cards` array, spelled exactly as in the "
    "facts; (4) judgment and prose only. Reply with exactly one fenced ```json "
    "block: {\"sections\": {<section>: {\"text\": <prose, <=6 sentences>, "
    "\"cards\": [<names>]}}} and nothing after it. Sections: "
    + ", ".join(WRITER_SECTIONS) + "."
)


class ReviewWriter:
    """Callable writer for pipeline.run: payload -> raw oracle reply text."""

    def __init__(self, client: OracleClient):
        self.client = client

    @classmethod
    def from_env(cls) -> "ReviewWriter":
        """Build from ORACLE_URL/ORACLE_KEY; raises OracleNotConfigured."""
        return cls(OracleClient.from_env())

    def __call__(self, payload: Mapping) -> str:
        facts = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        prompt = (
            "Explain this Commander deck review. Facts (verified, complete):\n"
            f"```json\n{facts}\n```\n"
            "Write the sections now, following the rules."
        )
        return self.client.ask(prompt, system=SYSTEM_PROMPT)


def default_writer() -> Optional[ReviewWriter]:
    """The env-configured writer, or None when the oracle is not configured
    (offline degradation is a normal path, not an error)."""
    try:
        return ReviewWriter.from_env()
    except OracleNotConfigured:
        return None
