"""Package-level test setup: never start interaction-graph background
rebuilds (network: Scryfall + LLM) from route tests. Tests that exercise
the rebuild call analytics.interactions.service directly with stubs."""
import os

os.environ.setdefault("INTERACTIONS_DISABLED", "1")

import pytest  # noqa: E402


class _OfflineEDHRec:
    def get_commander_page(self, names, theme=None):
        from src.server.domain.analytics.providers.edhrec import EDHRecRequestError
        raise EDHRecRequestError("offline in tests")


@pytest.fixture(autouse=True)
def _brew_stages_offline():
    """Brew's skeleton stage reaches EDHREC and Scryfall; no test may touch the network unless it
    installs its own brew_stages.Deps."""
    from src.server.domain.draft import brew_stages
    brew_stages.set_deps(brew_stages.Deps(edhrec_client=_OfflineEDHRec(), scryfall_search=lambda q: []))
    yield
    brew_stages.set_deps(None)
