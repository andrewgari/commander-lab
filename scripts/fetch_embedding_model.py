"""Build-time download of the local embedding model (AN-3 pillar fit).

Used by the Dockerfile: `python scripts/fetch_embedding_model.py`. Pinned to a
Hugging Face revision and checked against sha256, so a rebuild is
reproducible and a tampered/changed file fails the build. Also usable locally:
    EMBEDDINGS_MODEL_DIR=./.models/bge python scripts/fetch_embedding_model.py
"""
import hashlib
import os
import sys
import urllib.request

REPO = os.getenv("EMBEDDINGS_REPO", "Xenova/bge-small-en-v1.5")
REV = os.getenv("EMBEDDINGS_REV", "ea104dacec62c0de699686887e3f920caeb4f3e3")
DEST = os.getenv("EMBEDDINGS_MODEL_DIR", "/opt/models/bge-small-en-v1.5")
FILES = {
    "onnx/model_quantized.onnx": "6c9c6101a956d62dfb5e7190c538226c0c5bb9cb27b651234b6df063ee7dbfe4",
    "tokenizer.json": "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
    "config.json": "fa73f90bf92c8cace1fbcb709626306f2bdbc9ea3e5b5f94b440df9b6aa56350",
}


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    os.makedirs(DEST, exist_ok=True)
    for path, want in FILES.items():
        out = os.path.join(DEST, os.path.basename(path))
        if os.path.exists(out) and sha256(out) == want:
            print("cached", out)
            continue
        url = f"https://huggingface.co/{REPO}/resolve/{REV}/{path}"
        urllib.request.urlretrieve(url, out)
        got = sha256(out)
        if got != want:
            os.remove(out)
            print(f"sha256 mismatch for {path}: {got} != {want}", file=sys.stderr)
            return 1
        print("fetched", out, os.path.getsize(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
