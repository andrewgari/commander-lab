"""Provider-neutral LLM port: error types and the chat client protocol.

Domain code catches these errors and never names a provider. Provider
clients (infra or legacy adapters) raise subclasses of them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Tuple, Union

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

    def __init__(self, message: str, status: Optional[int] = None, *, usage: Optional["Usage"] = None):
        self.status = status
        self.usage = usage
        super().__init__(message)


class LLMTruncatedError(LLMResponseError):
    """The reply hit max_tokens (finish_reason=length); callers may retry with a larger budget."""


class ChatClient(Protocol):
    def chat(self, messages: List[Dict[str, str]], **extra) -> str:
        """Return the assistant reply text."""

    def ask(self, prompt: str, *, system: Optional[str] = None, **extra) -> str:
        """Single-prompt convenience wrapper around chat()."""


@dataclass(frozen=True)
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    cost_usd: Optional[float] = None  # None = not reported


@dataclass(frozen=True)
class ToolCall:
    id: str  # provider id; synthesized "call_{turn}_{i}" if missing/blank
    name: str
    arguments: Optional[Dict[str, Any]]  # parsed JSON object, None when it did not parse
    arguments_raw: str  # the raw string as sent
    parse_error: Optional[str]  # "invalid JSON: ..." / "arguments must be an object"


@dataclass(frozen=True)
class ChatTurn:
    content: str  # "" when the model only called tools
    tool_calls: Tuple[ToolCall, ...]
    finish_reason: str
    usage: Usage
    model: str
    raw_message: Dict[str, Any]  # choices[0].message exactly as received


class ToolChatClient(Protocol):
    model: str

    def chat_turn(self, messages: List[Dict[str, Any]], *, tools: Optional[List[Dict[str, Any]]] = None,
                  tool_choice: Union[str, Dict[str, Any], None] = None,
                  response_format: Optional[Dict[str, Any]] = None,
                  temperature: Optional[float] = None) -> ChatTurn:
        """Run one model turn; may return tool calls instead of text."""
