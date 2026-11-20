"""Tags domain: autotag engine, tag registry, deck/lab tag storage.

Absorbs the old top-level ``autotag`` package (engine + registry) and the
``tags.py`` / ``lab_tags.py`` storage modules.
"""
from src.server.domain.tags.engine import run_auto_tag, AutoTagConfig

__all__ = ["run_auto_tag", "AutoTagConfig"]
