"""pgvector read/write, raw SQL over a caller-supplied Connection/Session.

Writes (``replace_all``) exist for exactly one caller:
``src/server/workers/index_rebuild.py`` (AGENT_RULES.md §1 / ARCHITECTURE.md
§7.1 — the vector store is a rebuildable index, written by nobody else).
Everything else in the app uses the read functions only.

The embedding tables are deliberately absent from orm_models.py (the
``vector`` column type is PostgreSQL-only); ``derived_index_status`` does
have an ORM row but is written here too, in the same transaction as the
truncate + re-embed, so a rebuild is atomic.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from sqlalchemy import text

from src.server.infra.vector.embed import to_vector_literal

STORE_VECTOR = "vector"

# Every function takes a SQLAlchemy Connection or Session — both satisfy
# .execute(text(), params); callers own the transaction.


# ---------------------------------------------------------------------------
# Write path — index_rebuild.py only
# ---------------------------------------------------------------------------

def replace_all(conn, *, model_id: str,
                card_rows: Sequence[Tuple[str, Sequence[float]]],
                deck_rows: Sequence[Tuple[str, Sequence[float]]],
                source_watermark) -> int:
    """Truncate both embedding tables and bulk-insert the freshly computed
    rows, then upsert derived_index_status(store='vector'). Caller owns the
    transaction (worker uses engine.begin()). Returns the total row count."""
    conn.execute(text("DELETE FROM card_embeddings"))
    conn.execute(text("DELETE FROM deck_embeddings"))
    if card_rows:
        conn.execute(
            text("INSERT INTO card_embeddings (oracle_id, model_id, embedding)"
                 " VALUES (:oid, :mid, CAST(:emb AS vector))"),
            [{"oid": oid, "mid": model_id, "emb": to_vector_literal(v)}
             for oid, v in card_rows],
        )
    if deck_rows:
        conn.execute(
            text("INSERT INTO deck_embeddings (deck_id, model_id, embedding)"
                 " VALUES (:did, :mid, CAST(:emb AS vector))"),
            [{"did": did, "mid": model_id, "emb": to_vector_literal(v)}
             for did, v in deck_rows],
        )
    row_count = len(card_rows) + len(deck_rows)
    conn.execute(
        text("INSERT INTO derived_index_status"
             "     (store, rebuilt_at, source_watermark, row_count)"
             "     VALUES (:store, now(), :wm, :rc)"
             " ON CONFLICT (store) DO UPDATE SET"
             "     rebuilt_at = EXCLUDED.rebuilt_at,"
             "     source_watermark = EXCLUDED.source_watermark,"
             "     row_count = EXCLUDED.row_count"),
        {"store": STORE_VECTOR, "wm": source_watermark, "rc": row_count},
    )
    return row_count


# ---------------------------------------------------------------------------
# Read path — any caller
# ---------------------------------------------------------------------------

def similar_cards(conn, oracle_id: str, k: int = 10) -> Optional[dict]:
    """Top-k similar cards by oracle text + tag embedding. None = the card
    is not indexed (stale/absent index — the caller renders 'unavailable')."""
    row = conn.execute(
        text("SELECT model_id FROM card_embeddings WHERE oracle_id = :o"),
        {"o": oracle_id},
    ).first()
    if row is None:
        return None
    rows = conn.execute(
        text("SELECT e.oracle_id, c.name,"
             "     1 - (e.embedding <=> q.embedding) AS score"
             " FROM card_embeddings e"
             " JOIN cards c ON c.oracle_id = e.oracle_id"
             " , (SELECT embedding FROM card_embeddings WHERE oracle_id = :o) q"
             " WHERE e.oracle_id <> :o"
             " ORDER BY e.embedding <=> q.embedding"
             " LIMIT :n"),
        {"o": oracle_id, "n": k},
    ).all()
    return {
        "model_id": row[0],
        "results": [{"oracle_id": r[0], "name": r[1],
                     "score": round(float(r[2]), 4)} for r in rows],
    }


def similar_decks(conn, deck_id: str, k: int = 10) -> Optional[dict]:
    """Top-k similar decks by member-card centroid embedding. None = the
    deck is not indexed."""
    row = conn.execute(
        text("SELECT model_id FROM deck_embeddings WHERE deck_id = :d"),
        {"d": deck_id},
    ).first()
    if row is None:
        return None
    rows = conn.execute(
        text("SELECT e.deck_id, d.name,"
             "     1 - (e.embedding <=> q.embedding) AS score"
             " FROM deck_embeddings e"
             " JOIN decks d ON d.id = e.deck_id"
             " , (SELECT embedding FROM deck_embeddings WHERE deck_id = :d) q"
             " WHERE e.deck_id <> :d"
             " ORDER BY e.embedding <=> q.embedding"
             " LIMIT :n"),
        {"d": deck_id, "n": k},
    ).all()
    return {
        "model_id": row[0],
        "results": [{"deck_id": r[0], "name": r[1],
                     "score": round(float(r[2]), 4)} for r in rows],
    }


def source_watermark(conn):
    """max(updated) over the rebuild's inputs (§4.3): cards.refreshed_at and
    decks.updated_at. None when both tables are empty."""
    return conn.execute(
        text("SELECT GREATEST((SELECT max(refreshed_at) FROM cards),"
             "                  (SELECT max(updated_at) FROM decks))")
    ).scalar()


def status(conn) -> List[dict]:
    """derived_index_status rows plus a computed freshness flag: a store is
    fresh when its recorded source_watermark covers the current max
    cards/decks update time. Stores with no row yet report 'never_built'."""
    current = source_watermark(conn)
    rows = conn.execute(
        text("SELECT store, rebuilt_at, source_watermark, row_count"
             " FROM derived_index_status ORDER BY store")
    ).all()
    by_store = {r[0]: r for r in rows}
    out = []
    for store in ("vector", "graph"):
        r = by_store.get(store)
        if r is None:
            out.append({"store": store, "status": "never_built",
                        "rebuilt_at": None, "source_watermark": None,
                        "row_count": 0, "fresh": False,
                        "current_watermark": current})
            continue
        wm = r[2]
        fresh = wm is not None and (current is None or wm >= current)
        out.append({"store": r[0], "status": "ok",
                    "rebuilt_at": r[1], "source_watermark": wm,
                    "row_count": int(r[3]), "fresh": fresh,
                    "current_watermark": current})
    return out
