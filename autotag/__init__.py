"""Auto Tag — intent-aware tag engine for Magic: The Gathering decks.

Implements the autotag-v1 spec: deterministic Pass-1 regex scoring,
Pass-1.5 deck-theme computation, Pass-2 context rules, and
conservatism caps (§6).
"""
from autotag.engine import run_auto_tag, AutoTagConfig

__all__ = ["run_auto_tag", "AutoTagConfig"]
