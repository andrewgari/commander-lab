# Review rendering: routes, PDF, image cache (card 11)

Flow: `POST /api/v2/decks/{id}/review-documents` (202, queued) -> worker thread runs Passes A-E (`stage` updates) -> row `ok` with the
`review_document.v1` JSON -> page `/decks/{id}/review` renders it with the card-06 components -> `GET /api/v2/review-documents/{id}/pdf`.

| Piece | Where | Notes |
|---|---|---|
| Job + history store | `review_documents` (migration `a011`), `infra/services/review_document_service.py` | one live run per deck; an `ok` row is immutable history; a row left `running` by a restart is reported `failed (interrupted)` |
| Production runner | `LiveReviewRunner` + `domain/analytics/review_pipeline/bridge.py` | needs a seeded `andrew` player profile and model providers (`REVIEW_MODEL_*`, provider keys / secrets); verdict = eval-bridge derived verdict |
| Image cache | `infra/services/card_images.py`, `GET /api/v2/card-images`, web `/api/card-image` | `CARD_IMAGE_CACHE_DIR` (default `/data/card-images`), `CARD_IMAGE_OFFLINE=1` forbids Scryfall calls; >= 0.55 s between Scryfall calls, 429 = 30 s pause, UA + Accept sent |
| PDF | `docker/pdf` (Playwright 1.64.1 + Chromium), `infra/services/review_pdf.py` | API warms the cache, mints a 300 s HMAC print token, asks the pdf container to print `<web>/decks/{id}/review?doc=N&print=1&token=...`; Chromium may only reach `PDF_ALLOWED_ORIGIN` |
| Feedback | `POST /api/v2/review-documents/{id}/feedback` | writes card 04's `recommendation_feedback` (`review_id` = document id); cut/add come from the stored document |

Env: `PDF_SERVICE_URL`, `PDF_WEB_URL`, `REVIEW_PDF_SECRET`, `CARD_IMAGE_CACHE_DIR`, `CARD_IMAGE_OFFLINE`, `REVIEW_JOB_WORKERS` (default 2).

Image sizes: the API image is unchanged (no Chromium). The optional `pdf` image is ~1.36 GB uncompressed (python:3.11-slim + Chromium + fonts); it is built only with `--profile pdf`.
