"""Pure helpers for the vector index: embedding-text composition and the
deck-centroid math (ARCHITECTURE.md §4.3). No database, no network, no model
loading — importable and testable anywhere.
"""
from __future__ import annotations

from typing import Iterable, List, Mapping, Optional, Sequence

# Same exclusion as analytics/card2vec.py: basic lands carry no signal.
BASICS = {
    "Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes",
    "Snow-Covered Plains", "Snow-Covered Island", "Snow-Covered Swamp",
    "Snow-Covered Mountain", "Snow-Covered Forest", "Snow-Covered Wastes",
}

# deck_cards boards that count as deck membership for the centroid.
# 'considering' is explicitly not part of the deck (ARCHITECTURE.md §4.2).
CENTROID_BOARDS = ("commander", "main", "sideboard", "maybe")


def card_embedding_text(card: Mapping, tags: Iterable[str] = ()) -> str:
    """The passage text embedded for one card (bge 'passage' kind: no query
    prefix). Oracle facts first, then keywords, then learned tags (§4.3:
    'from oracle text + learned tags')."""
    parts = [card.get("name") or "", card.get("type_line") or "",
             card.get("oracle_text") or ""]
    keywords = [k for k in (card.get("keywords") or []) if k]
    if keywords:
        parts.append("Keywords: " + ", ".join(keywords))
    tag_list = sorted({t for t in tags if t})
    if tag_list:
        parts.append("Tags: " + ", ".join(tag_list))
    return "\n".join(p for p in parts if p)


def centroid(vectors: Sequence[Sequence[float]]) -> Optional[List[float]]:
    """Normalized mean of member vectors — the same math as the card2vec
    deck centroid (sum, then divide by the L2 norm). Member vectors are
    already unit-length (bge embeddings are L2-normalized), and each card
    counts once regardless of qty. Returns None for an empty deck."""
    if not vectors:
        return None
    import numpy as np

    total = np.asarray(vectors, dtype=np.float32).sum(axis=0)
    norm = float(np.linalg.norm(total))
    if not norm:
        return None
    return (total / norm).astype("float32").tolist()


def to_vector_literal(vec: Sequence[float]) -> str:
    """pgvector text input format: '[0.1,0.2,...]'. repr() keeps full float
    round-trip precision so drop-and-rebuild is byte-identical."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"
