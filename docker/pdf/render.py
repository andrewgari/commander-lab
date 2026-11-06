"""Print a review page to PDF with Chromium (deck reviewer card 11, DECISIONS D4).

``render_pdf(url)`` opens the SvelteKit review page in print mode and prints it with the page's own ``@page`` CSS, so the
PDF equals the on-screen print preview. The browser may only talk to the one allowed origin (the web container): every
other request, including any attempt to reach the internet, is aborted and counted. Card art arrives through the web app's
``/api/card-image`` route, which serves the API's local image cache, so rendering works with the network unplugged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Optional
from urllib.parse import urlsplit

from playwright.sync_api import Route, sync_playwright

PAGE_TIMEOUT_MS = 60_000


class RenderError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


@dataclass
class Rendered:
    pdf: bytes
    pages: int
    images: int
    missing_images: list[str]
    blocked: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def count_pages(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?![A-Za-z])", pdf))


def render_pdf(url: str, *, allowed_origin: Optional[str] = None, stubs: Optional[Mapping[str, tuple[str, bytes]]] = None,
               viewport: tuple[int, int] = (1280, 1000)) -> Rendered:
    """``stubs``: ``{url substring: (content type, body)}`` served instead of the network (tests only; the service never sets it)."""
    allowed = allowed_origin or origin_of(url)
    if origin_of(url) != allowed:
        raise RenderError(f"refusing to render {origin_of(url)}: only {allowed} is allowed", 400)
    blocked: list[str] = []

    def guard(route: Route) -> None:
        req_url = route.request.url
        for needle, (ctype, body) in (stubs or {}).items():
            if needle in req_url:
                route.fulfill(status=200, content_type=ctype, body=body)
                return
        if origin_of(req_url) == allowed or req_url.startswith(("data:", "blob:")):
            route.continue_()
        else:
            blocked.append(req_url)
            route.abort()

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, locale="en-US",
                                          timezone_id="UTC", color_scheme="light")
            context.route("**/*", guard)
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            response = page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT_MS)
            if response is None or response.status != 200:
                raise RenderError(f"the review page answered HTTP {response.status if response else 'none'}")
            if page.locator("article.review-report").count() != 1:
                raise RenderError("the print page did not render a review (expired token or unfinished review?)", 422)
            page.evaluate("document.fonts.ready.then(() => true)")
            page.emulate_media(media="print")
            imgs = page.evaluate("""async () => {
                const imgs = [...document.querySelectorAll('.review-report img')];
                await Promise.all(imgs.map(i => i.complete ? null : new Promise(r => { i.onload = i.onerror = r; })));
                return imgs.map(i => ({src: i.currentSrc || i.src, ok: i.complete && i.naturalWidth > 0}));
            }""")
            headings = page.evaluate("[...document.querySelectorAll('.review-report h2')].map(h => h.textContent.trim())")
            if errors:
                raise RenderError("the review page threw: " + "; ".join(errors)[:300], 422)
            pdf = page.pdf(prefer_css_page_size=True, print_background=True)
        finally:
            browser.close()
    return Rendered(pdf=pdf, pages=count_pages(pdf), images=len(imgs), blocked=blocked, headings=headings,
                    missing_images=[i["src"] for i in imgs if not i["ok"]])
