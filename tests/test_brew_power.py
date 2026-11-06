"""Brew power-level targeting: parsing, signals, scoring term, estimate, end-to-end."""
import pytest

from src.server.domain.draft import brew_power as bp
from src.server.domain.draft import brew_scoring as bs
from src.server.domain.draft.brew_scoring_config import BrewScoringConfig, PowerConfig, SkeletonRange
from src.server.domain.draft.brew_builder import StubBrewBuilder
from tests.support import deck_assertions as da
from tests.support.brew_harness import run_brew

ATRAXA = "Atraxa, Praetors' Voice"


# ---- request parsing -------------------------------------------------------

def test_parse_default_alias_numeric_and_range():
    assert bp.parse_power_level(None) == PowerConfig().default_level
    assert bp.parse_power_level("") == PowerConfig().default_level
    assert bp.parse_power_level("low") < bp.parse_power_level("mid") < bp.parse_power_level("high")
    assert bp.parse_power_level("7") == 7.0
    assert bp.parse_power_level(9) == 9.0
    for bad in (0, 11, -1, "11", "ultra", True, [3], float("nan")):
        with pytest.raises(ValueError):
            bp.parse_power_level(bad)


def test_start_brew_validates_and_defaults():
    from src.server.domain.draft import brew
    from tests.test_brew import FakeRedis
    r = FakeRedis()
    r.set("decks", "[]")
    assert brew.start_brew(r, "Atraxa", [], "owned_first")["target_power"] == PowerConfig().default_level
    assert brew.start_brew(r, "Atraxa", [], "owned_first", power_level="high")["target_power"] == 8.5
    with pytest.raises(ValueError, match="power_level"):
        brew.start_brew(r, "Atraxa", [], "owned_first", power_level="42")


# ---- signal classification -------------------------------------------------

def test_signals():
    s = bp.card_signals({"name": "Sol Ring"})
    assert s["fast_mana"] and not s["tutor"]
    assert bp.card_signals({"name": "Demonic Tutor"})["tutor"]
    assert bp.card_signals({"name": "Rhystic Study"})["game_changer"]
    assert bp.card_signals({"name": "X", "tags": ["Combo"]})["combo"]
    assert bp.card_signals({"name": "Mind Stone", "type_line": "Artifact", "cmc": 2,
                            "oracle_text": "{T}: Add {C}."})["fast_mana"] is False
    assert bp.card_signals({"name": "Odd Rock", "type_line": "Artifact", "cmc": 1,
                            "oracle_text": "{T}: Add {C}{C}."})["fast_mana"]
    assert not any(bp.card_signals({"name": "Grizzly Bears"}).values())
    assert bp.card_signals({"name": "Zzz", "game_changer": False}, game_changers=["Zzz"])["game_changer"]


# ---- scoring term ----------------------------------------------------------

def _ctx(power, **kw):
    return bs.ScoreContext(skeleton={"ramp": SkeletonRange(8, 12)}, target_power=power, **kw)


def test_term_sign_flips_with_power():
    cfg = BrewScoringConfig()
    for name in ("Sol Ring", "Demonic Tutor", "Rhystic Study"):
        lo = bs.score_card({"name": name}, _ctx(2)).components["power_level"]
        hi = bs.score_card({"name": name}, _ctx(9.5)).components["power_level"]
        assert lo < 0 < hi, name
    plain = bs.score_card({"name": "Grizzly Bears"}, _ctx(9.5), cfg).components["power_level"]
    assert plain == 0
    assert bs.score_card({"name": "Sol Ring"}, bs.ScoreContext()).components["power_level"] == 0


def test_curve_term_prefers_target_average():
    ctx = _ctx(5.5, mv_sum=30.0, mv_n=10)  # running avg 3.0, target avg 3.0
    ok = bs.score_card({"name": "a", "cmc": 3, "type_line": "Creature"}, ctx).components["power_level"]
    far = bs.score_card({"name": "b", "cmc": 9, "type_line": "Creature"}, ctx).components["power_level"]
    land = bs.score_card({"name": "c", "cmc": 0, "type_line": "Land"}, ctx).components["power_level"]
    assert ok > far and land == 0


def test_weights_are_config_driven():
    off = BrewScoringConfig(power=PowerConfig(w_fast_mana=0, w_curve=0))
    assert bs.score_card({"name": "Sol Ring"}, _ctx(9.5), off).components["power_level"] == 0


# ---- selection and estimate ------------------------------------------------

POOL = ([{"name": n, "tags": []} for n in ("Sol Ring", "Mana Crypt", "Chrome Mox", "Mana Vault",
                                          "Demonic Tutor", "Vampiric Tutor", "Mystical Tutor", "Worldly Tutor")]
        + [{"name": f"Plain {i:02d}", "cmc": 3, "type_line": "Creature", "tags": []} for i in range(24)])


def _pick(power, n=12):
    return bs.select_cards(POOL, bs.ScoreContext(target_power=power), n)


def _count(cards, sig):
    return sum(bp.card_signals(c)[sig] for c in cards)


def test_selection_differs_by_power():
    lo, hi = _pick(2), _pick(9.5)
    assert _count(hi, "fast_mana") > _count(lo, "fast_mana")
    assert _count(hi, "tutor") > _count(lo, "tutor")
    assert _count(lo, "fast_mana") + _count(lo, "tutor") == 0
    assert {c["name"] for c in lo} != {c["name"] for c in hi}


def test_estimate_correlates_with_requested_power():
    ests = []
    for p in (1, 3, 5, 7, 9, 10):
        e = bp.estimate_power_level(_pick(p, 16))
        assert 1.0 <= e["power"] <= 10.0 and "counts" in e["breakdown"]
        ests.append(e["power"])
    assert ests == sorted(ests) and ests[-1] - ests[0] >= 2.0


def test_estimate_empty_and_curve():
    assert bp.estimate_power_level([])["breakdown"]["avg_mana_value"] is None
    cheap = bp.estimate_power_level([{"name": "a", "cmc": 1}] * 10)["power"]
    pricey = bp.estimate_power_level([{"name": "a", "cmc": 6}] * 10)["power"]
    assert cheap > pricey


# ---- end to end through the real stub builder ------------------------------

def _run(power):
    return run_brew(ATRAXA, power, sources=True, build_mode="best_build", builder=StubBrewBuilder())


@pytest.mark.parametrize("power", ["low", "mid", "high"])
def test_stub_output_valid_and_reports_estimate(power):
    res = _run(power)
    d = res.deck
    assert d["estimated_power"] == d["metadata"]["estimated_power"]["power"]
    assert "counts" in d["metadata"]["estimated_power"]["breakdown"]
    assert d["target_power"] == res.session["target_power"]
    assert d["cards"]
    da.assert_not_banned(d, res.catalog)
    da.assert_color_identity(d, res.catalog)
    da.assert_singleton(d, res.catalog)


def test_stub_low_vs_high_differ():
    lo, hi = _run("low").deck, _run("high").deck
    sig = lambda d, s: sum(bp.card_signals(c)[s] for c in d["cards"])
    assert sig(hi, "fast_mana") + sig(hi, "tutor") + sig(hi, "game_changer") > \
        sig(lo, "fast_mana") + sig(lo, "tutor") + sig(lo, "game_changer")
    assert hi["estimated_power"] > lo["estimated_power"]
