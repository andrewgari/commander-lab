"""Review pipeline orchestration (Slice 3, ARCHITECTURE.md §6).

Gathers the pipeline's inputs from the Postgres source of truth (deck,
cards, instances, profile snapshot/overrides) plus the read-only reference
providers (Commander Spellbook, EDHREC — both through the Redis cache,
which stays a cache, never truth), runs the pure domain pipeline, and
persists the stored review + its audit trail in one transaction.

Every provider/capability is injectable so tests run fully offline.
"""
from __future__ import annotations

import os
from typing import Callable, Mapping, Optional, Sequence

from sqlalchemy.orm import Session

from src.server.domain.analytics import edhrec_inclusion
from src.server.domain.analytics.interactions import combos as combos_mod
from src.server.domain.inventory import have_buy as have_buy_mod
from src.server.domain.review import candidates as candidates_mod
from src.server.domain.review import fact_pack as fact_pack_mod
from src.server.domain.review import pipeline as pipeline_mod
from src.server.domain.review.fact_pack import name_key
from src.server.infra.db import repositories as repo
from src.server.infra.llm.review_writer import default_writer

SCRYFALL_NAMED_URL = "https://api.scryfall.com/cards/named"
SCRYFALL_HEADERS = {"User-Agent": "commander-lab/1.0 (review-verifier)",
                    "Accept": "application/json"}

MAX_CARD_INFO = 60  # bound Scryfall lookups for EDHREC candidate identity checks


class DictCache:
    """Minimal get/set handle for provider caches when no Redis is wired
    (tests). Providers treat it as a cache; losing it only costs refetches."""

    def __init__(self):
        self.store: dict = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value


def _default_cache():
    """Redis cache handle (cache/queue role only), or a dict fallback."""
    try:
        from src.server.infra.cache import datastore

        return datastore.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    except Exception:  # noqa: BLE001 - cache is optional by design
        return DictCache()


def scryfall_named_lookup(name: str) -> Optional[dict]:
    """Default Scryfall /cards/named (fuzzy) lookup for the verifier.
    Returns {name, oracle_id, color_identity} or None (not found / offline)."""
    import httpx

    try:
        resp = httpx.get(SCRYFALL_NAMED_URL, params={"fuzzy": name},
                         headers=SCRYFALL_HEADERS, timeout=10.0)
    except Exception:  # noqa: BLE001 - unreachable == unresolvable
        return None
    if resp.status_code != 200:
        return None
    try:
        card = resp.json()
    except ValueError:
        return None
    if not card.get("name"):
        return None
    return {
        "name": card["name"],
        "oracle_id": card.get("oracle_id"),
        "color_identity": list(card.get("color_identity") or []),
        "source": "scryfall",
    }


def make_resolver(session: Session,
                  scryfall_lookup: Optional[Callable[[str], Optional[dict]]] = scryfall_named_lookup):
    """Verifier resolver: cards table fast path, then Scryfall.

    The DB path covers every card Commander Lab has ever imported (facts
    from the DB, AGENT_RULES.md §3); Scryfall is the fallback for names the
    DB has never seen (LLM-listed cards, fresh EDHREC candidates).
    """
    def resolve(name: str) -> Optional[dict]:
        hit = repo.get_cards_by_names(session, [name]).get(name)
        if hit:
            return {"name": hit["name"], "oracle_id": hit["oracle_id"],
                    "color_identity": list(hit.get("color_identity") or []), "source": "db"}
        if scryfall_lookup is None:
            return None
        return scryfall_lookup(name)

    return resolve


def fetch_combos(cache, commanders: Sequence[str], names: Sequence[str],
                 owned: Mapping, spellbook_client=None) -> tuple:
    """(review-shaped combos, raw spellbook payload). Never raises."""
    client = spellbook_client or combos_mod.SpellbookClient(cache)
    try:
        data = client.find_my_combos(list(commanders), sorted(names))
    except Exception as exc:  # noqa: BLE001 - Spellbook is optional; the review renders without it
        return ({"in_deck": [], "one_away": [], "error": str(exc)[:200]},
                {"included": [], "error": str(exc)[:200]})

    in_deck_keys = {name_key(n) for n in names}
    one_away = []
    for variant in data.get("almost_included") or []:
        missing = [n for n in variant.get("card_names") or [] if name_key(n) not in in_deck_keys]
        one_away.append({**variant, "missing": sorted(missing),
                         "missing_owned": bool(missing) and all(name_key(m) in owned for m in missing)})
    one_away.sort(key=lambda c: (not c["missing_owned"], -int(c.get("popularity") or 0),
                                 str(c.get("id") or "")))
    combos = {"in_deck": sorted(data.get("included") or [],
                                key=lambda c: (-int(c.get("popularity") or 0), str(c.get("id") or ""))),
              "one_away": one_away, "identity": data.get("identity")}
    return combos, data


def fetch_edhrec(cache, commanders: Sequence[str], edhrec_client=None) -> dict:
    """Cached EDHREC commander page; never raises (error dict on failure)."""
    if not commanders:
        return {"cards": {}, "error": "deck has no commander"}
    try:
        return edhrec_inclusion.commander_page(cache, list(commanders), client=edhrec_client)
    except Exception as exc:  # noqa: BLE001
        return {"cards": {}, "error": str(exc)[:200]}


def build_card_info(edhrec_page: Mapping, deck: Mapping,
                    resolver) -> dict:
    """Pre-resolve the top EDHREC candidates (bounded) so the retrieval pass
    can filter by color identity and store canonical names."""
    cards = (edhrec_page or {}).get("cards") or {}
    if not cards:
        return {}
    in_deck = {name_key(c) for c in deck.get("commanders") or []}
    in_deck |= {name_key(c["name"]) for c in deck.get("cards") or []}
    ranked = sorted((r for r in cards.values() if name_key(r.get("name") or "") not in in_deck),
                    key=lambda r: (-(r.get("inclusion") or 0.0), str(r.get("name"))))
    info = {}
    for rec in ranked[:MAX_CARD_INFO]:
        hit = resolver(rec["name"])
        if hit:
            info[name_key(rec["name"])] = {
                "name": hit["name"], "color_identity": list(hit.get("color_identity") or []),
            }
    return info


def build_review(session: Session, deck_id: str, *, cache=None,
                 llm_writer=None, scryfall_lookup=scryfall_named_lookup,
                 edhrec_client=None, spellbook_client=None) -> Optional[dict]:
    """Build and store one review for `deck_id`. Returns the stored review
    (with jobs), or None when the deck does not exist.

    `llm_writer`: callable(payload) -> raw reply text; the sentinel default
    (None) means "use the env-configured oracle, else offline". Pass
    ``llm_writer=False`` to force the offline path in tests.
    """
    deck_row = repo.get_deck(session, deck_id)
    if deck_row is None:
        return None
    cache = cache if cache is not None else _default_cache()

    deck_cards = repo.get_deck_cards(session, deck_id)
    instances = repo.list_instances(session)
    all_oids = {c["oracle_id"] for c in deck_cards} | {i.get("oracle_id") for i in instances}
    cards_by_oracle = repo.get_cards(session, {o for o in all_oids if o})

    deck = fact_pack_mod.deck_for_review(deck_row, deck_cards)
    texts = fact_pack_mod.texts_for_deck(deck, cards_by_oracle, deck_cards)

    names_by_oracle = {oid: c["name"] for oid, c in cards_by_oracle.items()}
    deck_names = {d["id"]: d["name"] for d in repo.list_decks(session, include_archived=True)}
    owned = candidates_mod.owned_index(instances, names_by_oracle, deck_names)

    deck_instances = [i for i in instances if i.get("oracle_id") in {c["oracle_id"] for c in deck_cards}]
    have_buy = have_buy_mod.compute_have_buy(deck_cards, deck_instances, deck_id)

    names = [c["name"] for c in deck.get("cards") or []]
    combos, combos_raw = fetch_combos(cache, deck["commanders"], names, owned, spellbook_client)
    edhrec_page = fetch_edhrec(cache, deck["commanders"], edhrec_client)

    resolver = make_resolver(session, scryfall_lookup)
    card_info = build_card_info(edhrec_page, deck, resolver)

    # Collection texts for the tag pass: owned cards with reference data,
    # excluding cards already in this deck.
    in_deck = {name_key(n) for n in names} | {name_key(c) for c in deck["commanders"]}
    collection_texts = {
        c["name"]: c for c in cards_by_oracle.values()
        if name_key(c.get("name")) not in in_deck
        and name_key(c.get("name")) in owned
    }

    writer = default_writer() if llm_writer is None else (llm_writer or None)

    result = pipeline_mod.run(
        deck, texts, deck_id=deck_id,
        combos=combos, combos_raw=combos_raw, edhrec_page=edhrec_page,
        have_buy=have_buy,
        snapshot=repo.latest_snapshot(session),
        overrides=repo.get_overrides(session),
        owned=owned, collection_texts=collection_texts,
        color_identity=list(deck_row.get("color_identity") or []),
        card_info=card_info,
        writer=writer, resolver=resolver,
    )

    review_id = repo.insert_review(session, {
        "deck_id": deck_id,
        "version": repo.next_review_version(session, deck_id),
        "fingerprint": result["fact_pack"]["fingerprint"],
        "status": result["status"],
        "fact_pack": result["fact_pack"],
        "analysis": result["analysis"],
        "doc": result["doc"],
    })
    for job in result["jobs"]:
        repo.insert_review_job(session, review_id, job["stage"], job["status"], job.get("error"))
    repo.insert_review_job(session, review_id, "store", "ok")

    review = repo.get_review(session, review_id)
    if review is None:  # pragma: no cover - defensive; insert flushed the row
        raise RuntimeError(f"review {review_id} missing after insert")
    review["jobs"] = repo.list_review_jobs(session, review_id)
    return review
