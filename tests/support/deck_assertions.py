"""Reusable assertion helpers for Brew output (a draft dict as produced by a
brew builder: {"commanders": [...], "cards": [{"name", "quantity", ...}]},
commanders listed inside `cards`).

Each helper raises DeckAssertionError (an AssertionError) naming the offending
cards, so failures read well under pytest and unittest. `assert_valid_brew_deck`
runs all of them. Independent of deck_validator's checks on purpose: this is a
second pair of eyes on builder output, reusing only its pure data helpers
(name normalization, color parsing, singleton text parsing, banlist snapshot).
"""
from __future__ import annotations

import re
from typing import Any, Iterable, List, Mapping, Optional

from src.server.domain.draft.deck_validator import (
    BASIC_LAND_NAMES,
    COMMANDER_BANNED,
    SINGLETON_EXCEPTIONS,
    CardCatalog,
    normalize_name,
    singleton_limit_from_text,
)

DECK_SIZE = 100

UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
# Bare 32-hex (uuid4().hex style) tokens are also leaked identifiers.
HEX32_RE = re.compile(r"(?<![0-9a-zA-Z])[0-9a-fA-F]{32}(?![0-9a-zA-Z])")


class DeckAssertionError(AssertionError):
    pass


def _cards(deck: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    return list(deck.get("cards") or [])


def _qty(card: Mapping[str, Any]) -> int:
    q = card.get("quantity", 1)
    return q if isinstance(q, int) and not isinstance(q, bool) else 0


def total_cards(deck: Mapping[str, Any]) -> int:
    return sum(_qty(c) for c in _cards(deck))


def assert_deck_size(deck: Mapping[str, Any], size: int = DECK_SIZE) -> None:
    n = total_cards(deck)
    if n != size:
        raise DeckAssertionError(f"deck has {n} cards, expected exactly {size}")
    have = {normalize_name(c.get("name")) for c in _cards(deck)}
    missing = [c for c in deck.get("commanders") or [] if normalize_name(c) not in have]
    if missing:
        raise DeckAssertionError(f"commander(s) not counted in the deck: {missing}")


def assert_names_resolved(deck: Mapping[str, Any]) -> None:
    bad = [c for c in _cards(deck)
           if not isinstance(c.get("name"), str) or not c["name"].strip()
           or c["name"].strip().casefold() in {"none", "null", "unknown", "undefined"}]
    if bad:
        raise DeckAssertionError(f"{len(bad)} card(s) with unresolved/empty name: {bad}")


def assert_no_guid_names(deck: Mapping[str, Any]) -> None:
    bad = [c.get("name") for c in _cards(deck)
           if isinstance(c.get("name"), str) and (UUID_RE.search(c["name"]) or HEX32_RE.search(c["name"]))]
    if bad:
        raise DeckAssertionError(f"GUID/UUID in card name(s): {bad}")


def _copy_limit(name: str, catalog: Optional[CardCatalog]) -> Optional[int]:
    """Max copies allowed; None = unlimited."""
    info = catalog.get(name) if catalog is not None else None
    if name in BASIC_LAND_NAMES or (info is not None and info.is_basic):
        return None
    if info is not None:
        found, limit = singleton_limit_from_text(info.oracle_text)
        if found:
            return limit
    for k, v in SINGLETON_EXCEPTIONS.items():
        if normalize_name(k) == normalize_name(name):
            return v
    return 1


def assert_singleton(deck: Mapping[str, Any], catalog: Optional[CardCatalog] = None) -> None:
    counts: dict = {}
    names: dict = {}
    for c in _cards(deck):
        k = normalize_name(c.get("name"))
        counts[k] = counts.get(k, 0) + _qty(c)
        names.setdefault(k, c.get("name"))
    bad = []
    for k, n in counts.items():
        limit = _copy_limit(names[k], catalog)
        if limit is not None and n > limit:
            bad.append(f"{names[k]} x{n} (max {limit})")
    if bad:
        raise DeckAssertionError(f"singleton violations: {bad}")


def commander_identity(deck: Mapping[str, Any], catalog: CardCatalog) -> frozenset:
    ident: frozenset = frozenset()
    for cmd in deck.get("commanders") or []:
        info = catalog.get(cmd)
        if info is None:
            raise DeckAssertionError(f"commander not in catalog: {cmd!r}")
        ident |= info.color_identity
    return ident


def assert_color_identity(deck: Mapping[str, Any], catalog: CardCatalog) -> None:
    if not deck.get("commanders"):
        raise DeckAssertionError("deck has no commanders")
    allowed = commander_identity(deck, catalog)
    bad, unknown = [], []
    for c in _cards(deck):
        info = catalog.get(c.get("name") or "")
        if info is None:
            unknown.append(c.get("name"))
        elif not info.color_identity <= allowed:
            bad.append(f"{c['name']} ({''.join(sorted(info.color_identity))})")
    if unknown:
        raise DeckAssertionError(f"cards not in catalog, identity unverifiable: {unknown}")
    if bad:
        raise DeckAssertionError(
            f"outside commander identity {''.join(sorted(allowed)) or 'C'}: {bad}")


def assert_not_banned(deck: Mapping[str, Any], catalog: Optional[CardCatalog] = None,
                      banned: Iterable[str] = COMMANDER_BANNED) -> None:
    ban = {normalize_name(n) for n in banned}
    bad = []
    for c in _cards(deck):
        info = catalog.get(c.get("name") or "") if catalog is not None else None
        legality = info.commander_legality if info is not None else None
        # Synced legality wins over the snapshot when present.
        is_banned = (legality == "banned") if legality in ("legal", "banned", "not_legal") \
            else normalize_name(c.get("name")) in ban
        if is_banned or legality == "not_legal":
            bad.append(c.get("name"))
    if bad:
        raise DeckAssertionError(f"banned/illegal card(s): {bad}")


def assert_valid_brew_deck(deck: Mapping[str, Any], catalog: CardCatalog) -> None:
    """All Brew output invariants, in a stable order."""
    assert_names_resolved(deck)
    assert_no_guid_names(deck)
    assert_deck_size(deck)
    assert_singleton(deck, catalog)
    assert_color_identity(deck, catalog)
    assert_not_banned(deck, catalog)
