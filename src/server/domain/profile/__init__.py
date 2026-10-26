"""Profile domain: evolving taste profile (docs/ARCHITECTURE.md §0, §4.2).

Snapshots are computed from decks + collection by deterministic code only
(AGENT_RULES §5): curve preferences, archetype mix, ownership coverage. No
LLM, no guessing; missing card facts render as explicit "unavailable" fields
(AGENT_RULES §4), never dressed-up zeros.
"""
