"""v1 oracle tools and the per-run registry factory."""
from __future__ import annotations

from typing import Callable, Dict, Iterable

from ..registry import Tool, ToolRegistry
from .cards import card_lookup_tool
from .context import ToolContext
from .deck import get_deck_tool
from .sim import SimRunner, sim_run_tool
from .stats import deck_stats_tool

FACTORIES: Dict[str, Callable[[], Tool]] = {
    "get_deck": get_deck_tool,
    "card_lookup": card_lookup_tool,
    "deck_stats": deck_stats_tool,
    "sim_run": sim_run_tool,
}


def build_registry(names: Iterable[str], ctx: ToolContext) -> ToolRegistry:
    """A fresh ToolRegistry for one run (failure/call counters are per instance)."""
    tools = []
    for n in names:
        if n not in FACTORIES:
            raise ValueError(f"unknown oracle tool: {n!r}")
        tools.append(FACTORIES[n]())
    return ToolRegistry(tools)


__all__ = ["SimRunner", "ToolContext", "build_registry", "FACTORIES"]
