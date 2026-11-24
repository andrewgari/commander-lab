"""BP-2: secret commander on Brew (validation, pinning, tutors, EDHREC ignored, prompts, API round-trip)."""
import json
from unittest.mock import patch

import pytest

from src.server.domain.draft import brew, brew_builder, brew_stages
from src.server.domain.draft.brew_builder import StubBrewBuilder
from tests.support import brew_harness as bh
from tests.support.brew_harness import _info, build_fixture_catalog, seed_catalog
from tests.test_brew import FakeRedis

ATRAXA = "Atraxa, Praetors' Voice"
MASAMUNE = "The Masamune"
TUTOR = "Fixture Equipment Tutor"
TYPE_LINES = {MASAMUNE: "Legendary Artifact — Equipment", TUTOR: "Sorcery"}


def _catalog():
    cat = build_fixture_catalog()
    cat.add(_info(MASAMUNE, "", ("Artifact", "Equipment"), supers=("Legendary",),
                  text="Equipped creature gets +2/+2."))
    cat.add(_info(TUTOR, "W", ("Sorcery",), text="Search your library for an Equipment card, reveal it, put it into your hand."))
    cat.add(_info("Fixture Any Tutor", "B", ("Sorcery",), text="Search your library for a card, put it into your hand."))
    cat.add(_info("Fixture Plain Spell", "G", ("Sorcery",), text="Draw a card."))
    cat.add(_info("Fixture Red Equipment", "R", ("Artifact", "Equipment"), supers=("Legendary",)))
    cat.add(_info("Fixture Banned Equipment", "", ("Artifact", "Equipment"), legality="banned"))
    return cat


def _seed(r, cat):
    seed_catalog(r, cat)
    for n, tl in TYPE_LINES.items():
        meta = json.loads(r.get(f"card_meta:{n}"))
        meta["type_line"] = tl
        r.set(f"card_meta:{n}", json.dumps(meta))


@pytest.fixture(autouse=True)
def _offline_scryfall():
    deps = brew_stages.Deps(scryfall_search=lambda q: [])
    prev = brew_stages.get_deps()
    brew_stages.set_deps(deps)
    yield
    brew_stages.set_deps(prev)


def _start(r, secret="", bracket=3, **kw):
    return brew.start_brew(r, ATRAXA, [], "best_build", bracket=bracket, secret_commander=secret, **kw)


@pytest.fixture
def r():
    r = FakeRedis()
    _seed(r, _catalog())
    return r


# ---- validation -----------------------------------------------------------

def test_valid_secret_commander_stored_canonical(r):
    s = _start(r, "  The Masamune ")
    assert s["secret_commander"] == MASAMUNE
    assert brew.get_session(r, s["session_id"])["secret_commander"] == MASAMUNE


@pytest.mark.parametrize("name,why", [
    ("Totally Not A Card", "not a card we know"),
    ("Fixture Red Equipment", "colour identity"),
    ("Fixture Banned Equipment", "not legal"),
])
def test_invalid_secret_commander_rejected(r, name, why):
    with pytest.raises(ValueError, match=why):
        _start(r, name)


def test_game_changer_secret_commander_breaks_low_bracket(r):
    assert _start(r, "Rhystic Study", bracket=3)["secret_commander"] == "Rhystic Study"
    with pytest.raises(ValueError, match="breaks Bracket 2"):
        _start(r, "Rhystic Study", bracket=2)


def test_blank_field_is_off(r):
    s = _start(r, "")
    assert s["secret_commander"] == ""
    with pytest.raises(ValueError):
        brew.start_brew(r, ATRAXA, [], "best_build", secret_commander=5)


def test_old_session_without_field_loads(r):
    s = _start(r)
    old = {k: v for k, v in s.items() if k != "secret_commander"}
    r.set(brew._session_key(s["session_id"]), json.dumps(old))
    assert brew.get_session(r, s["session_id"])["secret_commander"] == ""


def test_api_round_trip_and_400s(r):
    from fastapi.testclient import TestClient
    import src.server.app.main as app_module
    with patch.object(app_module, "r", r):
        c = TestClient(app_module.app)
        body = {"commanders": [ATRAXA], "build_mode": "owned_first", "secret_commander": MASAMUNE}
        res = c.post("/api/brew/start", json=body)
        assert res.status_code == 201, res.text
        assert res.json()["session"]["secret_commander"] == MASAMUNE
        for bad, extra in (("Nope Nothing", {}), ("Fixture Red Equipment", {}), ("Fixture Banned Equipment", {}),
                           ("Rhystic Study", {"bracket": 2})):
            res = c.post("/api/brew/start", json={**body, "secret_commander": bad, **extra})
            assert res.status_code == 400 and res.json()["error"], bad
        assert c.post("/api/brew/start", json={k: v for k, v in body.items() if k != "secret_commander"}).status_code == 201


def test_form_has_secret_field_and_js_sends_it():
    root = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    html = open(f"{root}/src/web/templates/brew.html", encoding="utf-8").read()
    js = open(f"{root}/src/web/static/js/brew.js", encoding="utf-8").read()
    assert 'id="brewSecret"' in html and "secret_commander:" in js and "resume.secret_commander" in js


# ---- builder ---------------------------------------------------------------

def _run(secret, bracket=3):
    """run_brew pins bracket by power band; drive start_brew ourselves with the harness's fakes."""
    import contextlib
    from unittest import mock
    cat = _catalog()
    rr = FakeRedis()
    rr.set("decks", json.dumps(bh._donor_decks([ATRAXA], cat)))
    _seed(rr, cat)
    with contextlib.ExitStack() as st:
        st.enter_context(mock.patch.object(brew_builder.reconstruct_pools, "redis_price_lookup",
                                           lambda r, refresh=True: (lambda names: {})))
        orig = brew_builder.get_builder()
        brew_builder.set_builder(StubBrewBuilder())
        st.callback(brew_builder.set_builder, orig)
        s = brew.start_brew(rr, [ATRAXA], ["archidekt:901"], "best_build", bracket=bracket, secret_commander=secret)
        brew.approve_rules(rr, s["session_id"], [])
        brew.trigger_build(rr, s["session_id"], sync=True)
        return brew.get_session(rr, s["session_id"]), brew.get_drafts(rr, s["session_id"])[0]


def _names(draft):
    return [c["name"] for c in draft["cards"]]


def test_secret_commander_always_in_final_99_with_tutor():
    s, d = _run(MASAMUNE)
    names = _names(d)
    assert names.count(MASAMUNE) == 1 and d["secret_commander"] == MASAMUNE
    assert TUTOR in names and TUTOR in d["secret_tutors"]
    assert "Fixture Any Tutor" not in names or names.index(TUTOR) < names.index("Fixture Any Tutor")
    assert sum(int(c.get("quantity") or 1) for c in d["cards"]) == 100


def test_blank_secret_matches_no_field():
    _, a = _run("")
    _, b = _run("")
    assert _names(a) == _names(b)
    assert MASAMUNE not in _names(a) and a["secret_commander"] == "" and a["secret_tutors"] == []


def test_tutor_classification():
    words = brew_builder.secret_type_words("Legendary Artifact — Equipment")
    assert words[0] == "Equipment" and "Legendary" not in words
    f = brew_builder.fetches_secret
    assert f("Search your library for an Equipment card", words) == 2
    assert f("Search your library for a card, then shuffle", words) == 1
    assert f("Search your library for a creature card", words) == 0
    assert f("Draw a card", words) == 0


def test_edhrec_ignored_for_secret_commander():
    sk = brew_stages.run_skeleton(FakeRedis(), {"commanders": [ATRAXA], "secret_commander": MASAMUNE}, ["W"], {"slots": []})
    assert sk["edhrec"]["cards"] == {} and sk["degraded"] is None
    scorer = brew_builder._Scorer(FakeRedis(), {"commanders": [ATRAXA], "secret_commander": MASAMUNE},
                                  brew_builder.BrewScoringConfig.from_env())
    assert scorer.edhrec == {}
    assert brew_builder.guidance_note({"secret_commander": MASAMUNE}).count("SECRET COMMANDER") == 1


def test_prompts_state_secret_commander():
    note = brew_builder.guidance_note({"secret_commander": MASAMUNE, "archetype": "let the brewer decide"})
    for frag in (MASAMUNE, "MUST be in the 99", "tutors", "Bracket always wins", "Ignore popular guides", "synergy"):
        assert frag in note
    assert "secret commander first" in note
    from src.server.domain.draft import brew_panel, brew_review
    msgs = brew_panel._prompt("synergy_flavor", {"secret_commander": MASAMUNE, "commanders": [ATRAXA]}, ["X"], [])
    assert MASAMUNE in json.dumps(msgs)
    assert brew_review.deterministic_check({"cards": [{"name": "A", "quantity": 100}], "secret_commander": MASAMUNE})[-1] \
        .startswith("secret commander")


def test_panel_cannot_cut_secret_commander():
    d = {"cards": [{"name": ATRAXA}] + [{"name": f"c{i}"} for i in range(98)], "secret_commander": MASAMUNE}
    brew_builder.pin_secret(None, {"secret_commander": MASAMUNE, "commanders": [ATRAXA]}, d)
    assert MASAMUNE in [c["name"] for c in d["cards"]] and len(d["cards"]) == 99
