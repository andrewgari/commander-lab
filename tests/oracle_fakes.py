"""Test doubles for the oracle loop (spec oracle-tool-loop §9.2)."""
import json
from typing import Any, Dict, List, Optional

from src.server.domain.llm.port import ChatTurn, ToolCall, Usage


def call(name: str, args: Optional[dict] = None, id: str = "c1") -> ToolCall:
    raw = json.dumps(args if args is not None else {})
    return ToolCall(id=id, name=name, arguments=args if args is not None else {}, arguments_raw=raw, parse_error=None)


def bad_call(name: str, raw: str = "{nope", id: str = "c1") -> ToolCall:
    return ToolCall(id=id, name=name, arguments=None, arguments_raw=raw, parse_error="invalid JSON: x")


def tool_turn(*calls: ToolCall, cost: Optional[float] = 0.01, tokens_in: int = 10, content: str = "") -> ChatTurn:
    raw = {"role": "assistant", "content": content or None,
           "tool_calls": [{"id": c.id, "type": "function",
                           "function": {"name": c.name, "arguments": c.arguments_raw}} for c in calls]}
    return ChatTurn(content=content, tool_calls=tuple(calls), finish_reason="tool_calls",
                    usage=Usage(tokens_in, 5, 0, cost), model="m/served", raw_message=raw)


def text_turn(text: str, cost: Optional[float] = 0.01, tokens_in: int = 10) -> ChatTurn:
    return ChatTurn(content=text, tool_calls=(), finish_reason="stop", usage=Usage(tokens_in, 5, 0, cost),
                    model="m/served", raw_message={"role": "assistant", "content": text})


class ScriptedToolChat:
    """ToolChatClient that replays `turns` and records every request."""
    model = "m/x"

    def __init__(self, turns: List[ChatTurn], on_call=None):
        self.turns = list(turns)
        self.calls: List[Dict[str, Any]] = []
        self.on_call = on_call

    def chat_turn(self, messages, *, tools=None, tool_choice=None, response_format=None, temperature=None):
        self.calls.append({"messages": json.loads(json.dumps(messages)), "tools": tools,
                           "tool_choice": tool_choice, "response_format": response_format})
        if self.on_call:
            self.on_call(len(self.calls))
        if not self.turns:
            raise AssertionError("ScriptedToolChat ran out of scripted turns")
        return self.turns.pop(0)


class FakeClock:
    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t
