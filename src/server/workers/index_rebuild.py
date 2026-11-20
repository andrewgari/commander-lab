"""Derived-index rebuild worker (ARCHITECTURE.md §4.3/§4.4, §7.1).

This is the ONLY writer of the vector and graph stores — they are
rebuildable indexes (caches with a build step), never truth. Everything they
hold is recomputed from Postgres on every run, so they can be dropped and
rebuilt at any time.

Usage:
    python -m src.server.workers.index_rebuild vector

`vector` truncates and re-embeds straight from `cards`/`decks`:
  - card_embeddings: bge-small int8 (384-d, CPU) over oracle text + learned
    tags (Lab Tags with source 'learned', read from Redis when reachable —
    the tags table lands in Postgres in a later slice).
  - deck_embeddings: normalized centroid of member-card embeddings (same
    math as the card2vec centroid; boards commander/main/sideboard/maybe,
    basic lands excluded, each card counted once).
Then it updates derived_index_status(store='vector', rebuilt_at,
source_watermark = max(cards.refreshed_at, decks.updated_at), row_count) —
all in ONE transaction, so a rebuild is atomic.

`graph` (Apache AGE) is a PR2 follow-up; this module is the entry point it
will plug into.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Callable, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.server.infra.vector import embed as vembed
from src.server.infra.vector import store as vstore

log = logging.getLogger("commander_lab.index_rebuild")

TagProvider = Callable[[str], List[str]]


def redis_tag_provider(redis_url: Optional[str] = None) -> Optional[TagProvider]:
    """Learned Lab Tags per card name, read once from Redis (``lab_tags`` +
    the ``lab_tags_source`` provenance side-channel; only source='learned'
    tags are embedded, per §4.3). Returns None when Redis is unreachable —
    the rebuild then proceeds without tags and says so in its summary."""
    try:
        import redis

        from src.server.domain.tags import lab_tags

        r = redis.Redis.from_url(redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                                 socket_connect_timeout=2, socket_timeout=5)
        raw = r.get("lab_tags")
        import json

        flat = json.loads(raw) if raw else {}
        sources = lab_tags.get_source_map(r)
    except Exception as exc:  # noqa: BLE001 - tags are an enhancement, never a blocker
        log.warning("learned tags unavailable (%s); embedding oracle text only", exc)
        return None

    def provider(card_name: str) -> List[str]:
        tags = flat.get(card_name) or []
        card_sources = sources.get(card_name, {})
        return [t for t in tags if card_sources.get(t, lab_tags.LEARNED) == lab_tags.LEARNED]

    return provider


def _load_cards(conn) -> List[dict]:
    rows = conn.execute(
        text("SELECT oracle_id, name, type_line, oracle_text, keywords"
             " FROM cards ORDER BY oracle_id")
    ).all()
    return [{"oracle_id": r[0], "name": r[1], "type_line": r[2],
             "oracle_text": r[3], "keywords": list(r[4] or [])} for r in rows]


def _load_deck_members(conn) -> Dict[str, List[str]]:
    """{deck_id: [oracle_id, ...]} for centroid boards, basic lands excluded,
    each card once (card2vec centroid math)."""
    from sqlalchemy import bindparam

    stmt = text(
        "SELECT dc.deck_id, dc.oracle_id, c.name"
        " FROM deck_cards dc JOIN cards c ON c.oracle_id = dc.oracle_id"
        " WHERE dc.board IN :boards"
        " ORDER BY dc.deck_id, dc.oracle_id"
    ).bindparams(bindparam("boards", expanding=True))
    rows = conn.execute(stmt, {"boards": list(vembed.CENTROID_BOARDS)}).all()
    members: Dict[str, List[str]] = {}
    for deck_id, oracle_id, name in rows:
        if name in vembed.BASICS:
            continue
        seen = members.setdefault(deck_id, [])
        if oracle_id not in seen:
            seen.append(oracle_id)
    return members


def rebuild_vector(engine: Engine, embedder, tag_provider: Optional[TagProvider] = None) -> dict:
    """One atomic truncate + re-embed of card_embeddings/deck_embeddings from
    cards/decks, plus the derived_index_status upsert. ``embedder`` is any
    analytics.embeddings.Embedder (injected for tests)."""
    with engine.begin() as conn:
        watermark = vstore.source_watermark(conn)
        cards = _load_cards(conn)
        texts = [
            vembed.card_embedding_text(c, tag_provider(c["name"]) if tag_provider else ())
            for c in cards
        ]
        log.info("embedding %d cards with %s ...", len(texts), embedder.model_id)
        vectors = embedder.embed(texts, kind="passage") if texts else []
        card_rows = [(c["oracle_id"], v) for c, v in zip(cards, vectors)]

        by_oracle = {c["oracle_id"]: v for c, v in zip(cards, vectors)}
        members = _load_deck_members(conn)
        deck_rows = []
        for deck_id, oracle_ids in sorted(members.items()):
            cent = vembed.centroid([by_oracle[o] for o in oracle_ids if o in by_oracle])
            if cent is not None:
                deck_rows.append((deck_id, cent))

        row_count = vstore.replace_all(
            conn, model_id=embedder.model_id,
            card_rows=card_rows, deck_rows=deck_rows,
            source_watermark=watermark,
        )
    summary = {
        "store": vstore.STORE_VECTOR,
        "model_id": embedder.model_id,
        "cards": len(card_rows),
        "decks": len(deck_rows),
        "row_count": row_count,
        "source_watermark": watermark.isoformat() if watermark else None,
        "tags": "redis" if tag_provider else "unavailable",
    }
    log.info("vector rebuild complete: %s", summary)
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="index_rebuild",
                                     description="Rebuild derived indexes (§7.1: the only writer).")
    parser.add_argument("store", choices=["vector"], help="which derived store to rebuild")
    parser.add_argument("--database-url", default=None,
                        help="override DATABASE_URL")
    parser.add_argument("--no-tags", action="store_true",
                        help="embed oracle text only (skip the Redis learned-tags lookup)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from src.server.infra.db.config import get_settings
    from src.server.infra.db.engine import make_engine

    engine = make_engine(get_settings(args.database_url))
    try:
        if args.store == "vector":
            from src.server.domain.analytics.embeddings import OnnxEmbedder

            embedder = OnnxEmbedder()
            if not embedder.available():
                print("error: bge-small int8 model not available "
                      f"(EMBEDDINGS_MODEL_DIR={embedder.model_dir}); cannot rebuild the "
                      "vector index without the real model — refusing to fake it",
                      file=sys.stderr)
                return 2
            tag_provider = None if args.no_tags else redis_tag_provider()
            summary = rebuild_vector(engine, embedder, tag_provider)
            print(summary)
            return 0
        parser.error(f"unknown store {args.store}")
        return 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
