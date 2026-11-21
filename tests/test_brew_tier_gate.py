from src.server.domain.draft import brew_gate
from src.server.domain.draft.brew_power import TierGate
from src.server.domain.draft.brew_scoring_config import PowerConfig

GC = lambda n: {"name": n}  # noqa: E731  (names on the checked-in Game Changers snapshot)
CFG = PowerConfig()


def test_tiers():
    assert TierGate(3.0, CFG).tier == "low"
    assert TierGate(6.0, CFG).tier == "mid"
    assert TierGate(8.5, CFG).tier == "high"


def test_low_rejects_game_changers_and_mid_caps_at_three():
    low = TierGate(3.0, CFG)
    assert not low.admit(GC("Demonic Tutor"))
    mid = TierGate(6.0, CFG)
    got = [mid.admit(GC(n)) for n in ("Rhystic Study", "Necropotence", "Cyclonic Rift", "Smothering Tithe")]
    assert got == [True, True, True, False]
    assert TierGate(8.5, CFG).admit(GC("Demonic Tutor"))


def test_commander_counts_against_budget_and_admit_is_idempotent():
    mid = TierGate(6.0, CFG)
    mid.count(GC("Rhystic Study"))  # e.g. a Game Changer commander
    assert [mid.admit(GC(n)) for n in ("Necropotence", "Cyclonic Rift", "Smothering Tithe")] == [True, True, False]
    assert mid.admit(GC("Necropotence"))  # already admitted: no double charge


def test_repair_backfill_is_tier_filtered():
    draft = {"commanders": ["Atraxa, Praetors' Voice"], "cards": []}
    loaded = [("donor", ["Demonic Tutor", "Island"])]
    out = brew_gate._tier_filter_sources(loaded, draft, {"target_power": 3.0}, None)
    assert out == [("donor", ["Island"])]
    assert brew_gate._tier_filter_sources(loaded, draft, {"target_power": 9.0}, None) == loaded
