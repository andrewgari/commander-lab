"""PDF export of a review (deck reviewer card 11; DECISIONS.md D4).

The API never runs a browser. It asks the separate ``pdf`` container (Playwright/Chromium, ``docker/pdf``) to print
the review page the user sees: ``<web>/decks/<id>/review?doc=<n>&print=1&token=<signed>``. One renderer, one set of
charts, so the PDF equals the print preview of the page.

The token is an HMAC over ``<document id>.<expiry>`` with ``REVIEW_PDF_SECRET`` (a random per-process secret when
unset, which is fine because the API both mints and verifies it). It lets the print page load the document without a
login or cookies and is only good for a few minutes. Card images come from the local cache (``card_images``), warmed
before the render, so the PDF container needs no internet access at all.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import quote

import httpx

TOKEN_TTL_SECONDS = 300
_process_secret = secrets.token_hex(32)


class PdfUnavailable(RuntimeError):
    """The PDF service is not configured, not reachable, or failed. ``status`` is the HTTP status to answer with."""

    def __init__(self, reason: str, status: int = 503):
        super().__init__(reason)
        self.reason, self.status = reason, status


def _secret() -> bytes:
    return (os.environ.get("REVIEW_PDF_SECRET") or _process_secret).encode()


def _sign(document_id: int, expires: int) -> str:
    return hmac.new(_secret(), f"{document_id}.{expires}".encode(), hashlib.sha256).hexdigest()


def mint_token(document_id: int, *, ttl: int = TOKEN_TTL_SECONDS, now: Optional[float] = None) -> str:
    expires = int((time.time() if now is None else now) + ttl)
    return f"{expires}.{_sign(document_id, expires)}"


def verify_token(document_id: int, token: str, *, now: Optional[float] = None) -> bool:
    try:
        expires_s, sig = (token or "").split(".", 1)
        expires = int(expires_s)
    except ValueError:
        return False
    if expires < (time.time() if now is None else now):
        return False
    return hmac.compare_digest(sig, _sign(document_id, expires))


def print_url(deck_id: str, document_id: int, *, web_url: Optional[str] = None) -> str:
    base = (web_url or os.environ.get("PDF_WEB_URL") or "http://web:3000").rstrip("/")
    return f"{base}/decks/{quote(deck_id, safe='')}/review?doc={document_id}&print=1&token={mint_token(document_id)}"


def card_names(doc: Mapping[str, Any]) -> list[str]:
    """Every card name the review page renders an image for (commanders, swap cut/add/alternative)."""
    names: list[str] = [c["name"] for c in (doc.get("meta") or {}).get("commanders") or []]
    for swap in doc.get("swaps") or []:
        for key in ("cut", "add", "alternative"):
            if isinstance(swap.get(key), Mapping) and swap[key].get("name"):
                names.append(swap[key]["name"])
    return list(dict.fromkeys(names))


@dataclass
class RenderedPdf:
    data: bytes
    pages: int
    missing_images: int


def render(url: str, *, service_url: Optional[str] = None, client: Optional[httpx.Client] = None,
           timeout: float = 90.0) -> RenderedPdf:
    base = (service_url or os.environ.get("PDF_SERVICE_URL") or "").rstrip("/")
    if not base:
        raise PdfUnavailable("PDF export is not configured: set PDF_SERVICE_URL and start the pdf container "
                             "(docker compose --profile pdf up -d pdf)")
    try:
        c = client or httpx.Client(timeout=timeout)
        resp = c.post(f"{base}/render", json={"url": url})
    except httpx.HTTPError as exc:
        raise PdfUnavailable(f"the PDF service at {base} is unreachable ({type(exc).__name__}); start the pdf container") from exc
    if resp.status_code != 200 or not resp.content.startswith(b"%PDF"):
        detail = resp.text[:300] if resp.status_code != 200 else "the PDF service returned a non-PDF body"
        raise PdfUnavailable(f"the PDF service failed (HTTP {resp.status_code}): {detail}", 502)
    return RenderedPdf(resp.content, int(resp.headers.get("X-Pdf-Pages") or 0), int(resp.headers.get("X-Missing-Images") or 0))
