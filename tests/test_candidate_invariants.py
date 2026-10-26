"""Probe: candidate-pool filters for legality and color identity must be exercised by the fixture stream."""
from types import SimpleNamespace as NS
import copy
from tests.test_candidate_pool import DECK, SEARCH, prof, sources, candidate_pool


def _probe_card(name, **over):
    base = copy.deepcopy(SEARCH["ramp"][0])
    base.update(over)
    base["name"] = name
    base["oracle_id"] = "probe-" + name
    return base


def test_illegal_card_in_search_stream_is_dropped():
    bad = _probe_card("Probe Illegal Ramp", legalities={**SEARCH["ramp"][0]["legalities"], "commander": "banned"}, color_identity=[])
    def search(q):
        return [bad] + list(SEARCH["ramp"]) if "ramp" in q else []
    pool = candidate_pool(DECK, None, prof(cap=None), sources=sources(edhrec=lambda c: (_ for _ in ()).throw(RuntimeError("off")), scryfall_search=search))
    assert "Probe Illegal Ramp" not in pool.names()
    assert pool.excluded.get("not_commander_legal", 0) == 1


def test_off_identity_card_in_search_stream_is_dropped():
    bad = _probe_card("Probe Blue Ramp", color_identity=["U"])  # Atraxa is WUBG -> U is inside; use a color outside WUBG
    bad = _probe_card("Probe Red Ramp", color_identity=["R"])
    def search(q):
        return [bad] + list(SEARCH["ramp"]) if "ramp" in q else []
    pool = candidate_pool(DECK, None, prof(cap=None), sources=sources(edhrec=lambda c: (_ for _ in ()).throw(RuntimeError("off")), scryfall_search=search))
    assert "Probe Red Ramp" not in pool.names()
    assert pool.excluded.get("outside_color_identity", 0) == 1


def test_basic_land_in_stream_is_dropped():
    basic = _probe_card("Wastes", type_line="Basic Land — Wastes", color_identity=[])
    def search(q):
        return [basic] + list(SEARCH["ramp"]) if "ramp" in q else []
    pool = candidate_pool(DECK, None, prof(cap=None), sources=sources(edhrec=lambda c: (_ for _ in ()).throw(RuntimeError("off")), scryfall_search=search))
    assert "Wastes" not in pool.names()
