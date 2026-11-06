"""Provider-neutral LLM port: error types and the chat client protocol.

Domain code catches these errors and never names a provider. Provider
clients (infra or legacy adapters) raise subclasses of them.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Protocol

UNAVAILABLE_MESSAGE = "AI writer unavailable, try again later"


class LLMError(Exception):
    """Base class for every LLM client failure."""


class LLMUnavailable(LLMError):
    """The provider can't be reached right now (down, timeout, overloaded, disabled)."""

    def __init__(self, detail: str = ""):
        self.detail = detail
        super().__init__(UNAVAILABLE_MESSAGE + (f" ({detail})" if detail else ""))


class LLMNotConfigured(LLMUnavailable):
    """No provider or credentials configured."""


class LLMAuthError(LLMError):
    """The provider rejected the credentials (401/403). A config problem, not an outage."""


class LLMResponseError(LLMError):
    """The provider answered with a client error or a reply we can't parse."""

    def __init__(self, message: str, status: Optional[int] = None):
        self.status = status
        super().__init__(message)


class ChatClient(Protocol):
    def chat(self, messages: List[Dict[str, str]], **extra) -> str:
        """Return the assistant reply text."""

    def ask(self, prompt: str, *, system: Optional[str] = None, **extra) -> str:
        """Single-prompt convenience wrapper around chat()."""
