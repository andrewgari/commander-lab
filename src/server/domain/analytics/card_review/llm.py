"""LLM provider boundary. The debate only ever talks to a DebateLLM; tests pass a scripted fake."""
from __future__ import annotations

from typing import Callable, Optional, Protocol

Role = str  # "remove" | "keep" | "judge"


class DebateLLM(Protocol):
    def complete(self, role: Role, system: str, user: str, *, temperature: float = 0.0) -> str:
        """Return the raw model reply (the runner parses and validates the JSON)."""


class OracleLLM:
    """Production adapter: the `oracle` Hermes profile via analytics.oracle_client.OracleClient.
    Oracle fixes its own sampling, so `temperature` is advisory and not forwarded."""

    def __init__(self, client_factory: Callable[[], object]):
        self._factory = client_factory
        self._client: Optional[object] = None

    def complete(self, role: Role, system: str, user: str, *, temperature: float = 0.0) -> str:
        if self._client is None:
            self._client = self._factory()
        return self._client.chat([{"role": "system", "content": system}, {"role": "user", "content": user}])
