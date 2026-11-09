"""Package-level test setup: never start interaction-graph background
rebuilds (network: Scryfall + LLM) from route tests. Tests that exercise
the rebuild call analytics.interactions.service directly with stubs."""
import os

os.environ.setdefault("INTERACTIONS_DISABLED", "1")
