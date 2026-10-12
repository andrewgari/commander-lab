"""
card2vec (AN-7, ANALYTICS_SPEC Phase 2): "played-with" similarity learned
from real Commander decklists.

word2vec over decklists: each deck is a sentence, each card a word. Two
cards are close when they keep turning up in the same decks. The model never
sees card text, so this is a different signal from pillar fit (bge-small on
oracle text): pillar fit says "this card reads like your plan", played-with
says "decks like yours actually run this card".

Artifact: analytics/data/card2vec.npz (trained offline by
scripts/train_card2vec.py, see docs/CARD2VEC.md), override with
CARD2VEC_PATH. Arrays:
    keys      name_key per row (lower-cased front face)
    names     display name per row
    vectors   float16 [vocab, dim], L2-normalised
    counts    decks each card was seen in (training corpus)
    meta      JSON: sources, deck counts, params, calibration
Loaded once per process, lazily (~5 MB in RAM). No network, no Redis.

Deck similarity ("played-with"):
    deck centroid = normalised mean of the vectors of the deck's non-land,
                    non-basic cards the model knows (commanders included)
    card sim      = cos(card, centroid); for a card already in the deck the
                    card is left out of its own centroid
    score         = scale(sim) in 0..1, using the artifact's calibration:
                    sim_floor (median sim of a random card to a deck) -> 0,
                    sim_ceil (p90 sim of a card to its own deck) -> 1
Cards the model doesn't know (new sets, very rare cards) get None, never 0:
unknown isn't the same as "nobody plays this".
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

log = logging.getLogger("commander_lab.card2vec")

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "card2vec.npz")
BASICS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes", "Snow-Covered Plains",
          "Snow-Covered Island", "Snow-Covered Swamp", "Snow-Covered Mountain", "Snow-Covered Forest",
          "Snow-Covered Wastes"}


def name_key(name: str) -> str:
    """Same key as analytics/edhrec_inclusion.name_key: lower-cased front face.
    EDHTop16 writes 'A // B', some sources write 'A / B'; both map to 'a'."""
    n = (name or "").split(" // ")[0]
    return n.strip().lower()


class Card2Vec:
    def __init__(self, path: Optional[str] = None):
        self.path = path or os.getenv("CARD2VEC_PATH") or DEFAULT_PATH
        self._lock = threading.Lock()
        self._loaded = False
        self._error: Optional[str] = None
        self.keys: List[str] = []
        self.names: List[str] = []
        self.index: Dict[str, int] = {}
        self.vectors = None
        self.counts = None
        self.meta: dict = {}

    # -- loading ------------------------------------------------------------
    def _load(self) -> bool:
        if self._loaded:
            return self.vectors is not None
        with self._lock:
            if self._loaded:
                return self.vectors is not None
            try:
                import numpy as np

                with np.load(self.path, allow_pickle=False) as z:
                    self.keys = [str(k) for k in z["keys"]]
                    self.names = [str(k) for k in z["names"]]
                    self.vectors = z["vectors"].astype(np.float32)
                    self.counts = z["counts"]
                    self.meta = json.loads(str(z["meta"]))
                self.index = {k: i for i, k in enumerate(self.keys)}
                log.info("card2vec: loaded %d cards x %d from %s", len(self.keys), self.vectors.shape[1], self.path)
            except FileNotFoundError:
                self._error = f"no card2vec artifact at {self.path}"
            except Exception as exc:  # noqa: BLE001 - a bad artifact disables the signal, never the page
                self._error = f"card2vec load failed: {exc}"
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

    @property
    def calibration(self) -> dict:
        self._load()
        return dict(self.meta.get("calibration") or {})

    def info(self) -> dict:
        if not self._load():
            return {"status": "unavailable", "error": self._error}
        m = self.meta
        return {"status": "ok", "model_id": m.get("model_id"), "trained_at": m.get("trained_at"),
                "decks": m.get("decks"), "vocab": m.get("vocab"), "dim": m.get("dim"),
                "sources": m.get("sources"), "calibration": m.get("calibration")}

    # -- math ---------------------------------------------------------------
    def scale(self, sim: Optional[float]) -> Optional[float]:
        if sim is None:
            return None
        cal = self.calibration
        lo, hi = cal.get("sim_floor", 0.0), cal.get("sim_ceil", 1.0)
        span = (hi - lo) or 1.0
        return round(min(1.0, max(0.0, (sim - lo) / span)), 4)

    def has(self, name: str) -> bool:
        return self._load() and name_key(name) in self.index

    def vector(self, name: str):
        if not self._load():
            return None
        i = self.index.get(name_key(name))
        return None if i is None else self.vectors[i]

    def similarity(self, a: str, b: str) -> Optional[float]:
        va, vb = self.vector(a), self.vector(b)
        if va is None or vb is None:
            return None
        return round(float(va @ vb), 4)

    def neighbours(self, name: str, n: int = 10, exclude: Iterable[str] = ()) -> List[dict]:
        v = self.vector(name)
        if v is None:
            return []
        import numpy as np

        sims = self.vectors @ v
        skip = {name_key(name)} | {name_key(x) for x in exclude}
        out = []
        for i in np.argsort(-sims):
            if self.keys[i] in skip:
                continue
            out.append({"name": self.names[i], "sim": round(float(sims[i]), 4), "decks": int(self.counts[i])})
            if len(out) >= n:
                break
        return out

    def _deck_ids(self, deck_cards: Iterable[str]) -> List[int]:
        ids, seen = [], set()
        for n in deck_cards:
            if not n or n in BASICS:
                continue
            i = self.index.get(name_key(n))
            if i is not None and i not in seen:
                seen.add(i)
                ids.append(i)
        return ids

    def deck_scores(self, deck_cards: Sequence[str], candidates: Iterable[str] = ()) -> dict:
        """Played-with scores for every deck card (leave-one-out) and for each
        candidate (vs the whole deck). Unknown cards are left out (None)."""
        if not self._load():
            return {"status": "unavailable", "error": self._error, "deck": {}, "candidates": {}}
        import numpy as np

        ids = self._deck_ids(deck_cards)
        if len(ids) < 5:
            return {"status": "too_few", "known": len(ids), "deck": {}, "candidates": {}}
        m = self.vectors[ids]
        total = m.sum(axis=0)
        cent = total / (np.linalg.norm(total) or 1.0)
        in_deck = {self.keys[i] for i in ids}
        deck_out: Dict[str, dict] = {}
        for n in deck_cards:
            k = name_key(n)
            i = self.index.get(k)
            if i is None or k not in in_deck or n in deck_out:
                continue
            c = total - self.vectors[i]
            c = c / (np.linalg.norm(c) or 1.0)
            sim = float(self.vectors[i] @ c)
            deck_out[n] = {"sim": round(sim, 4), "score": self.scale(sim)}
        cand_out: Dict[str, dict] = {}
        for n in candidates:
            i = self.index.get(name_key(n))
            if i is None or n in cand_out:
                continue
            sim = float(self.vectors[i] @ cent)
            cand_out[n] = {"sim": round(sim, 4), "score": self.scale(sim)}
        return {"status": "ok", "known": len(ids), "deck": deck_out, "candidates": cand_out,
                "model_id": self.meta.get("model_id")}

    def deck_view(self, deck_cards: Sequence[str], exclude_lands: Iterable[str] = (), top: int = 8) -> dict:
        """Stats page payload: the deck's most and least 'played-with' cards
        plus coverage. Lands are passed in by the caller (composition rows)."""
        info = self.info()
        if info["status"] != "ok":
            return {"status": "unavailable", "error": info.get("error"), "model": info}
        lands = set(exclude_lands or ())
        cards = [n for n in deck_cards if n not in lands and n not in BASICS]
        sc = self.deck_scores(cards)
        if sc["status"] != "ok":
            return {"status": sc["status"], "model": info, "known": sc.get("known", 0), "total": len(set(cards))}
        rows = sorted(({"name": n, **v} for n, v in sc["deck"].items()), key=lambda r: (-r["sim"], r["name"]))
        unknown = sorted({n for n in cards if n not in sc["deck"]})
        scores = [r["score"] for r in rows if r["score"] is not None]
        return {
            "status": "ok", "model": info,
            "known": len(rows), "total": len(set(cards)), "unknown": unknown,
            "mean": round(sum(scores) / len(scores), 4) if scores else None,
            "most": rows[:top], "least": list(reversed(rows[-top:])) if len(rows) > top else list(reversed(rows)),
            "per_card": {r["name"]: r["score"] for r in rows},
        }


_default: Optional[Card2Vec] = None
_default_lock = threading.Lock()


def default_model() -> Card2Vec:
    global _default
    with _default_lock:
        if _default is None:
            _default = Card2Vec()
        return _default


def deck_card_names(deck: Mapping) -> List[str]:
    names = [c.get("name") for c in deck.get("cards") or [] if c.get("name")]
    for c in deck.get("commanders") or []:
        if c not in names:
            names.append(c)
    return names
