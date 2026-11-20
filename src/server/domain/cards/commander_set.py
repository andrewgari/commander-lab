"""The Redis set of commander-eligible oracle ids (card-search §3.1.2).

Built by paging Scryfall ``is:commander legal:commander game:paper`` and kept
24h. A pull that fails part-way is NEVER written (same "a failure is never
cached" rule as the search cache); a short-lived marker stops a flaky upstream
from being re-pulled on every request. The Redis client and the page fetcher
are injected, so this module owns the policy and nothing else.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Tuple

from src.server.domain.cards.commander_eligibility import front_face, is_commander_eligible

SET_KEY = "cards:commander_eligible"            # oracle ids
NAMES_KEY = "cards:commander_eligible_names"    # casefolded names, for name-only callers
FAILED_KEY = "cards:commander_eligible:failed"  # negative marker, NOT a result cache
TTL_SECONDS = 24 * 60 * 60
FAILED_TTL_SECONDS = 60
ELIGIBLE_QUERY = "is:commander legal:commander game:paper"
MAX_PAGES = 60

PageFetcher = Callable[[int], Tuple[list, bool]]


def _text(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def available(r) -> bool:
    try:
        return bool(r.exists(SET_KEY))
    except Exception:
        return False


def contains_name(r, name: str) -> bool:
    try:
        folded = (name or "").casefold()
        return bool(r.sismember(NAMES_KEY, folded)
                    or r.sismember(NAMES_KEY, folded.split(" // ")[0]))
    except Exception:
        return False


def card_oracle_id(card: Mapping[str, Any]) -> str:
    oid = card.get("oracle_id")
    if not oid:
        faces = card.get("card_faces") or []
        oid = (faces[0].get("oracle_id") if faces else "") or ""
    return oid


def ensure(r, fetch_page: PageFetcher, *, build: bool = True) -> bool:
    """True when the eligible set is usable. Builds it lazily when allowed."""
    if available(r):
        return True
    if not build:
        return False
    try:
        if r.get(FAILED_KEY):
            return False
    except Exception:
        pass
    ids, names = [], []
    try:
        page = 1
        while True:
            cards, has_more = fetch_page(page)
            for card in cards:
                oid = card_oracle_id(card)
                if oid:
                    ids.append(oid)
                    full = (card.get("name") or "").casefold()
                    if full:
                        names.append(full)
                        names.append(full.split(" // ")[0])
            if not has_more:
                break
            page += 1
            if page > MAX_PAGES:
                raise RuntimeError("eligible-set pull did not terminate")
    except Exception:
        _mark_failed(r)
        return False                       # partial pull: nothing is written
    if not ids:
        _mark_failed(r)
        return False
    try:
        r.sadd(NAMES_KEY, *dict.fromkeys(names))
        r.sadd(SET_KEY, *dict.fromkeys(ids))
        r.expire(NAMES_KEY, TTL_SECONDS)
        r.expire(SET_KEY, TTL_SECONDS)
    except Exception:
        try:
            r.delete(SET_KEY)
            r.delete(NAMES_KEY)
        except Exception:
            pass
        return False
    return True


def _mark_failed(r) -> None:
    try:
        r.set(FAILED_KEY, "1", ex=FAILED_TTL_SECONDS)
    except Exception:
        pass


class Eligibility:
    """Per-request eligibility lookups: the set when present, else the predicate.

    ``used_fallback`` flips to True the first time the predicate answers, so the
    caller can mark the response ``degraded``.
    """

    def __init__(self, r, set_available: bool, *, bulk: bool = False):
        self._r = r
        self.set_available = set_available
        self.used_fallback = False
        self._ids = None
        if set_available and bulk:
            try:
                self._ids = {_text(m) for m in r.smembers(SET_KEY)}
            except Exception:
                self._ids = None

    def check(self, row: Mapping[str, Any]) -> bool:
        if self.set_available:
            oid = row.get("oracle_id") or ""
            if self._ids is not None:
                return oid in self._ids
            try:
                return bool(self._r.sismember(SET_KEY, oid))
            except Exception:
                pass
        self.used_fallback = True
        return is_commander_eligible(row)


class AlwaysEligible:
    """Used when the upstream query was already ``is:commander``."""

    used_fallback = False

    def check(self, row: Mapping[str, Any]) -> bool:
        return True


def name_eligible(r, name: str, type_line: str) -> bool:
    """Name-only check for the autocomplete fallback (card_meta has no oracle id)."""
    if available(r):
        return contains_name(r, name)
    return is_commander_eligible({"type_line": front_face(type_line),
                                  "oracle_text": "", "legalities": {"commander": "legal"}})
