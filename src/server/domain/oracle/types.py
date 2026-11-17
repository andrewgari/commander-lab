"""Dataclasses shared by the oracle loop and tool registry."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class LoopCaps:
    max_turns: int  # model calls, including nudge, repair and finalize turns
    max_tool_calls: int  # total over the run
    soft_cost_usd: float  # finalize when reached
    hard_cost_usd: float  # abort (OracleBudgetExceeded) when reached before a call
    max_tokens_in: int  # fallback meter when the provider reports no cost: sum of prompt tokens
    deadline_s: float  # wall clock for the whole run (tool time included)
    max_calls_per_turn: int = 8
    repairs: int = 1
    nudges: int = 1


@dataclass
class ToolOutcome:
    call_id: str
    tool: str
    arguments: Any
    ok: bool
    result: Dict[str, Any]  # the envelope sent back to the model
    ms: int = 0
    truncated: bool = False
    error_kind: Optional[str] = None
    result_chars: int = 0


@dataclass
class OracleTrace:
    task: str
    model: str
    pack_id: Optional[str] = None
    turns: List[Dict[str, Any]] = field(default_factory=list)
    totals: Dict[str, Any] = field(default_factory=lambda: {
        "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "tool_calls": 0})
    stop_reason: Optional[str] = None
    error: Optional[str] = None

    def to_job_meta(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "model": self.model,
            "pack": self.pack_id,
            "turns": len(self.turns),
            "tool_calls": self.totals.get("tool_calls", 0),
            "cost_usd": self.totals.get("cost_usd"),
            "stop_reason": self.stop_reason,
        }


@dataclass
class OracleRunState:
    """Handed to the consumer's validate(); the validator may stash its parsed result on .parsed."""
    task: str
    outputs: List[ToolOutcome] = field(default_factory=list)
    parsed: Any = None


@dataclass
class OracleResult:
    text: str
    parsed: Any
    outputs: List[ToolOutcome]
    trace: OracleTrace
    stop_reason: str  # "answered" | "answered_after_finalize"
