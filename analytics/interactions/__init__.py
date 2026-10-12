"""
Card interaction graph (AN-1): produce/consume tagging + deck/collection graphs.

    vocab         fixed resource vocabulary (what cards produce / consume)
    oracle_rules  deterministic oracle-text rules
    tagger        Scryfall Tagger + oracle pass, LLM pass, forever Redis cache
    llm           LLM provider interface: Gemini -> Nous -> (Anthropic slot)
    graph         pure graph construction
    service       rebuild orchestration, run/cost log, nightly job, reads

See docs/INTERACTION_GRAPH.md.
"""
from .vocab import RESOURCE_KEYS, RESOURCES, VOCAB_VERSION  # noqa: F401
