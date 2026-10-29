"""
Local text embeddings for pillar fit (AN-3).

Model: BAAI bge-small-en-v1.5 (384-d, MIT), int8-quantized ONNX export from
Xenova/bge-small-en-v1.5 (pinned revision, ~34 MB). Runs on CPU with
onnxruntime + the HF `tokenizers` library: no torch, no GPU, no network at
runtime. The Docker image downloads it at build time into
EMBEDDINGS_MODEL_DIR (default /opt/models/bge-small-en-v1.5).

bge conventions: queries (pillar descriptions) get the retrieval
instruction prefix, passages (card text) don't; the CLS vector is
L2-normalized, so cosine == dot product.

Calibration (on_pillar_sim / sim_floor / sim_ceil) was measured on Holy
Hellfire against hand-written pillars: card<->pillar cosines sit in roughly
0.49..0.77 (4 pillars), clearly on-theme cards >= ~0.62; distinct pillars sit at 0.48..0.59 from
each other and a reworded duplicate at 0.92, so dup_sim 0.85 separates them. Scores are rescaled to 0..1 with the
floor/ceil so the UI's 2-decimal fit reads like a percentage.
"""
from __future__ import annotations

import base64
import logging
import math
import os
import struct
import threading
from typing import List, Optional, Sequence

log = logging.getLogger("commander_lab.embeddings")

DEFAULT_MODEL_DIR = "/opt/models/bge-small-en-v1.5"
MODEL_FILE = "model_quantized.onnx"
TOKENIZER_FILE = "tokenizer.json"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def encode_vector(v: Sequence[float]) -> str:
    return base64.b64encode(struct.pack(f"<{len(v)}f", *v)).decode("ascii")


def decode_vector(s: str) -> List[float]:
    raw = base64.b64decode(s)
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class Embedder:
    """Interface. `embed(texts, kind)` -> list of unit vectors.
    kind = "query" (pillar text) or "passage" (card text)."""

    model_id: str = ""
    dim: int = 0
    on_pillar_sim: float = 0.6   # best-pillar cosine at/above this = the card is on a pillar
    sim_floor: float = 0.45      # cosine mapped to fit 0.0
    sim_ceil: float = 0.80       # cosine mapped to fit 1.0
    dup_sim: float = 0.85        # pillar<->pillar cosine at/above this = same pillar (UX_SPEC §3.2)

    def available(self) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def embed(self, texts: List[str], kind: str = "passage") -> List[List[float]]:  # pragma: no cover
        raise NotImplementedError

    def scale(self, sim: float) -> float:
        span = (self.sim_ceil - self.sim_floor) or 1.0
        return round(min(1.0, max(0.0, (sim - self.sim_floor) / span)), 4)


class OnnxEmbedder(Embedder):
    model_id = "bge-small-en-v1.5-int8"
    dim = 384
    on_pillar_sim = 0.62
    sim_floor = 0.48
    sim_ceil = 0.78
    dup_sim = 0.85

    def __init__(self, model_dir: Optional[str] = None, threads: Optional[int] = None, batch_size: int = 32,
                 max_tokens: int = 256):
        self.model_dir = model_dir or os.getenv("EMBEDDINGS_MODEL_DIR") or DEFAULT_MODEL_DIR
        self.threads = threads or int(os.getenv("EMBEDDINGS_THREADS", "2"))
        self.batch_size = batch_size
        self.max_tokens = max_tokens
        self._session = None
        self._tokenizer = None
        self._inputs: List[str] = []
        self._lock = threading.Lock()
        self._error: Optional[str] = None

    def _paths(self):
        return os.path.join(self.model_dir, MODEL_FILE), os.path.join(self.model_dir, TOKENIZER_FILE)

    def available(self) -> bool:
        model, tok = self._paths()
        if not (os.path.exists(model) and os.path.exists(tok)):
            return False
        try:
            import onnxruntime  # noqa: F401
            import tokenizers  # noqa: F401
            import numpy  # noqa: F401
        except ImportError:
            return False
        return True

    @property
    def error(self) -> Optional[str]:
        return self._error

    def _load(self):
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return
            import onnxruntime as ort
            from tokenizers import Tokenizer

            model, tok_path = self._paths()
            tok = Tokenizer.from_file(tok_path)
            tok.enable_truncation(self.max_tokens)
            tok.enable_padding()
            so = ort.SessionOptions()
            so.intra_op_num_threads = self.threads
            so.inter_op_num_threads = 1
            sess = ort.InferenceSession(model, so, providers=["CPUExecutionProvider"])
            self._inputs = [i.name for i in sess.get_inputs()]
            self._tokenizer = tok
            self._session = sess
            log.info("loaded embedding model %s from %s (%d threads)", self.model_id, self.model_dir, self.threads)

    def embed(self, texts: List[str], kind: str = "passage") -> List[List[float]]:
        import numpy as np

        self._load()
        prefix = QUERY_PREFIX if kind == "query" else ""
        out: List[List[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = [prefix + t for t in texts[i : i + self.batch_size]]
            enc = self._tokenizer.encode_batch(batch)
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self._session.run(None, feed)[0]
            cls = hidden[:, 0]
            cls = cls / np.clip(np.linalg.norm(cls, axis=1, keepdims=True), 1e-12, None)
            out.extend(cls.astype("float32").tolist())
        return out


_default: Optional[Embedder] = None
_default_lock = threading.Lock()


def default_embedder() -> Embedder:
    """Process-wide embedder (the ONNX model is loaded lazily on first use)."""
    global _default
    with _default_lock:
        if _default is None:
            _default = OnnxEmbedder()
        return _default
