"""BracketGate: bracket (1-5) is the hard construction gate; power never drives a hard limit."""
from src.server.domain.draft import brew_gate
from src.server.domain.draft.brew_power import BracketGate, parse_bracket

import pytest

GC = lambda n: {"name": n}  # noqa: E731  (names on the checked-in Game Changers snapshot)
GCS = ["Rhystic Study", "Necropotence", "Cyclonic Rift", "Smothering Tithe", "Demonic Tutor"]


def test_default_bracket_is_three():
    assert parse_bracket(None) == 3 and BracketGate().bracket == 3


@pytest.mark.parametrize("bad", [0, 6, "x", 2.5, True])
def test_bad_bracket_rejected(bad):
    with pytest.raises(ValueError):
        parse_bracket(bad)


@pytest.mark.parametrize("b", [1, 2])
def test_bracket_1_2_never_admit_a_game_changer(b):
    g = BracketGate(b)
    assert [g.admit(GC(n)) for n in GCS] == [False] * len(GCS)


def test_bracket_3_admits_at_most_three_game_changers():
    g = BracketGate(3)
    assert [g.admit(GC(n)) for n in GCS] == [True, True, True, False, False]


def test_bracket_4_5_unrestricted():
    for b in (4, 5):
        g = BracketGate(b)
        assert all(g.admit(GC(n)) for n in GCS)


def test_commander_counts_against_budget_and_admit_is_idempotent():
    g = BracketGate(3)
    g.count(GC("Rhystic Study"))
    assert [g.admit(GC(n)) for n in ("Necropotence", "Cyclonic Rift", "Smothering Tithe")] == [True, True, False]
    assert g.admit(GC("Necropotence"))


def test_low_brackets_refuse_mass_land_denial_and_cap_extra_turns():
    assert not BracketGate(3).admit(GC("Armageddon"))
    g = BracketGate(2)
    got = [g.admit(GC(n)) for n in ("Time Warp", "Temporal Manipulation", "Walk the Aeons")]
    assert got == [True, True, False]
    assert not BracketGate(1).admit(GC("Time Warp"))
    assert BracketGate(4).admit(GC("Armageddon"))


def test_low_brackets_keep_fast_mana_and_combo_limits():
    g = BracketGate(2)
    assert g.admit(GC("Sol Ring")) and not g.admit(GC("Mana Crypt"))
    assert BracketGate(3).admit(GC("Mana Crypt"))


def test_power_nine_at_bracket_two_still_obeys_bracket_two_gate(tmp_path):
    from src.server.domain.draft import brew
    from tests.test_brew import FakeRedis
    s = brew.start_brew(FakeRedis(), "Atraxa", [], "owned_first", power_level=9, bracket=2)
    assert s["target_power"] == 9.0 and s["target_bracket"] == 2
    g = BracketGate(s["target_bracket"])
    assert not g.admit(GC("Rhystic Study"))


def test_repair_backfill_is_bracket_filtered():
    draft = {"commanders": ["Atraxa, Praetors' Voice"], "cards": []}
    loaded = [("donor", ["Demonic Tutor", "Island"])]
    assert brew_gate._tier_filter_sources(loaded, draft, {"target_bracket": 2, "target_power": 9.0}, None) == [("donor", ["Island"])]
    assert brew_gate._tier_filter_sources(loaded, draft, {"target_bracket": 4, "target_power": 1.0}, None) == loaded
