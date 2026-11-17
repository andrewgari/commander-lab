"""In-repo oracle: an agentic call -> run tool -> feed result back loop (pure domain)."""
from .errors import OracleBudgetExceeded, OracleEvidenceMissing, OracleInvalidAnswer, OracleRunError
from .loop import Oracle
from .registry import Tool, ToolError, ToolRegistry
from .types import LoopCaps, OracleResult, OracleRunState, OracleTrace, ToolOutcome

__all__ = [
    "Oracle", "OracleResult", "OracleRunState", "OracleTrace", "ToolOutcome", "LoopCaps",
    "Tool", "ToolError", "ToolRegistry",
    "OracleRunError", "OracleBudgetExceeded", "OracleEvidenceMissing", "OracleInvalidAnswer",
]
