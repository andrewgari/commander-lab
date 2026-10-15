"""Build the "mechanically similar" artifact (AN-8, docs/MECH_SIMILAR.md).

Fetches mtg_embeddings.parquet from the private HF bucket
CovaDax/mtg-embeddings-bucket (a copy of minimaxir/mtg-embeddings: ~33k cards, 768-d
gte-modernbert-base vectors of mechanics-only JSON) and writes a compact npz
that analytics/mech_similar.py loads.

Needs HF_TOKEN (never baked into the image, never committed) and pyarrow, which the
app image does NOT carry. Run it off-box, then copy the npz to the app's /data volume:

    HF_TOKEN=... uv run --with pyarrow --with numpy --with pydantic python scripts/build_mech_similar_artifact.py out/mech_similar.npz
    # or, from an already-downloaded parquet:
    uv run --with pyarrow --with numpy --with pydantic python scripts/build_mech_similar_artifact.py out.npz --parquet file.parquet

Output arrays: keys (name_key), names, vectors (float16 [n, 768], L2-normalised), meta (JSON).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone

BUCKET = "CovaDax/mtg-embeddings-bucket"
FILE = "mtg_embeddings.parquet"
URL = f"https://huggingface.co/buckets/{BUCKET}/resolve/{FILE}"

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def download(dest: str) -> None:
    token = os.environ.get("HF_TOKEN")
    if not token:
        sys.exit("HF_TOKEN is not set (fine-grained token for user CovaDax, read access to the bucket)")
    req = urllib.request.Request(URL, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=600) as resp, open(dest, "wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)


def convert(parquet_path: str, out_path: str) -> dict:
    import numpy as np
    import pyarrow.parquet as pq

    from analytics.mech_similar import name_key

    tbl = pq.read_table(parquet_path, columns=["name", "embedding"])
    names = tbl.column("name").to_pylist()
    emb = np.asarray(tbl.column("embedding").combine_chunks().values, dtype=np.float32).reshape(len(names), -1)
    norms = np.linalg.norm(emb, axis=1, keepdims=True)
    emb = emb / np.where(norms == 0, 1.0, norms)

    seen, keep = set(), []
    for i, n in enumerate(names):
        k = name_key(n)
        if k and k not in seen:
            seen.add(k)
            keep.append(i)
    keys = np.array([name_key(names[i]) for i in keep])
    disp = np.array([names[i] for i in keep])
    vecs = emb[keep].astype(np.float16)
    meta = {"source": f"hf://buckets/{BUCKET}/{FILE}", "upstream": "minimaxir/mtg-embeddings",
            "model_id": "Alibaba-NLP/gte-modernbert-base", "dim": int(vecs.shape[1]), "cards": len(keep),
            "rows_in": len(names), "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "wb") as fh:
        np.savez_compressed(fh, keys=keys, names=disp, vectors=vecs, meta=np.array(json.dumps(meta)))
    os.replace(tmp, out_path)
    with open(out_path, "rb") as fh:
        meta["sha256"] = hashlib.sha256(fh.read()).hexdigest()
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", help="output .npz path")
    ap.add_argument("--parquet", help="use this local parquet instead of downloading")
    a = ap.parse_args()
    if a.parquet:
        meta = convert(a.parquet, a.out)
    else:
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, FILE)
            download(p)
            meta = convert(p, a.out)
    print(json.dumps(meta, indent=2))
    print(f"wrote {a.out} ({os.path.getsize(a.out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
