"""Build a ChatClient for a task from env (spec t_d29ed3c9 §1.4)."""
from __future__ import annotations

import os
from typing import Mapping, Optional

from src.server.domain.llm.port import LLMNotConfigured
from src.server.infra.llm.openrouter_chat import OpenRouterChat
from src.server.infra.llm.provider import OPENROUTER_BASE_URL, _secret
from src.server.infra.llm.tasks import TASKS

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
