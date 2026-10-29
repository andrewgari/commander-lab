"""The optional ``pdf`` container: ``POST /render {"url": ...}`` -> ``application/pdf`` (DECISIONS D4).

Only the API calls this, over the compose-internal network. It renders one page at a time (a single Chromium per
request, serialised by a lock) and only for URLs on ``PDF_ALLOWED_ORIGIN`` (the web container), so it cannot be used to
fetch arbitrary URLs.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import render

ALLOWED = os.environ.get("PDF_ALLOWED_ORIGIN", "http://web:3000").rstrip("/")
PORT = int(os.environ.get("PORT", "8100"))
_lock = threading.Lock()
log = logging.getLogger("pdf")


class Handler(BaseHTTPRequestHandler):
    server_version = "review-pdf/1"

    def _send(self, status: int, body: bytes, ctype: str = "application/json", headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send(status, json.dumps({"error": message}).encode())

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200, b'{"ok": true}')
        else:
            self._error(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/render":
            return self._error(404, "not found")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            url = json.loads(self.rfile.read(min(length, 4096)) or b"{}").get("url", "")
        except (ValueError, AttributeError):
            return self._error(400, "body must be JSON {\"url\": ...}")
        if not isinstance(url, str) or render.origin_of(url) != ALLOWED:
            return self._error(400, f"url must be on {ALLOWED}")
        try:
            with _lock:
                out = render.render_pdf(url, allowed_origin=ALLOWED)
        except render.RenderError as exc:
            log.warning("render failed: %s", exc)
            return self._error(exc.status, str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("render crashed")
            return self._error(500, f"{type(exc).__name__}: {exc}")
        if out.blocked:
            log.warning("blocked %d non-local request(s): %s", len(out.blocked), out.blocked[:3])
        self._send(200, out.pdf, "application/pdf", {"X-Pdf-Pages": str(out.pages), "X-Missing-Images": str(len(out.missing_images)),
                                                      "X-Blocked-Requests": str(len(out.blocked))})

    def log_message(self, fmt: str, *args) -> None:
        log.info("%s %s", self.address_string(), fmt % args)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    log.info("pdf service on :%d, allowed origin %s", PORT, ALLOWED)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
