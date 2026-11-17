"""Oracle.run: the call -> run tool -> feed result back loop with hard caps (spec §2.2)."""
from __future__ import annotations

import hashlib
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

from ..llm.port import ChatTurn, ToolChatClient, ToolCall
from .errors import OracleBudgetExceeded, OracleEvidenceMissing, OracleInvalidAnswer, OracleRunError
from .registry import ToolRegistry, serialize
from .types import LoopCaps, OracleResult, OracleRunState, OracleTrace, ToolOutcome

EVIDENCE_NUDGE = (
    "Before you answer, check the deck with the tools. You have not yet called: {missing}. "
    "Call them, then give your final answer based on what they return.")
REPAIR_PROMPT = (
    "Your answer was rejected for these reasons:\n{errors}\n"
    "Fix them and send the complete answer again.")
FINALIZE_PROMPT = "Tool budget used up. Answer now from the tool results above; do not call tools."

SOFT_TOKEN_RATIO = 0.8


def _args_hash(o: ToolOutcome) -> str:
    return hashlib.sha256(serialize(o.arguments).encode("utf-8")).hexdigest()[:12]


def _assistant_tool_message(turn: ChatTurn) -> Dict[str, Any]:
    raw = (turn.raw_message or {}).get("tool_calls")
    if not raw:  # fakes / providers without a raw list: rebuild from the parsed calls
        raw = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments_raw}}
               for c in turn.tool_calls]
    return {"role": "assistant", "content": turn.content or None, "tool_calls": raw}


class Oracle:
    def __init__(self, client: ToolChatClient, caps: LoopCaps, *, clock: Callable[[], float] = time.monotonic,
                 pack_id: Optional[str] = None):
        self.client = client
        self.caps = caps
        self.clock = clock
        self.pack_id = pack_id

    def run(self, *, task: str, messages: List[Dict[str, Any]], registry: ToolRegistry, ctx: Any,
            validate: Callable[[str, OracleRunState], List[str]],
            required_evidence: Mapping[str, frozenset] = {},
            response_format: Optional[dict] = None) -> OracleResult:
        caps = self.caps
        trace = OracleTrace(task=task, model=getattr(self.client, "model", ""), pack_id=self.pack_id)
        state = OracleRunState(task=task)
        msgs: List[Dict[str, Any]] = list(messages)
        deadline = self.clock() + caps.deadline_s
        specs = registry.specs()
        st = {"cost": 0.0, "cost_known": True, "tokens_in": 0, "tool_calls": 0,
              "nudges": caps.nudges, "repairs": caps.repairs, "finalized": False}

        def fail(exc: OracleRunError, reason: str) -> OracleRunError:
            exc.trace = trace
            trace.stop_reason = reason
            trace.error = str(exc)
            return exc

        def over_deadline() -> bool:
            return self.clock() >= deadline

        def soft_reason() -> Optional[str]:
            if len(trace.turns) >= caps.max_turns - 1:
                return "max_turns"
            if st["tool_calls"] >= caps.max_tool_calls:
                return "max_tool_calls"
            if st["cost_known"]:
                if st["cost"] >= caps.soft_cost_usd:
                    return "cost"
            elif st["tokens_in"] >= SOFT_TOKEN_RATIO * caps.max_tokens_in:
                return "tokens"
            if over_deadline():
                return "deadline"
            return None

        def hard_reason() -> Optional[str]:
            if st["cost_known"]:
                return "cost" if st["cost"] >= caps.hard_cost_usd else None
            return "tokens" if st["tokens_in"] >= caps.max_tokens_in else None

        def missing_evidence() -> Dict[str, List[str]]:
            miss: Dict[str, List[str]] = {}
            for tool, wanted in required_evidence.items():
                seen = set()
                for o in state.outputs:
                    if o.ok and o.tool == tool and isinstance(o.arguments, dict):
                        for v in o.arguments.values():
                            for item in (v if isinstance(v, (list, tuple)) else [v]):
                                if isinstance(item, str):
                                    seen.add(item)
                gap = sorted(set(wanted) - seen)
                if gap:
                    miss[tool] = gap
            return miss

        while True:
            # 1. budget
            hard = hard_reason()
            if hard:
                raise fail(OracleBudgetExceeded(hard), "budget_" + hard)
            if st["finalized"]:
                if len(trace.turns) >= caps.max_turns:
                    raise fail(OracleBudgetExceeded("max_turns"), "budget_max_turns")
                if over_deadline():
                    raise fail(OracleBudgetExceeded("deadline"), "budget_deadline")
            just_finalized = False
            if not st["finalized"]:
                if soft_reason():  # 7. finalize turn, at most once
                    st["finalized"] = just_finalized = True
                    msgs.append({"role": "user", "content": FINALIZE_PROMPT})
            finalizing = st["finalized"]
            kind = "finalize" if just_finalized else None

            # 2. model call
            turn = self.client.chat_turn(
                list(msgs), tools=specs or None,
                tool_choice=("none" if finalizing else "auto") if specs else None,
                response_format=response_format if finalizing else None)
            # 3. usage
            u = turn.usage
            st["tokens_in"] += u.tokens_in
            trace.totals["tokens_in"] += u.tokens_in
            trace.totals["tokens_out"] += u.tokens_out
            if u.cost_usd is None:
                st["cost_known"] = False
                trace.totals["cost_usd"] = None
            else:
                st["cost"] += u.cost_usd
                if trace.totals["cost_usd"] is not None:
                    trace.totals["cost_usd"] += u.cost_usd
            rec: Dict[str, Any] = {"n": len(trace.turns) + 1, "kind": kind or "answer",
                                   "usage": {"tokens_in": u.tokens_in, "tokens_out": u.tokens_out,
                                             "cached_tokens": u.cached_tokens, "cost_usd": u.cost_usd},
                                   "calls": []}
            trace.turns.append(rec)
            if turn.model:
                trace.model = turn.model

            # 4. tool calls
            if turn.tool_calls:
                if finalizing:
                    rec["kind"] = "finalize"
                    raise fail(OracleBudgetExceeded("model kept calling tools after finalize"),
                               "budget_tools_after_finalize")
                rec["kind"] = "tool"
                msgs.append(_assistant_tool_message(turn))
                for i, call in enumerate(turn.tool_calls):
                    if i >= caps.max_calls_per_turn:
                        o = registry.reject(call, "too_many_calls",
                                            f"at most {caps.max_calls_per_turn} tool calls per turn; call again if needed")
                    elif st["tool_calls"] >= caps.max_tool_calls:
                        o = registry.reject(call, "call_limit", "tool call budget used up")
                    elif over_deadline():
                        o = registry.reject(call, "deadline", "time budget used up")
                    else:
                        st["tool_calls"] += 1
                        trace.totals["tool_calls"] = st["tool_calls"]
                        o = registry.dispatch(call, ctx)
                    state.outputs.append(o)
                    rec["calls"].append({"id": o.call_id, "tool": o.tool, "args_hash": _args_hash(o), "ok": o.ok,
                                         "error_kind": o.error_kind, "ms": o.ms, "result_chars": o.result_chars,
                                         "truncated": o.truncated})
                    msgs.append({"role": "tool", "tool_call_id": call.id, "content": serialize(o.result)})
                continue

            # 5. candidate final answer
            can_continue = (not finalizing) or len(trace.turns) < caps.max_turns
            miss = missing_evidence()
            if miss:
                text = "; ".join(f"{t} ({', '.join(v)})" for t, v in sorted(miss.items()))
                if st["nudges"] > 0 and can_continue:
                    st["nudges"] -= 1
                    rec["kind"] = kind or "nudge"
                    msgs.append({"role": "user", "content": EVIDENCE_NUDGE.format(missing=text)})
                    continue
                raise fail(OracleEvidenceMissing(miss), "evidence_missing")
            errs = list(validate(turn.content, state) or [])
            if not errs:
                reason = "answered_after_finalize" if finalizing else "answered"
                trace.stop_reason = reason
                return OracleResult(text=turn.content, parsed=state.parsed, outputs=state.outputs,
                                    trace=trace, stop_reason=reason)
            if st["repairs"] > 0 and can_continue:
                st["repairs"] -= 1
                rec["kind"] = kind or "repair"
                msgs.append({"role": "assistant", "content": turn.content})
                msgs.append({"role": "user", "content": REPAIR_PROMPT.format(errors="\n".join(errs[:5]))})
                continue
            raise fail(OracleInvalidAnswer(errs), "invalid_answer")
