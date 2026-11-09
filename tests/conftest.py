"""Package-level test setup: never start interaction-graph background
rebuilds (network: Scryfall + LLM) from route tests. Tests that exercise
the rebuild call analytics.interactions.service directly with stubs."""
import os

os.environ.setdefault("INTERACTIONS_DISABLED", "1")


import pytest


@pytest.fixture(autouse=True)
def _brew_oracle_reviewer_offline():
    """Brew tests never call the real oracle: default to a passing fake that
    skips the network. The deterministic gate in brew_review still runs
    unless a test patches brew_review.deterministic_check."""
    from src.server.domain.draft import brew_review

    brew_review.set_reviewer(lambda draft, session: {"status": "pass", "reasons": [], "source": "test"})
    yield
    brew_review.set_reviewer(None)
