"""Visual regression for the review PDF: one golden review, rendered by the pdf container's own renderer.

Run INSIDE the pdf image so fonts and Chromium are the production ones (CI does the same):

    cd src/web && npm ci && npm run build && (PORT=3000 node build &)
    docker build -t commander-lab-pdf:dev docker/pdf
    docker run --rm --network host -v "$PWD:/work" -w /work -e PYTHONPATH=/app commander-lab-pdf:dev \\
        sh -c 'pip install -q pymupdf numpy && python src/web/tests/review_visual.py --web http://127.0.0.1:3000'
    ... --update      regenerate src/web/tests/baseline/*.png after an intended design change

Checks: page count; per-page pixel diff vs the committed baseline within TOLERANCE; no swap card split across pages; no
orphaned section heading; no missing images; no request outside the web origin. Card art is a deterministic stub.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pymupdf

sys.path.insert(0, "/app")
import render  # noqa: E402  (docker/pdf/render.py, the same code the service runs)

HERE = Path(__file__).parent
BASELINE = HERE / "baseline"
GOLDEN = HERE.parents[2] / "tests/fixtures/review_document/golden/lara-uncharted.review.json"
TOLERANCE = 0.006          # mean absolute pixel difference, 0..1, per page (anti-aliasing noise is ~0.0005)
SCALE = 1.0


def stub_card() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 146, 204), False)
    pix.set_rect(pix.irect, (38, 46, 58))
    pix.set_rect(pymupdf.IRect(8, 8, 138, 196), (96, 112, 130))
    return pix.tobytes("jpeg")


def structure_problems(pdf: pymupdf.Document, doc: dict, headings: list[str]) -> list[str]:
    problems: list[str] = []
    pages = [pdf[i] for i in range(len(pdf))]
    for n, swap in enumerate(doc["swaps"], start=1):
        label = f"SWAP {n:02d}"
        where = [i for i, p in enumerate(pages) if label in p.get_text().upper()]
        if not where:
            problems.append(f"{label} not found in the PDF")
            continue
        page_text = pages[where[0]].get_text()
        for key, snippet in (("trade-off", swap["tradeoff"][:40]), ("budget effect", swap["budget_effect"][:40])):
            if snippet.replace("\n", " ") not in " ".join(page_text.split()):
                problems.append(f"{label} is split: its {key} is not on the same page as its label (page {where[0] + 1})")
    for i, page in enumerate(pages):
        body = [b for b in page.get_text("blocks") if b[3] < page.rect.height * 0.93 and b[4].strip()]
        if not body:
            continue
        last = max(body, key=lambda b: b[3])
        text = " ".join(last[4].split()).lower()
        for h in headings:
            if text == " ".join(h.split()).lower() or text.endswith(" ".join(h.split()).lower()):
                problems.append(f"page {i + 1} ends with the orphaned heading {h!r}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--web", default="http://127.0.0.1:3000")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--out", type=Path, default=Path("/tmp/review-visual"))
    args = ap.parse_args()
    doc = json.loads(GOLDEN.read_text())
    out = render.render_pdf(f"{args.web}/dev/review?variant=golden&print=1", allowed_origin=args.web,
                            stubs={"/api/card-image": ("image/jpeg", stub_card())})
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "golden.pdf").write_bytes(out.pdf)
    pdf = pymupdf.open(stream=out.pdf, filetype="pdf")
    failures: list[str] = []
    if out.missing_images:
        failures.append(f"{len(out.missing_images)} image(s) missing: {out.missing_images[:3]}")
    if out.blocked:
        failures.append(f"requests outside the web origin: {out.blocked[:3]}")
    failures += structure_problems(pdf, doc, out.headings)
    BASELINE.mkdir(exist_ok=True)
    for i in range(len(pdf)):
        pix = pdf[i].get_pixmap(matrix=pymupdf.Matrix(SCALE, SCALE), alpha=False)
        pix.save(args.out / f"page-{i + 1}.png")
        target = BASELINE / f"review-golden-p{i + 1}.png"
        if args.update:
            pix.save(target)
            continue
        if not target.exists():
            failures.append(f"no baseline for page {i + 1} (run with --update)")
            continue
        base = pymupdf.Pixmap(str(target))
        if (base.width, base.height) != (pix.width, pix.height):
            failures.append(f"page {i + 1}: size {pix.width}x{pix.height} != baseline {base.width}x{base.height}")
            continue
        a = np.frombuffer(pix.samples, np.uint8).astype(np.int16)
        b = np.frombuffer(base.samples, np.uint8).astype(np.int16)
        diff = float(np.abs(a - b).mean() / 255)
        print(f"page {i + 1}: mean diff {diff:.5f} (tolerance {TOLERANCE})")
        if diff > TOLERANCE:
            failures.append(f"page {i + 1} differs from the baseline by {diff:.4f} > {TOLERANCE}")
    if not args.update:
        stale = sorted(p.name for p in BASELINE.glob("review-golden-p*.png") if int(p.stem.split("-p")[1]) > len(pdf))
        if stale:
            failures.append(f"baseline has more pages than the PDF: {stale}")
    print(f"{len(pdf)} page(s), {out.images} image(s), {len(out.blocked)} blocked request(s)")
    if failures:
        print("FAIL\n  " + "\n  ".join(failures))
        return 1
    print("baselines updated" if args.update else "OK: PDF matches the golden baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
