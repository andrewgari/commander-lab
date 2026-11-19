"""Reserved interface for an interactive game-state loop (D3). Not implemented."""
from __future__ import annotations

from typing import Any, Mapping, Protocol, Tuple


class PlaySession(Protocol):
    """Reserved: an interactive game-state loop (oracle as a player/advisor). Not implemented."""

    def step(self, observation: Mapping[str, Any]) -> Mapping[str, Any]: ...


PLAY_TOOLS: Tuple[str, ...] = ()  # reserved registry slot; no tools in v1


def start_play(*_a: Any, **_k: Any) -> PlaySession:
    raise NotImplementedError(
        "oracle play is reserved; see docs/superpowers/specs/2026-11-10-oracle-tool-loop-design.md §8")
