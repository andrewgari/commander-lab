"""The oracle LLM client, re-homed under infra/llm (ARCHITECTURE.md §2:
`infra/llm/` is the only module that may call an LLM).

The implementation still lives in `domain/analytics/oracle_client.py` for
the legacy Redis-world callers (theory, sim, card review); infra importing
domain is allowed, the reverse is not, so this module re-exports it rather
than moving it mid-migration.
"""
from src.server.domain.analytics.oracle_client import (  # noqa: F401
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_S,
    OFFLINE_MESSAGE,
    OracleAuthError,
    OracleClient,
    OracleError,
    OracleNotConfigured,
    OracleResponseError,
    OracleUnavailable,
)

__all__ = [
    "OracleClient",
    "OracleError",
    "OracleUnavailable",
    "OracleNotConfigured",
    "OracleAuthError",
    "OracleResponseError",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_S",
    "OFFLINE_MESSAGE",
]
