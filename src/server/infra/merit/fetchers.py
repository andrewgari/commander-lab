"""Raw-payload fetchers for offline merit calibration (EDHREC, Commander Spellbook).

Payloads are written ONLY under CALIBRATION_CACHE_DIR (never the repo, D15).
Commander Salt is deliberately not supported: its POST ingests decks (D13).
Stdlib only; synchronous; resumable (existing cache files are reused).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse
from typing import Callable, Iterable, Optional

USER_AGENT = "commander-lab/1.0 (+https://github.com/andrewgari/commander-lab)"
EDHREC_BASE = "https://json.edhrec.com/pages"
SPELLBOOK_VARIANTS_URL = "https://json.commanderspellbook.com/variants.json.gz"
BRACKET_PAGES = ("exhibition", "core", "upgraded", "optimized", "cedh")
EDHREC_MIN_INTERVAL_S = 0.6
SCRYFALL_BULK_INDEX = "https://api.scryfall.com/bulk-data"
ARCHIDEKT_LIST = "https://archidekt.com/api/decks/v3/"
ARCHIDEKT_DECK = "https://archidekt.com/api/decks/{id}/"
ARCHIDEKT_MIN_INTERVAL_S = 1.0
MIN_DECK_CARDS = 95
FORBIDDEN_HOSTS = ("commandersalt.com",)

_SLUG_STRIP = re.compile(r"[^a-z0-9\s-]")
_SAFE_SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")
_SLUG_WS = re.compile(r"[\s-]+")

Opener = Callable[[urllib.request.Request], bytes]


class CalibrationCacheError(RuntimeError):
    pass


def cache_dir(env: Optional[dict] = None) -> Path:
    raw = (env if env is not None else os.environ).get("CALIBRATION_CACHE_DIR", "").strip()
    if not raw:
        raise CalibrationCacheError("CALIBRATION_CACHE_DIR is not set")
    path = Path(raw).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def slugify(name: str) -> str:
    front = (name or "").split(" // ")[0].lower()
    return _SLUG_WS.sub("-", _SLUG_STRIP.sub("", front).strip())


def _default_opener(req: urllib.request.Request) -> bytes:
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


class Fetcher:
    def __init__(self, cache: Path, *, opener: Opener = _default_opener,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 min_interval_s: float = EDHREC_MIN_INTERVAL_S, retries: int = 2):
        self.cache = Path(cache)
        self._opener, self._sleep, self._clock = opener, sleep, clock
        self.min_interval_s, self.retries = min_interval_s, retries
        self._last = None
        self.requests = 0

    def _get(self, url: str, min_interval_s: Optional[float] = None) -> bytes:
        interval = self.min_interval_s if min_interval_s is None else min_interval_s
        host = (urlparse(url).hostname or "").lower()
        if any(host == h or host.endswith("." + h) for h in FORBIDDEN_HOSTS):
            raise CalibrationCacheError(f"refusing to call {host}")
        for attempt in range(self.retries + 1):
            if self._last is not None:
                wait = interval - (self._clock() - self._last)
                if wait > 0:
                    self._sleep(wait)
            self._last = self._clock()
            self.requests += 1
            try:
                return self._opener(urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, */*"}))
            except urllib.error.HTTPError as exc:
                if exc.code == 404 or attempt >= self.retries or exc.code not in (429, 500, 502, 503, 504):
                    raise
            except urllib.error.URLError:
                if attempt >= self.retries:
                    raise
            self._sleep(2 ** attempt)
        raise AssertionError("unreachable")

    def _cached(self, rel: str, url: str, min_interval_s: Optional[float] = None) -> Optional[Path]:
        """Fetch url to cache/rel unless present. Returns None on 404."""
        dest = (self.cache / rel).resolve()
        if not dest.is_relative_to(self.cache.resolve()):
            raise CalibrationCacheError(f"path escapes cache dir: {rel}")
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        try:
            body = self._get(url, min_interval_s)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        tmp.write_bytes(body)
        tmp.replace(dest)
        return dest

    # -- EDHREC ------------------------------------------------------------
    def edhrec_year(self) -> Optional[Path]:
        return self._cached("edhrec/year.json", f"{EDHREC_BASE}/commanders/year.json")

    def edhrec_commander(self, name_or_slug: str, *, slug: bool = False) -> dict:
        """Base page + 5 bracket pages. Returns {page: Path|None}."""
        s = name_or_slug if slug else slugify(name_or_slug)
        if not _SAFE_SLUG.fullmatch(s or ""):
            raise CalibrationCacheError(f"invalid slug: {s!r}")
        out = {"base": self._cached(f"edhrec/{s}/base.json", f"{EDHREC_BASE}/commanders/{s}.json")}
        for b in BRACKET_PAGES:
            out[b] = self._cached(f"edhrec/{s}/{b}.json", f"{EDHREC_BASE}/commanders/{s}/{b}.json")
        return out

    def edhrec_many(self, names: Iterable[str]) -> dict:
        return {n: self.edhrec_commander(n) for n in names}

    # -- Commander Spellbook ----------------------------------------------
    def spellbook_variants(self) -> Optional[Path]:
        return self._cached("spellbook/variants.json.gz", SPELLBOOK_VARIANTS_URL)


    # -- Scryfall bulk -------------------------------------------------------
    def scryfall_bulk(self, kind: str = "oracle_cards") -> Optional[Path]:
        """Download the Scryfall bulk file of ``kind`` into the cache (once)."""
        if not _SAFE_SLUG.fullmatch(kind.replace("_", "-")):
            raise CalibrationCacheError(f"invalid bulk kind: {kind!r}")
        index = self._cached("scryfall/bulk-data.json", SCRYFALL_BULK_INDEX, 0.2)
        if index is None:
            return None
        entry = next((e for e in json.loads(index.read_text()).get("data", []) if e.get("type") == kind), None)
        url = (entry or {}).get("download_uri") or (entry or {}).get("jsonl_download_uri")
        if not url:
            return None
        ext = "jsonl.gz" if url.endswith(".jsonl.gz") else ("json.gz" if url.endswith(".gz") else "json")
        return self._cached(f"scryfall/{kind}.{ext}", url, 0.2)

    # -- Archidekt held-out decks (D15: cache only, never republished) --------
    def archidekt_heldout(self, bracket: int, limit: int = 150, exclude_ids: Iterable[int] = ()) -> list:
        """Cache up to ``limit`` public labelled decks for ``bracket`` (1-5).

        Lists decks with ``edhBracket=bracket`` at >=1 req/s, keeps those with
        >= MIN_DECK_CARDS cards, and caches each deck JSON under
        ``archidekt/b{bracket}/{id}.json``. Returns the cached paths.
        """
        if bracket not in (1, 2, 3, 4, 5):
            raise CalibrationCacheError(f"invalid bracket: {bracket!r}")
        skip = {int(i) for i in exclude_ids}
        out: list = []
        page = 1
        while len(out) < limit:
            url = f"{ARCHIDEKT_LIST}?formats=3&edhBracket={bracket}&pageSize=50&page={page}"
            listing = json.loads(self._get(url, ARCHIDEKT_MIN_INTERVAL_S))
            results = listing.get("results") or []
            for d in results:
                did = d.get("id")
                if not isinstance(did, int) or did in skip or d.get("edhBracket") != bracket:
                    continue
                if (d.get("size") or 0) < MIN_DECK_CARDS or d.get("private") or d.get("unlisted"):
                    continue
                p = self._cached(f"archidekt/b{bracket}/{did}.json", ARCHIDEKT_DECK.format(id=did),
                                 ARCHIDEKT_MIN_INTERVAL_S)
                if p is not None:
                    out.append(p)
                if len(out) >= limit:
                    break
            if not results or not listing.get("next"):
                break
            page += 1
        return out


def write_manifest(cache: Path, data: dict) -> Path:
    p = Path(cache) / "manifest.json"
    p.write_text(json.dumps(data, indent=2, sort_keys=True))
    return p
