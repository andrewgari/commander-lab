"""Persist interaction-graph edges into Postgres (card_interaction_edges).

Spec §2 discrepancy flag: the in-memory builder
(domain/analytics/interactions/graph.py) stays the algorithm; this module is
the write side that makes PG the dossier's read store. Registered with the
rebuild as an edge SINK (dependency injection — domain/ never imports
infra/):

    interactions_service.register_edge_sink(make_pg_edge_sink())

Edges are capped to cards in Andrew's decks/collection by construction: deck
graphs are materialized over decklists and the collection graph over owned
cards only (spec §5). Name-keyed edges resolve to oracle_ids through the
cards table; unresolved names are counted and skipped (their dossier page
cannot exist yet anyway).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable, Optional

from sqlalchemy.orm import sessionmaker

from src.server.domain.analytics.interactions import graph as graph_mod
from src.server.domain.analytics.interactions.vocab import RESOURCE_BY_KEY, RESOURCE_KEYS
from src.server.infra.db import repositories as repo
from src.server.infra.db.session import get_session_factory, session_scope

log = logging.getLogger("commander_lab.interaction_edges")

EdgeSink = Callable[[list[dict], Optional[dict], int], dict]

_ORDER = {k: i for i, k in enumerate(RESOURCE_KEYS)}


def collect_edges(
    deck_graphs: list[dict], collection_graph: Optional[dict]
) -> dict[tuple[str, str], list[str]]:
    """Merge name-keyed edges from deck graphs + the collection graph into
    {(src_name, dst_name): sorted unique reason keys}."""
    pair_reasons: dict[tuple[str, str], set] = defaultdict(set)

    for g in deck_graphs or []:
        for e in g.get("edges", []):
            if e.get("source") and e.get("target") and e["source"] != e["target"]:
                pair_reasons[(e["source"], e["target"])].update(e.get("reasons") or [])

    if collection_graph:
        # The collection graph stores nodes + a resource index, not
        # materialized edges; derive the pairs from the index (same logic as
        # graph.collection_neighbors, for every node at once).
        index = collection_graph.get("index", {})
        for key, entry in index.items():
            for src in entry.get("producers", []):
                for dst in entry.get("consumers", []):
                    if src != dst:
                        pair_reasons[(src, dst)].add(key)

    return {
        pair: sorted(reasons, key=lambda k: _ORDER.get(k, len(_ORDER)))
        for pair, reasons in pair_reasons.items()
    }


def persist_edges(session, pair_reasons: dict[tuple[str, str], list[str]],
                  vocab_version: int) -> dict:
    """Resolve names to oracle_ids and replace the edge table. Returns a
    summary (rows written, unresolved pairs skipped)."""
    names = {n for pair in pair_reasons for n in pair}
    cards = repo.get_cards_by_names(session, names)

    edges = []
    unresolved = 0
    for (src, dst), reasons in pair_reasons.items():
        src_card, dst_card = cards.get(src), cards.get(dst)
        if src_card is None or dst_card is None:
            unresolved += 1
            continue
        edges.append({
            "src_oracle_id": src_card["oracle_id"],
            "dst_oracle_id": dst_card["oracle_id"],
            "vocab_version": vocab_version,
            "reasons": reasons,
            "reason_labels": [RESOURCE_BY_KEY[k].label for k in reasons
                              if k in RESOURCE_BY_KEY],
            "reason_text": "; ".join(graph_mod._explain(src, dst, k) for k in reasons),
            "weight": len(reasons),
        })
    written = repo.replace_interaction_edges(session, edges)
    return {
        "pairs": len(pair_reasons),
        "rows_written": written,
        "pairs_unresolved": unresolved,
    }


def make_pg_edge_sink(session_factory: Optional[sessionmaker] = None) -> EdgeSink:
    """The edge sink registered with the interaction rebuild. Best-effort:
    a database failure is logged and never fails the rebuild (Redis graphs
    remain the in-app read path until the dossier cutover)."""
    def _sink(deck_graphs: list[dict], collection_graph: Optional[dict],
              vocab_version: int) -> dict:
        factory = session_factory or get_session_factory()
        pair_reasons = collect_edges(deck_graphs, collection_graph)
        with session_scope(factory) as session:
            summary = persist_edges(session, pair_reasons, vocab_version)
        log.info(
            "persisted interaction edges: %d rows (%d pairs, %d unresolved)",
            summary["rows_written"], summary["pairs"], summary["pairs_unresolved"],
        )
        return summary

    return _sink
