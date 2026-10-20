"""Shared TCGplayer "Mass Entry" cart URL builder.

TCGplayer's mass-entry page accepts a `c` query param containing
newline-separated `{qty} {name}` lines (TCGplayer itself uses `||` as the
line separator when passed via URL). See
https://www.tcgplayer.com/massentry for the UI this targets.

This module is the single place that knows how to:
  - merge duplicate card names (summing quantities)
  - handle double-faced cards written as "Front // Back" (TCGplayer's
    catalog only indexes the front face, so we submit the front face name)
  - URL-encode the result safely

Usage:
    from tcgplayer import build_tcgplayer_url
    url = build_tcgplayer_url([{"name": "Sol Ring", "qty": 1}, ...])
"""
from __future__ import annotations

from urllib.parse import quote
from typing import Iterable, Mapping

BASE_URL = "https://www.tcgplayer.com/massentry"
LINE_SEPARATOR = "||"


def _front_face(name: str) -> str:
    """Return the front-face name for a double-faced card like "Front // Back".

    TCGplayer's catalog indexes double-faced cards by their front face only,
    so "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki" becomes
    "Fable of the Mirror-Breaker".
    """
    if "//" in name:
        return name.split("//", 1)[0].strip()
    return name.strip()


def build_tcgplayer_url(cards: Iterable[Mapping]) -> str:
    """Build a TCGplayer mass-entry cart URL from a list of {name, qty} dicts.

    - Duplicate names (after double-faced normalization) are merged, summing
      quantities.
    - Cards with qty <= 0 are dropped.
    - An empty (or entirely zero-qty) input returns the bare base URL with
      no `c` param.
    - Card order in the output follows first-seen order of the input.
    """
    merged: dict[str, int] = {}
    for card in cards:
        name = (card.get("name") or "").strip()
        if not name:
            continue
        qty = int(card.get("qty", 1) or 0)
        if qty <= 0:
            continue
        front = _front_face(name)
        merged[front] = merged.get(front, 0) + qty

    if not merged:
        return BASE_URL

    lines = [f"{qty} {name}" for name, qty in merged.items()]
    c_param = LINE_SEPARATOR.join(lines)
    return f"{BASE_URL}?c={quote(c_param, safe='')}&productline=Magic"
