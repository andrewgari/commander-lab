#!/usr/bin/env python3
"""
Train card2vec (AN-7): word2vec over real Commander decklists. Each deck is
one "sentence" and each card is a "word", so cards that get played together
end up close together. The app never trains; it only loads the artifact this
script writes (analytics/data/card2vec.npz, read by analytics/card2vec.py).

Offline only. Needs gensim, which is NOT in requirements.txt:

    pip install gensim numpy
    python scripts/fetch_card2vec_data.py data/edhtop16.ndjson     # EDHTop16 decklists
    curl -O https://mtgjson.com/api/v5/AllDeckFiles.tar.xz && tar -xJf AllDeckFiles.tar.xz
    python scripts/train_card2vec.py --edhtop16 data/edhtop16.ndjson \
        --mtgjson AllDeckFiles --out analytics/data/card2vec.npz

Inputs (see docs/CARD2VEC.md for the source choice and terms):
    --edhtop16  ndjson lines {"commander": "A / B", "cards": [...]} written by
                scripts/fetch_card2vec_data.py (EDHTop16 public GraphQL API,
                tournament decklists from TopDeck.gg)
    --mtgjson   MTGJSON AllDeckFiles dir; only type == "Commander Deck" (the
                official precons, MIT licence) are used

Cleaning: basic lands are dropped (every deck plays them); the commander(s)
are added to the deck as cards; identical lists (the same player
re-registering a deck) are kept once. Names are keyed like the rest of the
app: lower-cased front face (analytics/card2vec.name_key).

Model: skip-gram, negative sampling, window = the whole deck (the deck is
an unordered set, so every card is context for every other card; the
sentence is reshuffled each epoch so the window never depends on order),
sample=1e-3 downsamples staples like Sol Ring. Vectors are L2-normalised
and stored as float16.

Calibration (written into the artifact's meta): for held-out decks we take
cos(card, centroid of the other cards in its deck) for cards that ARE in the
deck vs cards sampled from the vocabulary that are not. sim_floor = the
median of the random sims, sim_ceil = the 90th percentile of the in-deck
sims, so `scale()` maps "random card" to ~0 and "clearly belongs" to ~1.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.card2vec import BASICS, name_key  # noqa: E402


def _commanders(raw) -> list:
    return [c.strip() for c in (raw or "").split(" / ") if c.strip()]


def load_edhtop16(paths):
    for p in paths:
        with open(p) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                yield "edhtop16", _commanders(d.get("commander")), d.get("cards") or []


def load_mtgjson(root):
    for f in sorted(glob.glob(os.path.join(root, "**", "*.json"), recursive=True)):
        try:
            d = json.load(open(f)).get("data") or {}
        except (ValueError, OSError):
            continue
        if d.get("type") != "Commander Deck":
            continue
        cmd = [c["name"] for c in d.get("commander") or []]
        cards = [c["name"] for c in (d.get("mainBoard") or [])]
        yield "mtgjson", cmd, cards


def build_corpus(sources):
    seen = set()
    decks, display, stats = [], {}, {"raw": 0, "dupes": 0, "short": 0, "by_source": {}}
    for src, cmd, cards in sources:
        stats["raw"] += 1
        names = []
        for n in list(cmd) + list(cards):
            if not n or n in BASICS:
                continue
            k = name_key(n)
            if not k or k in names:
                continue
            names.append(k)
            display.setdefault(k, n)
        if len(names) < 40:
            stats["short"] += 1
            continue
        h = hashlib.sha1("\n".join(sorted(names)).encode()).hexdigest()
        if h in seen:
            stats["dupes"] += 1
            continue
        seen.add(h)
        decks.append(names)
        stats["by_source"][src] = stats["by_source"].get(src, 0) + 1
    return decks, display, stats


class Shuffled:
    """Re-iterable corpus that reshuffles every deck on every pass."""

    def __init__(self, decks, seed=7):
        self.decks, self.rng = decks, random.Random(seed)

    def __iter__(self):
        for d in self.decks:
            d = list(d)
            self.rng.shuffle(d)
            yield d


def calibrate(vecs, index, decks, rng, n_decks=1500):
    words = list(index)
    inside, rand = [], []
    for d in rng.sample(decks, min(n_decks, len(decks))):
        ids = [index[k] for k in d if k in index]
        if len(ids) < 20:
            continue
        m = vecs[ids]
        tot = m.sum(axis=0)
        dset = set(d)
        for j in rng.sample(range(len(ids)), min(5, len(ids))):
            c = tot - m[j]
            c /= np.linalg.norm(c) or 1.0
            inside.append(float(m[j] @ c))
            w = rng.choice(words)
            while w in dset:
                w = rng.choice(words)
            rand.append(float(vecs[index[w]] @ (tot / (np.linalg.norm(tot) or 1.0))))
    inside, rand = np.array(inside), np.array(rand)
    return {
        "sim_floor": round(float(np.median(rand)), 4),
        "sim_ceil": round(float(np.percentile(inside, 90)), 4),
        "in_deck_median": round(float(np.median(inside)), 4),
        "random_median": round(float(np.median(rand)), 4),
        "in_deck_p10": round(float(np.percentile(inside, 10)), 4),
        "random_p90": round(float(np.percentile(rand, 90)), 4),
        "samples": int(len(inside)),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--edhtop16", nargs="*", default=[])
    ap.add_argument("--mtgjson")
    ap.add_argument("--out", default="analytics/data/card2vec.npz")
    ap.add_argument("--dim", type=int, default=64)
    ap.add_argument("--min-count", type=int, default=5)
    ap.add_argument("--epochs", type=int, default=15)
    ap.add_argument("--negative", type=int, default=10)
    ap.add_argument("--sample", type=float, default=1e-3)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)

    from gensim.models import Word2Vec

    sources = []
    sources.append(load_edhtop16(a.edhtop16))
    if a.mtgjson:
        sources.append(load_mtgjson(a.mtgjson))
    decks, display, stats = build_corpus(s for gen in sources for s in gen)
    dates = []
    for p in a.edhtop16:
        with open(p) as fh:
            dates.extend((json.loads(line).get("date") or "")[:10] for line in fh if line.strip())
    dates = sorted(d for d in dates if d)
    stats["edhtop16_dates"] = [dates[0], dates[-1]] if dates else None
    print(f"corpus: {len(decks)} decks after cleaning; {json.dumps(stats)}", flush=True)
    if len(decks) < 500:
        sys.exit("not enough decks to train")

    rng = random.Random(a.seed)
    rng.shuffle(decks)
    held = decks[: max(200, len(decks) // 20)]
    train = decks[len(held):]

    t0 = time.time()
    model = Word2Vec(vector_size=a.dim, window=200, shrink_windows=False, min_count=a.min_count, sg=1,
                     negative=a.negative, sample=a.sample, workers=a.workers, seed=a.seed)
    model.build_vocab(Shuffled(train, a.seed))
    model.train(Shuffled(train, a.seed + 1), total_examples=len(train), epochs=a.epochs)
    print(f"trained {len(model.wv)} cards x {a.dim}d in {time.time() - t0:.0f}s", flush=True)

    keys = list(model.wv.index_to_key)
    vecs = np.asarray(model.wv.vectors, dtype=np.float32)
    vecs /= np.clip(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-12, None)
    counts = np.array([model.wv.get_vecattr(k, "count") for k in keys], dtype=np.int32)
    index = {k: i for i, k in enumerate(keys)}
    cal = calibrate(vecs, index, held, rng)
    print("calibration (held-out decks):", json.dumps(cal), flush=True)

    meta = {
        "model_id": f"card2vec-w2v-{a.dim}d-{datetime.now(timezone.utc):%Y%m%d}",
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "decks": len(decks), "train_decks": len(train), "held_out_decks": len(held),
        "by_source": stats["by_source"], "raw_lists": stats["raw"], "duplicates_dropped": stats["dupes"],
        "vocab": len(keys), "dim": a.dim,
        "params": {"sg": 1, "window": "whole deck", "min_count": a.min_count, "epochs": a.epochs,
                   "negative": a.negative, "sample": a.sample, "seed": a.seed},
        "calibration": cal,
        "sources": [
            {"name": "EDHTop16 public GraphQL API (tournament decklists from TopDeck.gg)",
             "url": "https://edhtop16.com", "decks": stats["by_source"].get("edhtop16", 0),
             "dates": stats.get("edhtop16_dates")},
            {"name": "MTGJSON AllDeckFiles, official Commander precons (MIT)",
             "url": "https://mtgjson.com", "decks": stats["by_source"].get("mtgjson", 0)},
        ],
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    np.savez_compressed(a.out, keys=np.array(keys), names=np.array([display.get(k, k) for k in keys]),
                        vectors=vecs.astype(np.float16), counts=counts,
                        meta=np.array(json.dumps(meta)))
    print(f"wrote {a.out} ({os.path.getsize(a.out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
