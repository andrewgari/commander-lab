"""Deck-review pipeline (CONTRACT §3): Pass A brief, Pass B analysis, then C/D/E in later cards.

Domain code only: no FastAPI, no SQLAlchemy, no ``src.server.infra`` imports (AGENT_RULES §2).
The LLM transport is an injected ``LLMClient`` (see ``llm.py``); the HTTP implementations live in
``src/server/infra/llm/provider.py``.
"""
