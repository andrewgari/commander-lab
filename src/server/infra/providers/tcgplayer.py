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


def _qty(raw) -> int:
    """Quantity as the buy lists display it: missing/None/"" means 1 (the
    templates render `card.qty or 1`); anything unparseable means 0 (dropped).
    Mirrored by qtyOf() in static/js/tcgplayer_cart.js."""
    if raw is None or raw == "":
        return 1
    try:
        return int(float(raw))
    except (TypeError, ValueError, OverflowError):
        return 0


def _set_code(raw) -> str:
    """TCGplayer set code for printing-specific cart lines, e.g. "sld" -> "SLD".

    Mass Entry reads `1 Lightning Bolt [SLD]` as "that printing"; without the
    bracketed code any printing is acceptable. Blank/unparseable -> "" (line
    falls back to any printing).
    """
    return str(raw or "").strip().upper()


def cart_items(cards: Iterable[Mapping] | None) -> list[dict]:
    """Normalized cart contents: [{name, qty}] (+ "set" when given) in
    first-seen order.

    - Duplicate names (after double-faced normalization) with the SAME set
      code are merged, summing quantities; different printings stay separate.
    - Cards with a blank name or qty <= 0 are dropped.
    """
    merged: dict[tuple, dict] = {}
    for card in cards or []:
        if not isinstance(card, Mapping):
            continue
        name = str(card.get("name") or "").strip()
        if not name:
            continue
        qty = _qty(card.get("qty"))
        if qty <= 0:
            continue
        front = _front_face(name)
        set_code = _set_code(card.get("set"))
        key = (front, set_code)
        if key not in merged:
            merged[key] = {"name": front, "qty": 0, **({"set": set_code} if set_code else {})}
        merged[key]["qty"] += qty
    return list(merged.values())


def build_tcgplayer_url(cards: Iterable[Mapping]) -> str:
    """Build a TCGplayer mass-entry cart URL from a list of {name, qty} dicts.

    Contents follow cart_items(). An empty (or entirely zero-qty) input
    returns the bare base URL with no `c` param.
    """
    items = cart_items(cards)
    if not items:
        return BASE_URL

    lines = [f"{it['qty']} {it['name']}" + (f" [{it['set']}]" if it.get("set") else "") for it in items]
    c_param = LINE_SEPARATOR.join(lines)
    return f"{BASE_URL}?c={quote(c_param, safe='')}&productline=Magic"


def cart(cards: Iterable[Mapping] | None) -> dict:
    """Everything the cart button needs: {items, count, url}.

    Jinja global `tcgplayer_cart` (app.py), consumed by the hud.tcg_cart
    macro. `count` is total copies; `url` is "" when there is nothing to buy
    so the button can render nothing.
    """
    items = cart_items(cards)
    return {
        "items": items,
        "count": sum(it["qty"] for it in items),
        "url": build_tcgplayer_url(items) if items else "",
    }
