"""QA (tester, PR #525): adversarial checks for brew hard rules the PR suite does not cover."""
import json
import pytest

import src.server.domain.draft.brew as brew
from src.server.domain.draft import brew_builder
from tests.test_brew_rules_draft import env, RULE  # noqa: F401  (fixture + rule reuse)


def _pool_names(pools):
    return {pc.name for tier in ("tier1", "tier2", "tier3") for pc in getattr(pools, tier)}


def test_build_pools_excludes_violating_cards_from_every_tier(env):
    r, sid = env
    # Baseline: without the approved rule the Dragon is in the pools (proves the probe can see it).
    session = brew.get_session(r, sid)
    pools = brew._build_pools(r, session, price_lookup=lambda _: {})
    if "Shivan Dragon" not in _pool_names(pools):
        pytest.skip("seed does not place Shivan Dragon in any pool; probe cannot judge")
    brew.approve_rules(r, sid, [RULE])
    session = brew.get_session(r, sid)
    pools = brew._build_pools(r, session, price_lookup=lambda _: {})
    assert "Shivan Dragon" not in _pool_names(pools), "approved rule must remove Dragons from every pool"
    assert any(x["card"] == "Shivan Dragon" for x in pools.rule_removed)
