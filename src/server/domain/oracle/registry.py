"""Tool + ToolRegistry: specs, dispatch, argument validation, failure tracking, result capping."""
from __future__ import annotations

import copy
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from jsonschema import Draft202012Validator

from ..llm.port import ToolCall
from .types import ToolOutcome

log = logging.getLogger(__name__)

_NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
MAX_CONSECUTIVE_FAILURES = 3


def serialize(obj: Any) -> str:
    """The one serialization used for tool-message content and the size cap."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


class ToolError(Exception):
    def __init__(self, kind: str, message: str):
        self.kind = kind
        self.message = message
        super().__init__(f"{kind}: {message}")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: Mapping[str, Any]
    handler: Callable[[dict, Any], dict]
    max_calls: Optional[int] = None
    max_result_chars: int = 12_000
    shrink: Optional[Callable[[dict], dict]] = None

    def __post_init__(self) -> None:
        if not _NAME.match(self.name or ""):
            raise ValueError(f"invalid tool name: {self.name!r}")
        if len(self.description) > 600:
            raise ValueError(f"tool {self.name}: description over 600 chars")


def _fail(kind: str, message: str) -> Dict[str, Any]:
    return {"ok": False, "error": {"kind": kind, "message": message}}


def _lists(obj: Any, path: tuple = ()) -> List[tuple]:
    found: List[tuple] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            found += _lists(v, path + (k,))
    elif isinstance(obj, list):
        found.append(path)
        for i, v in enumerate(obj):
            found += _lists(v, path + (i,))
    return found


def _get(obj: Any, path: tuple) -> Any:
    for p in path:
        obj = obj[p]
    return obj


def _fit(data: Any, cap: int) -> Optional[Dict[str, Any]]:
    """Cut lists (largest first, from the end) until the envelope fits. None if it never fits."""
    data = copy.deepcopy(data)

    def env() -> Dict[str, Any]:
        return {"data": data, "ok": True, "truncated": True}

    for _ in range(10_000):
        if len(serialize(env())) <= cap:
            return env()
        cands = [p for p in _lists(data) if _get(data, p)]
        if not cands:
            return None
        path = max(cands, key=lambda p: len(serialize(_get(data, p))))
        lst = _get(data, path)
        lo, hi = 0, len(lst) - 1  # keep `lo`..`hi` items; find the longest prefix that fits
        full = lst[:]
        while lo < hi:
            mid = (lo + hi + 1) // 2
            lst[:] = full[:mid]
            if len(serialize(env())) <= cap:
                lo = mid
            else:
                hi = mid - 1
        lst[:] = full[:lo]
    return None


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool], *, clock: Callable[[], float] = time.monotonic):
        self._tools: Dict[str, Tool] = {}
        for t in tools:
            if t.name in self._tools:
                raise ValueError(f"duplicate tool name: {t.name}")
            self._tools[t.name] = t
        self._clock = clock
        self._failures: Dict[str, int] = {}
        self._calls: Dict[str, int] = {}

    def names(self) -> frozenset:
        return frozenset(self._tools)

    def specs(self) -> List[Dict[str, Any]]:
        return [{"type": "function", "function": {
            "name": t.name, "description": t.description, "parameters": dict(t.parameters)}}
            for t in self._tools.values()]

    def is_disabled(self, name: str) -> bool:
        return self._failures.get(name, 0) >= MAX_CONSECUTIVE_FAILURES

    def reject(self, call: ToolCall, kind: str, message: str) -> ToolOutcome:
        """Answer a call that was not run (loop-level cap)."""
        return self._outcome(call, _fail(kind, message), 0)

    def _outcome(self, call: ToolCall, result: Dict[str, Any], ms: int, truncated: bool = False) -> ToolOutcome:
        ok = bool(result.get("ok"))
        # an absurdly small cap yields a bare `{}` result: no error body fits, so name the kind here
        kind = None if ok else (result.get("error") or {}).get("kind", "result_too_large")
        return ToolOutcome(call_id=call.id, tool=call.name,
                           arguments=call.arguments if call.arguments is not None else call.arguments_raw,
                           ok=ok, result=result, ms=ms, truncated=truncated, error_kind=kind,
                           result_chars=len(serialize(result)))

    def dispatch(self, call: ToolCall, ctx: Any) -> ToolOutcome:
        t0 = self._clock()

        def done(result: Dict[str, Any], truncated: bool = False) -> ToolOutcome:
            return self._outcome(call, result, int((self._clock() - t0) * 1000), truncated)

        tool = self._tools.get(call.name)
        if tool is None:  # 1
            return done(_fail("unknown_tool", f"unknown tool {call.name!r}; valid tools: "
                              + ", ".join(sorted(self._tools))))
        name = tool.name
        if call.parse_error:  # 2
            self._failures[name] = self._failures.get(name, 0) + 1
            return done(_fail("bad_arguments", call.parse_error))
        errs = sorted(Draft202012Validator(dict(tool.parameters)).iter_errors(call.arguments),
                      key=lambda e: [str(p) for p in e.absolute_path])
        if errs:  # 3
            self._failures[name] = self._failures.get(name, 0) + 1
            msg = "; ".join((("/".join(str(p) for p in e.absolute_path) + ": ") if e.absolute_path else "")
                            + e.message[:200] for e in errs[:3])
            return done(_fail("bad_arguments", msg))
        if self.is_disabled(name):  # 4
            return done(_fail("disabled", f"{name} failed {MAX_CONSECUTIVE_FAILURES} times and is disabled for this run"))
        if tool.max_calls is not None and self._calls.get(name, 0) >= tool.max_calls:  # 5
            return done(_fail("call_limit", f"{name} may be called at most {tool.max_calls} time(s) per run"))
        self._calls[name] = self._calls.get(name, 0) + 1
        try:  # 6
            data = tool.handler(call.arguments, ctx)
        except ToolError as exc:
            self._failures[name] = self._failures.get(name, 0) + 1
            return done(_fail(exc.kind, exc.message))
        except Exception as exc:  # noqa: BLE001 - tool code is untrusted by the loop
            log.exception("tool %s raised", name)
            self._failures[name] = self._failures.get(name, 0) + 1
            return done(_fail("tool_failed", type(exc).__name__))
        self._failures[name] = 0  # 7
        result: Dict[str, Any] = {"ok": True, "data": data}
        try:
            size = len(serialize(result))
        except Exception as exc:  # noqa: BLE001
            log.exception("tool %s result not serializable", name)
            self._failures[name] = self._failures.get(name, 0) + 1
            return done(_fail("tool_failed", type(exc).__name__))
        if size <= tool.max_result_chars:
            return done(result)
        if tool.shrink is not None:
            try:
                data = tool.shrink(copy.deepcopy(data))
            except Exception:  # noqa: BLE001
                log.exception("tool %s shrink raised", name)
        fitted = _fit(data, tool.max_result_chars)
        if fitted is None:
            fitted = {"ok": True, "data": {}, "truncated": True}
            if len(serialize(fitted)) > tool.max_result_chars:
                fitted = {}  # absurdly small cap: send nothing rather than exceed it
        return done(fitted, truncated=True)
