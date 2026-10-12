"""
Produce/consume tagger: Scryfall Tagger otags + oracle-text rules first, then
an LLM pass for cards those left untagged. Results are cached per card in
Redis with no expiry (`card_interactions:{name}`); a card only costs a
Scryfall/LLM call the first time it is seen, or when VOCAB_VERSION changes.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from . import oracle_rules
from .llm import LLMChain
from .vocab import RESOURCE_KEYS, VOCAB_VERSION, normalize_resources, otag_queries

TAG_KEY_PREFIX = "card_interactions:"
OTAG_INDEX_KEY = "interactions:otag_index"
OTAG_INDEX_TTL = 7 * 24 * 3600  # the index itself is refreshed weekly; per-card tags never expire

SCRYFALL_API = "https://api.scryfall.com"
SCRYFALL_HEADERS = {"User-Agent": "commander-lab/1.0 (interaction-graph)", "Accept": "application/json"}


def tag_key(name: str) -> str:
    return f"{TAG_KEY_PREFIX}{name}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _merge(*lists: Iterable[str]) -> List[str]:
    s = set()
    for lst in lists:
        s.update(lst or [])
    return [k for k in RESOURCE_KEYS if k in s]


def _card_from_scryfall(c: dict) -> dict:
    faces = c.get("card_faces") or []
    text = c.get("oracle_text")
    if text is None and faces:
        text = "\n//\n".join(f.get("oracle_text", "") for f in faces)
    mana_cost = c.get("mana_cost")
    if mana_cost is None and faces:
        mana_cost = " // ".join(f.get("mana_cost", "") for f in faces)
    type_line = c.get("type_line") or " // ".join(f.get("type_line", "") for f in faces)
    return {
        "name": c.get("name", ""),
        "oracle_id": c.get("oracle_id") or (faces[0].get("oracle_id") if faces else None),
        "type_line": type_line or "",
        "oracle_text": text or "",
        "mana_cost": mana_cost or "",
    }


class ScryfallSource:
    """Scryfall access for the tagger. Counts HTTP requests in `requests`."""

    def __init__(self, r=None, session=None, delay: float = 0.15, search_delay: float = 0.6,
                 timeout: float = 30.0):
        import requests as _requests

        self.r = r
        self.http = session or _requests.Session()
        self.delay = delay
        self.search_delay = search_delay
        self.timeout = timeout
        self.requests = 0
        self._index: Optional[Dict[str, dict]] = None

    def _request(self, method: str, url: str, **kw):
        resp = None
        for attempt in range(8):
            self.requests += 1
            resp = self.http.request(method, url, headers=SCRYFALL_HEADERS, timeout=self.timeout, **kw)
            # /cards/search is rate limited harder than the rest of the API.
            time.sleep(self.search_delay if "/cards/search" in url else self.delay)
            if resp.status_code == 429 or resp.status_code >= 500:
                try:
                    wait = float(resp.headers.get("Retry-After") or 0)
                except (TypeError, ValueError):
                    wait = 0
                time.sleep(max(wait, min(60.0, 2.0 * 2 ** attempt)))
                continue
            return resp
        assert resp is not None
        resp.raise_for_status()
        return resp

    def fetch_cards(self, names: List[str]) -> Tuple[Dict[str, dict], List[str]]:
        found: Dict[str, dict] = {}
        for i in range(0, len(names), 75):
            chunk = names[i : i + 75]
            resp = self._request("POST", f"{SCRYFALL_API}/cards/collection",
                                 json={"identifiers": [{"name": n} for n in chunk]})
            resp.raise_for_status()
            data = resp.json().get("data", [])
            by_lower = {}
            for c in data:
                card = _card_from_scryfall(c)
                by_lower[card["name"].lower()] = card
                for face in card["name"].split(" // "):
                    by_lower.setdefault(face.lower(), card)
            for n in chunk:
                card = by_lower.get(n.lower()) or by_lower.get(n.split(" // ")[0].lower())
                if card:
                    found[n] = card
        missing = [n for n in names if n not in found]
        return found, missing

    def otag_index(self) -> Dict[str, dict]:
        """{card name: {"produces": [...], "consumes": [...]}} from Scryfall Tagger
        oracle tags in vocab.py. Cached in Redis for a week."""
        if self._index is not None:
            return self._index
        if self.r is not None:
            raw = self.r.get(OTAG_INDEX_KEY)
            if raw:
                self._index = json.loads(raw)
                return self._index
        index: Dict[str, dict] = {}
        for res_key, side, otags in otag_queries():
            q = "(" + " OR ".join(f"otag:{t}" for t in otags) + ")"
            url = f"{SCRYFALL_API}/cards/search"
            params = {"q": q, "unique": "cards"}
            while url:
                resp = self._request("GET", url, params=params)
                if resp.status_code == 404:  # no cards for this query
                    break
                resp.raise_for_status()
                body = resp.json()
                for c in body.get("data", []):
                    entry = index.setdefault(c["name"], {"produces": [], "consumes": []})
                    if res_key not in entry[side]:
                        entry[side].append(res_key)
                url = body.get("next_page") if body.get("has_more") else None
                params = None
        if self.r is not None:
            self.r.set(OTAG_INDEX_KEY, json.dumps(index), ex=OTAG_INDEX_TTL)
        self._index = index
        return index


class InteractionTagger:
    def __init__(self, r, scryfall=None, llm: Optional[LLMChain] = None, batch_size: int = 25):
        self.r = r
        self.scryfall = scryfall if scryfall is not None else ScryfallSource(r)
        self.llm = llm if llm is not None else LLMChain([])
        self.batch_size = batch_size

    # -- cache ---------------------------------------------------------------
    def load_cached(self, names: List[str]) -> Dict[str, dict]:
        if not names:
            return {}
        keys = [tag_key(n) for n in names]
        mget = self.r.mget if hasattr(self.r, "mget") else (lambda ks: [self.r.get(k) for k in ks])
        out = {}
        for n, raw in zip(names, mget(keys)):
            if raw:
                try:
                    out[n] = json.loads(raw)
                except json.JSONDecodeError:
                    pass
        return out

    def _save(self, rec: dict) -> None:
        self.r.set(tag_key(rec["name"]), json.dumps(rec))  # no TTL: cached forever

    @staticmethod
    def _fresh(rec: Optional[dict]) -> bool:
        return bool(rec) and rec.get("vocab_version") == VOCAB_VERSION and not rec.get("llm_pending")

    def _card_meta_fallback(self, name: str) -> Optional[dict]:
        raw = self.r.get(f"card_meta:{name}")
        if not raw:
            oid = self.r.get(f"idx:card_name_to_oracle:{name}")
            raw = self.r.get(f"card_meta:{oid}") if oid else None
        if not raw:
            return None
        try:
            m = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return {
            "name": name,
            "oracle_id": None,
            "type_line": " ".join((m.get("super_types") or []) + [m.get("type") or ""]).strip(),
            "oracle_text": m.get("oracle_text") or "",
            "mana_cost": "",
        }

    # -- main entry ----------------------------------------------------------
    def tag_cards(self, names: Iterable[str], run) -> Dict[str, dict]:
        names = sorted({n for n in names if n})
        cached = self.load_cached(names)
        result: Dict[str, dict] = {}
        need_det: List[str] = []
        need_llm: List[dict] = []

        for n in names:
            rec = cached.get(n)
            if self._fresh(rec):
                result[n] = rec
                run.cache_hits += 1
            elif rec and rec.get("vocab_version") == VOCAB_VERSION and rec.get("llm_pending"):
                need_llm.append(rec)
            else:
                need_det.append(n)

        if need_det:
            req_before = getattr(self.scryfall, "requests", 0)
            found, missing = self.scryfall.fetch_cards(need_det)
            for n in missing:
                fb = self._card_meta_fallback(n)
                if fb:
                    found[n] = fb
                else:
                    run.not_found.append(n)
            index = self.scryfall.otag_index() if found else {}
            run.scryfall_requests += getattr(self.scryfall, "requests", 0) - req_before

            for n in need_det:
                card = found.get(n)
                if not card:
                    continue
                o_p, o_c = oracle_rules.tag_from_oracle({**card, "name": n})
                ot = index.get(card.get("name") or n) or index.get(n) or {}
                produces = _merge(o_p, ot.get("produces"))
                consumes = _merge(o_c, ot.get("consumes"))
                trivial = oracle_rules.is_trivial({**card, "name": n})
                rec = {
                    "name": n,
                    "oracle_id": card.get("oracle_id"),
                    "produces": produces,
                    "consumes": consumes,
                    "evidence": {
                        "otag": {"produces": ot.get("produces", []), "consumes": ot.get("consumes", [])},
                        "oracle": {"produces": o_p, "consumes": o_c},
                    },
                    "card": {k: card.get(k, "") for k in ("type_line", "oracle_text", "mana_cost")},
                    "vocab_version": VOCAB_VERSION,
                    "tagged_at": _now(),
                }
                if produces or consumes:
                    rec["source"] = "scryfall"
                    self._save(rec)
                    result[n] = rec
                    run.count_new(rec["source"])
                elif trivial:
                    rec["source"] = "none"
                    self._save(rec)
                    result[n] = rec
                    run.count_new("none")
                else:
                    rec["source"] = "pending"
                    need_llm.append(rec)

        for i in range(0, len(need_llm), self.batch_size):
            batch = need_llm[i : i + self.batch_size]
            cards = [{"name": rec["name"], **(rec.get("card") or {})} for rec in batch]
            answers, provider = self.llm.tag_batch(cards, on_call=run.add_llm_call) if self.llm.available() else (None, None)
            for rec in batch:
                if answers is None:
                    rec["llm_pending"] = True
                    rec["source"] = "pending"
                    self._save(rec)
                    result[rec["name"]] = rec
                    run.llm_pending += 1
                    continue
                ans = answers.get(rec["name"]) or {}
                ans = {"produces": normalize_resources(ans.get("produces") or []),
                       "consumes": normalize_resources(ans.get("consumes") or [])}
                rec["produces"] = _merge(rec.get("produces"), ans.get("produces"))
                rec["consumes"] = _merge(rec.get("consumes"), ans.get("consumes"))
                rec.setdefault("evidence", {})["llm"] = {"provider": provider, **ans}
                rec["source"] = f"llm:{provider}"
                rec.pop("llm_pending", None)
                rec["tagged_at"] = _now()
                self._save(rec)
                result[rec["name"]] = rec
                run.count_new(rec["source"])

        return result
