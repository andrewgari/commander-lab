"""
mech_similar (AN-8): "mechanically similar" cards, a third similarity signal.

    pillar fit (AN-3)    bge-small on oracle text vs the deck's pillars
    played-with (AN-7)   card2vec: cards that turn up in the same decklists
    mechanically similar cards whose rules text does the same thing (this module)

It is NOT synergy and NOT "played with": Sol Ring's neighbours are other mana rocks,
not the cards you play next to it. Source: unit-normalised 768-d gte-modernbert-base
vectors of each card's mechanics-only JSON (MTGJSON up to Aetherdrift, deduped by name),
precomputed in the HF bucket CovaDax/mtg-embeddings-bucket and converted by
scripts/build_mech_similar_artifact.py (docs/MECH_SIMILAR.md). Nothing is embedded at runtime.

Artifact (npz): keys (name_key), names, vectors float16 [n, dim], meta (JSON).
Path: MECH_SIMILAR_PATH, else /data/mech_similar.npz (the persistent volume), else
analytics/data/mech_similar.npz. Loaded once per process, lazily (~50 MB as float16).
A missing or bad artifact disables the signal (empty results), never the page.
Cards newer than the dataset get an empty list, never an error.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from .card2vec import BASICS, name_key  # same name normalisation as card2vec

log = logging.getLogger("commander_lab.mech_similar")

VOLUME_PATH = "/data/mech_similar.npz"
REPO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "mech_similar.npz")

# Oracle feed caps (keep prompts small).
FEED_MAX_CARDS = 12
FEED_PER_CARD = 3
FEED_MIN_SIM = 0.80

__all__ = ["MechSimilar", "default_model", "name_key", "oracle_feed", "similar"]


def resolve_path() -> str:
    env = os.getenv("MECH_SIMILAR_PATH")
    if env:
        return env
    return VOLUME_PATH if os.path.exists(VOLUME_PATH) else REPO_PATH


class MechSimilar:
    def __init__(self, path: Optional[str] = None):
        self._path = path
        self._lock = threading.Lock()
        self._loaded = False
        self._error: Optional[str] = None
        self.path = path or ""
        self.keys: List[str] = []
        self.names: List[str] = []
        self.index: Dict[str, int] = {}
        self.vectors = None
        self.meta: dict = {}

    def _load(self) -> bool:
        if self._loaded:
            return self.vectors is not None
        with self._lock:
            if self._loaded:
                return self.vectors is not None
            self.path = self._path or resolve_path()
            try:
                import numpy as np

                with np.load(self.path, allow_pickle=False) as z:
                    keys = [str(k) for k in z["keys"]]
                    names = [str(k) for k in z["names"]]
                    vecs = z["vectors"].astype(np.float32)
                    meta = json.loads(str(z["meta"]))
                if not isinstance(meta, dict):
                    raise ValueError("meta is not an object")
                if vecs.ndim != 2 or vecs.shape[0] == 0 or vecs.shape[1] == 0:
                    raise ValueError(f"bad vector shape {vecs.shape}")
                if not (len(keys) == len(names) == vecs.shape[0]):
                    raise ValueError("keys/names/vectors row counts differ")
                if not np.isfinite(vecs).all():
                    raise ValueError("non-finite vector values")
                norms = np.linalg.norm(vecs, axis=1, keepdims=True)
                vecs = vecs / np.where(norms == 0, 1.0, norms)
                self.keys, self.names, self.meta = keys, names, meta
                self.vectors = vecs
                self.index = {k: i for i, k in enumerate(self.keys)}
                log.info("mech_similar: loaded %d cards x %d from %s", len(self.keys), self.vectors.shape[1], self.path)
            except FileNotFoundError:
                self._error = f"no mech_similar artifact at {self.path}"
                log.warning("%s; mechanically-similar results are disabled (docs/MECH_SIMILAR.md)", self._error)
            except Exception as exc:  # noqa: BLE001 - a bad artifact disables the signal, never the page
                self._error = f"mech_similar load failed: {exc}"
                log.warning(self._error)
                self.vectors = None
            self._loaded = True
        return self.vectors is not None

    def available(self) -> bool:
        return self._load()

    @property
    def error(self) -> Optional[str]:
        self._load()
        return self._error

    def info(self) -> dict:
        if not self._load():
            return {"status": "unavailable", "error": self._error}
        m = self.meta
        return {"status": "ok", "model_id": m.get("model_id"), "source": m.get("source"),
                "built_at": m.get("built_at"), "cards": len(self.keys), "dim": m.get("dim")}

    def has(self, name: str) -> bool:
        return self._load() and name_key(name) in self.index

    def neighbours(self, name: str, n: int = 10, exclude: Iterable[str] = ()) -> List[dict]:
        """Top-n cards by cosine similarity of rules-text vectors. [] when the card
        isn't in the dataset (newer than Aetherdrift) or the artifact is missing."""
        if not self._load():
            return []
        i = self.index.get(name_key(name))
        if i is None:
            return []
        import numpy as np

        sims = self.vectors @ self.vectors[i]
        skip = {name_key(name)} | {name_key(x) for x in exclude}
        out: List[dict] = []
        for j in np.argsort(-sims):
            if self.keys[j] in skip:
                continue
            out.append({"name": self.names[j], "sim": round(float(sims[j]), 4)})
            if len(out) >= n:
                break
        return out

    def similar(self, card: str, n: int = 10, exclude: Iterable[str] = ()) -> List[dict]:
        """Convenience alias for neighbours, returning top-n cosine neighbours plus scores."""
        return self.neighbours(card, n=n, exclude=exclude)


_default: Optional[MechSimilar] = None
_default_lock = threading.Lock()


def default_model() -> MechSimilar:
    global _default
    with _default_lock:
        if _default is None:
            _default = MechSimilar()
        return _default


def similar(card: str, n: int = 10, exclude: Iterable[str] = ()) -> List[dict]:
    """AN-8 top-level function: returns top-n cosine neighbours plus scores."""
    return default_model().similar(card, n=n, exclude=exclude)


def oracle_feed(cards: Sequence[str], deck_cards: Iterable[str] = (), *, model: Optional[MechSimilar] = None,
                max_cards: int = FEED_MAX_CARDS, per_card: int = FEED_PER_CARD,
                min_sim: float = FEED_MIN_SIM) -> Dict[str, List[dict]]:
    """Compact {card: [{name, sim}, ...]} for the oracle request, in `cards` order.
    Basics and cards with no neighbour above min_sim are left out; at most max_cards
    entries x per_card neighbours. {} when the model is unavailable."""
    m = model or default_model()
    if not m.available():
        return {}
    in_deck = list(deck_cards or ())  # materialize once; excluded BEFORE the per-card limit
    out: Dict[str, List[dict]] = {}
    for c in cards:
        if len(out) >= max_cards:
            break
        if not c or c in BASICS or c in out:
            continue
        nb = [x for x in m.neighbours(c, per_card, exclude=in_deck) if x["sim"] >= min_sim]
        if nb:
            out[c] = [{"name": x["name"], "sim": round(x["sim"], 2)} for x in nb]
    return out
