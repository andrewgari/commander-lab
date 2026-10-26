"""LLM infrastructure (ARCHITECTURE.md §2): the only layer that may call an
LLM. `client` re-exports the oracle HTTP client; `review_writer` assembles
the review-writer prompt, calls the oracle, and parses the reply.
"""
from src.server.infra.llm import client, review_writer  # noqa: F401
