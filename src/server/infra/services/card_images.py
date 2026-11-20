"""Server-side Scryfall card-image cache (deck reviewer card 11).

``cardImage(name, size)`` in the web app points at ``GET /api/v2/card-images``; this module answers it. An image is
fetched from Scryfall at most once per (card, size), written to a local directory, and served from there afterwards, so
a PDF renders with no network access once the cache is warm.

Scryfall rules (docs/reviewer/EXTERNAL_APIS.md, https://scryfall.com/docs/api and /docs/api/images):
- every request carries an accurate ``User-Agent`` and an ``Accept`` header;
- ``/cards/named`` is a 2-requests/second endpoint: requests are spaced >= ``MIN_INTERVAL`` (0.55 s) from one process-wide
  lock, so concurrent page loads queue instead of bursting;
- HTTP 429 locks us out for 30 s: we stop calling Scryfall for the ``Retry-After`` period (>= 30 s) and report the miss;
- images are cached (never hot-linked per view), served whole and unmodified (no crop, distortion or watermark), and the
  report footer carries the Wizards of the Coast / Scryfall credit.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

import httpx

logger = logging.getLogger(__name__)

SCRYFALL_BASE = "https://api.scryfall.com"
USER_AGENT = "commander-lab/1.0 (+https://github.com/andrewgari/commander-lab)"
SIZES = ("small", "normal", "large")
MIN_INTERVAL = 0.55           # s between Scryfall API calls (limit: 2/s on /cards/named)
LOCKOUT_SECONDS = 30.0        # a 429 locks the IP out for 30 s
NEGATIVE_TTL = 3600.0         # remember "no such card" for an hour instead of re-asking
MAX_NAME_LEN = 200
MAX_IMAGE_BYTES = 5_000_000
_JPEG = b"\xff\xd8\xff"


class ImageUnavailable(Exception):
    """No image can be served. ``status`` is the HTTP status the API answers with (404 unknown, 503 try later)."""

    def __init__(self, reason: str, status: int = 404):
        super().__init__(reason)
        self.reason, self.status = reason, status


def _env_dir() -> Path:
    configured = os.environ.get("CARD_IMAGE_CACHE_DIR")
    if configured:
        return Path(configured)
    data = Path("/data")
    if data.is_dir() and os.access(data, os.W_OK):
        return data / "card-images"
    return Path(tempfile.gettempdir()) / "commander-lab-card-images"


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip().lower()


class CardImageCache:
    def __init__(self, root: Path | str | None = None, *, base_url: str | None = None, min_interval: float = MIN_INTERVAL,
                 client: httpx.Client | None = None, offline: bool | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.root = Path(root) if root else _env_dir()
        self.base_url = (base_url or os.environ.get("SCRYFALL_IMAGE_BASE_URL") or SCRYFALL_BASE).rstrip("/")
        self.min_interval = min_interval
        self.offline = (os.environ.get("CARD_IMAGE_OFFLINE", "") == "1") if offline is None else offline
        self._client = client
        self._clock, self._sleep = clock, sleep
        self._lock = threading.Lock()
        self._last_call = float("-inf")
        self._locked_until = float("-inf")
        self._missing: dict[str, float] = {}
        self.network_calls = 0

    # ---- paths -------------------------------------------------------------------------------------------
    def path_for(self, name: str, size: str) -> Path:
        digest = hashlib.sha256(_norm(name).encode("utf-8")).hexdigest()
        return self.root / size / digest[:2] / f"{digest}.jpg"

    @staticmethod
    def check(name: str, size: str) -> tuple[str, str]:
        if size not in SIZES:
            raise ImageUnavailable(f"size must be one of {', '.join(SIZES)}", 422)
        name = (name or "").strip()
        if not name or len(name) > MAX_NAME_LEN:
            raise ImageUnavailable("card name is empty or too long", 422)
        return name, size

    def cached(self, name: str, size: str) -> Optional[Path]:
        path = self.path_for(name, size)
        return path if path.is_file() and path.stat().st_size > 0 else None

    # ---- lookup ------------------------------------------------------------------------------------------
    def get(self, name: str, size: str = "small") -> Path:
        """The cached image file for ``name``, fetching it from Scryfall once when it is not cached yet."""
        name, size = self.check(name, size)
        hit = self.cached(name, size)
        if hit:
            return hit
        if self.offline:
            raise ImageUnavailable("image not cached and card-image network access is disabled", 503)
        with self._lock:
            hit = self.cached(name, size)           # another request may have fetched it while we waited
            if hit:
                return hit
            return self._fetch(name, size)

    def warm(self, names: Iterable[str], size: str = "small") -> dict[str, str]:
        """Fetch every missing image; ``{name: reason}`` for the ones that could not be cached (never raises)."""
        failed: dict[str, str] = {}
        for name in dict.fromkeys(n for n in names if n):
            try:
                self.get(name, size)
            except ImageUnavailable as exc:
                failed[name] = exc.reason
        return failed

    # ---- network (callers hold self._lock) ------------------------------------------------------------------
    def _pace(self) -> None:
        wait = self._last_call + self.min_interval - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._last_call = self._clock()

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=15.0, follow_redirects=True,
                                        headers={"User-Agent": USER_AGENT, "Accept": "image/jpeg,image/*;q=0.8"})
        return self._client

    def _request(self, name: str, size: str, mode: str) -> httpx.Response:
        self._pace()
        self.network_calls += 1
        return self._http().get(f"{self.base_url}/cards/named", params={mode: name, "format": "image", "version": size},
                                headers={"User-Agent": USER_AGENT, "Accept": "image/jpeg,image/*;q=0.8"})

    def _fetch(self, name: str, size: str) -> Path:
        key = _norm(name)
        now = self._clock()
        if now < self._locked_until:
            raise ImageUnavailable(f"Scryfall rate limit: retry in {int(self._locked_until - now) + 1}s", 503)
        if self._missing.get(key, 0) > now:
            raise ImageUnavailable("Scryfall has no card with this name", 404)
        resp = None
        try:
            for mode in ("exact", "fuzzy"):          # exact first; fuzzy rescues "A // B" and punctuation variants
                resp = self._request(name, size, mode)
                if resp.status_code == 429:
                    wait = max(LOCKOUT_SECONDS, _retry_after(resp))
                    self._locked_until = self._clock() + wait
                    logger.warning("scryfall 429 for card image: pausing image fetches for %.0fs", wait)
                    raise ImageUnavailable(f"Scryfall rate limit: retry in {int(wait)}s", 503)
                if resp.status_code != 404:
                    break
        except httpx.HTTPError as exc:
            raise ImageUnavailable(f"Scryfall unreachable: {type(exc).__name__}", 503) from exc
        assert resp is not None
        if resp.status_code == 404:
            self._missing[key] = self._clock() + NEGATIVE_TTL
            raise ImageUnavailable("Scryfall has no card with this name", 404)
        if resp.status_code != 200:
            raise ImageUnavailable(f"Scryfall answered HTTP {resp.status_code}", 503)
        body = resp.content
        if not body.startswith(_JPEG) or len(body) > MAX_IMAGE_BYTES:
            raise ImageUnavailable("Scryfall did not return a JPEG image", 503)
        return self._store(name, size, body)

    def _store(self, name: str, size: str, body: bytes) -> Path:
        path = self.path_for(name, size)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(body)
        os.replace(tmp, path)                        # atomic: a reader never sees half an image
        path.with_suffix(".json").write_text(json.dumps({"name": name, "size": size, "bytes": len(body),
                                                         "fetched_at": time.time(), "source": "scryfall"}))
        return path


def _retry_after(resp: httpx.Response) -> float:
    try:
        return float(resp.headers.get("Retry-After", 0))
    except ValueError:
        return 0.0


_default: Optional[CardImageCache] = None
_default_lock = threading.Lock()


def get_cache() -> CardImageCache:
    global _default
    with _default_lock:
        if _default is None:
            _default = CardImageCache()
        return _default
