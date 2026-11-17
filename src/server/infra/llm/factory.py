"""Build a ChatClient for a task from env (spec t_d29ed3c9 §1.4)."""
from __future__ import annotations

import os
from typing import Mapping, Optional

from src.server.domain.llm.port import LLMNotConfigured
from src.server.infra.llm.openrouter_chat import OpenRouterChat
from src.server.infra.llm.provider import OPENROUTER_BASE_URL, _secret
from src.server.domain.oracle import LoopCaps, Oracle
from src.server.infra.llm.tasks import LOOPS, TASKS

MIN_TIMEOUT_S = 30.0


def build_chat_client(task: str, env: Optional[Mapping[str, str]] = None, **client_kwargs) -> OpenRouterChat:
    env = os.environ if env is None else env
    spec = TASKS.get(task)
    if spec is None:
        raise ValueError(f"unknown LLM task {task!r}")
    if (env.get("LLM_PROVIDER") or "openrouter").strip().lower() != "openrouter":
        raise LLMNotConfigured("LLM_PROVIDER is not openrouter")
    key = _secret("OPENROUTER_API_KEY", env)
    if not key:
        raise LLMNotConfigured("OPENROUTER_API_KEY is not set")
    model = (env.get(f"LLM_MODEL_{task.upper()}") or env.get("LLM_MODEL") or spec.model).strip()
    timeout = spec.timeout_s
    raw = (env.get("LLM_TIMEOUT_S") or "").strip()
    if raw:
        try:
            timeout = max(float(raw), MIN_TIMEOUT_S)
        except ValueError:
            pass
    base = (env.get("OPENROUTER_BASE_URL") or "").strip() or OPENROUTER_BASE_URL
    return OpenRouterChat(key, model, base_url=base, timeout=timeout, max_tokens=spec.max_tokens, **client_kwargs)


def build_oracle(task: str, env: Optional[Mapping[str, str]] = None, **client_kwargs) -> Oracle:
    """Oracle tool loop for a task: chat client plus caps from LOOPS (spec §3).

    Overrides: LLM_LOOP_MAX_TURNS_<TASK>, LLM_LOOP_HARD_USD_<TASK>; invalid values are ignored.
    """
    env = os.environ if env is None else env
    loop = LOOPS.get(task)
    if loop is None:
        raise ValueError(f"unknown LLM task {task!r}")
    client = build_chat_client(task, env, **client_kwargs)
    suffix = task.upper()
    max_turns, hard, soft = loop.max_turns, loop.hard_cost_usd, loop.soft_cost_usd
    raw = (env.get(f"LLM_LOOP_MAX_TURNS_{suffix}") or "").strip()
    if raw:
        try:
            if int(raw) >= 1:
                max_turns = int(raw)
        except ValueError:
            pass
    raw = (env.get(f"LLM_LOOP_HARD_USD_{suffix}") or "").strip()
    if raw:
        try:
            v = float(raw)
            if v > 0 and v == v and v != float("inf"):
                hard = v
        except ValueError:
            pass
    soft = min(soft, hard)
    caps = LoopCaps(max_turns=max_turns, max_tool_calls=loop.max_tool_calls, soft_cost_usd=soft,
                    hard_cost_usd=hard, max_tokens_in=loop.max_tokens_in, deadline_s=loop.deadline_s)
    return Oracle(client, caps)
