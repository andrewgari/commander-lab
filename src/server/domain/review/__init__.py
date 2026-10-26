"""Review pipeline domain (Slice 3, ARCHITECTURE.md §6).

Pure code only: no FastAPI, no SQLAlchemy, no src.server.infra imports
(AGENT_RULES.md §2). Every number and label here is deterministic; the LLM
writer (writer.py) receives its client as an injected factory and the
verifier (verifier.py) its Scryfall check as an injected callable, so the
whole pipeline is testable offline.

Modules:
    fact_pack    deterministic code passes (mana math, spirit, bracket/salt,
                 combo lines, own-first inputs, composition/curve/genre/
                 interactions/brackets/inventory/profile metrics)
    candidates   first-pass retrieval (EDHREC + collection tags) + ranked
                 adds/cuts with deterministic scores
    verifier     every card name in analysis + LLM output against Scryfall
    writer       prompt payload + reply parsing (pure halves of the LLM step)
    doc          typed-section stored doc + dashboard panels from one row
    pipeline     stage orchestration + review_jobs audit trail
"""
from src.server.domain.review import (  # noqa: F401
    candidates,
    doc,
    fact_pack,
    pipeline,
    verifier,
    writer,
)
