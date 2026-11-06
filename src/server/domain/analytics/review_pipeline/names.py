"""Deterministic card-name resolution against the deck's own card list.

An LLM-emitted name is accepted only if it resolves to a card that is actually in the deck
(CONTRACT §2.2). Resolution forgives typography (case, accents, curly quotes, double-faced
front/back face) and nothing else: no fuzzy matching, so a hallucinated name never "close-matches"
into a real one.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Mapping

_QUOTES = {"\u2018": "'", "\u2019": "'", "\u201b": "'", "\u2032": "'", "\u201c": '"', "\u201d": '"'}


def strip_typography(text: str) -> str:
    """Curly quotes -> straight, accents removed, whitespace collapsed. Case is kept."""
    s = (text or "").strip()
    for a, b in _QUOTES.items():
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s)


def normalize(name: str) -> str:
    return strip_typography(name).casefold()


class CardResolver:
    """``cards``: canonical name -> oracle_id (None when unknown)."""

    def __init__(self, cards: Mapping[str, str | None]):
        self._ids = dict(cards)
        self._by_norm: dict[str, str] = {}
        ambiguous: set[str] = set()
        for canon in self._ids:
            keys = {normalize(canon)}
            if " // " in canon:
                keys.update(normalize(face) for face in canon.split(" // "))
            for k in keys:
                if k in self._by_norm and self._by_norm[k] != canon:
                    ambiguous.add(k)
                self._by_norm.setdefault(k, canon)
        for k in ambiguous:      # a face name shared by two different deck entries: refuse to guess
            self._by_norm.pop(k, None)

    @property
    def names(self) -> list[str]:
        return list(self._ids)

    def resolve(self, name: str) -> str | None:
        return self._by_norm.get(normalize(name)) if name else None

    def oracle_id(self, canonical: str) -> str | None:
        return self._ids.get(canonical)
