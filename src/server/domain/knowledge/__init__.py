"""In-repo MTG knowledge pack for the LLM reviewer.

``build_messages`` assembles the chat messages for a task: a cacheable static system block
(rules, brackets, mana math, salt, Commander Salt structure, task text), then the dynamic Game
Changers list, then a user message with the FACTS block. Nothing here performs I/O beyond
reading packaged files; live facts are passed in by the caller.
"""
from __future__ import annotations

import hashlib
from typing import Any

from . import render

PACK_VERSION = "kp-1"
MAX_STATIC_CHARS = 48_000

_RULES = ("core",)
_BRACKETS = "brackets"
_MANA = "mana"
_SALT = "salt_and_table"
_CSALT = "commandersalt_structure"

# task -> (task file or None, sections in order). Tasks with no file carry their prompt in user_text.
TASKS: dict[str, tuple[str | None, tuple[str, ...]]] = {
    "deck_review": ("deck_review", ("core", _BRACKETS, _MANA, _SALT, _CSALT)),
    "sim_review": ("deck_sim_review", ("core", _BRACKETS)),
    "theory": ("deck_theory", ("core", _BRACKETS, _MANA, _SALT)),
    "personality_read": ("personality_read", ("core", _BRACKETS, _SALT, _CSALT)),
    "card_review": (None, ("core", _BRACKETS, _MANA, _SALT)),
    "reconstruct": (None, ("core", _BRACKETS, _MANA)),
    "genre_autofill": (None, ("core",)),
    "deck_writeup": (None, ("core",)),
    "sim_diagnosis": (None, ("core",)),
    "review_writer": (None, ("core",)),
}
SCHEMAS = {"deck_review": "review", "sim_review": "sim_review", "theory": "theory",
           "personality_read": "personality-read"}
_GC_TASKS = {t for t, (_, secs) in TASKS.items() if _BRACKETS in secs}


def _section(name: str) -> str:
    if name == _BRACKETS:
        return render.render_brackets()
    if name == _MANA:
        return render.render_mana_math()
    return render.read_static(name)


def static_parts(task: str) -> list[str]:
    if task not in TASKS:
        raise ValueError(f"unknown knowledge task {task!r}")
    task_file, sections = TASKS[task]
    parts = [_section(s) for s in sections]
    text = render.read_task(task_file) if task_file else None
    if text:
        parts.append(text)
    return parts


def _static_text(task: str) -> str:
    return "\n\n".join(static_parts(task))


def pack_hash(task: str | None = None) -> str:
    """sha256 hex of the static pack text for ``task`` (or of every task's, sorted, if None)."""
    if task is not None:
        return hashlib.sha256(_static_text(task).encode("utf-8")).hexdigest()
    h = hashlib.sha256()
    for t in sorted(TASKS):
        h.update(t.encode() + b"\0" + _static_text(t).encode("utf-8") + b"\0")
    return h.hexdigest()


def build_messages(task: str, *, game_changers: Any = None, facts_json: str = "",
                   user_text: str = "") -> tuple[list[dict[str, Any]], str]:
    """Return ``(messages, pack_id)``.

    ``messages[0]`` is the system message: the static parts (ephemeral cache_control on the last
    static part) followed by the dynamic Game Changers part (only for tasks that load Brackets).
    ``messages[1]`` is the user message: ``facts_json + "\\n\\n" + user_text``.
    """
    static = _static_text(task)
    if len(static) > MAX_STATIC_CHARS:
        raise ValueError(f"static pack for {task!r} is {len(static)} chars (max {MAX_STATIC_CHARS})")
    system: list[dict[str, Any]] = [{"type": "text", "text": static, "cache_control": {"type": "ephemeral"}}]
    if task in _GC_TASKS:
        system.append({"type": "text", "text": render.render_game_changers(game_changers)})
    pack_id = f"{PACK_VERSION}:{hashlib.sha256(static.encode('utf-8')).hexdigest()[:12]}"
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": f"{facts_json}\n\n{user_text}"}]
    return messages, pack_id


__all__ = ["PACK_VERSION", "TASKS", "build_messages", "pack_hash", "static_parts"]
