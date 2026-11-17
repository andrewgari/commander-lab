"""Oracle run failures. Subclass LLMError so existing `except LLMError` branches still catch them."""
from __future__ import annotations

from typing import Any, Iterable, Optional

from ..llm.port import LLMError


class OracleRunError(LLMError):
    def __init__(self, message: str = "", trace: Any = None):
        self.trace = trace
        super().__init__(message)


class OracleBudgetExceeded(OracleRunError):
    def __init__(self, reason: str, trace: Any = None):
        self.reason = reason
        super().__init__(reason, trace)


class OracleEvidenceMissing(OracleRunError):
    def __init__(self, missing: Any, trace: Any = None):
        self.missing = missing
        super().__init__(f"required evidence missing: {missing}", trace)


class OracleInvalidAnswer(OracleRunError):
    def __init__(self, errors: Iterable[str], trace: Any = None):
        self.errors = list(errors)
        first = (self.errors[0] if self.errors else "invalid answer")[:300]
        super().__init__(first, trace)
