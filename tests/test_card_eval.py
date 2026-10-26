"""Card evaluation service tests: EDHREC, HF and Scryfall are all mocked."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.card_eval import (DeckContext, ScoringWeights, UnknownCardError, InvalidDeckError, evaluate_card)
from analytics.card_eval import edhrec_data
from analytics.card_eval.config import ROLE_LABELS
from analytics.card_eval.hf_classifier import HFClassifier, HFError
from analytics.card_eval.service import compute_strength
from analytics.providers.edhrec import EDHRecNotFoundError, EDHRecRequestError


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, *a, **kw):
        self.store[k] = v


def view(name, inc, syn, pot=1000):
    return {"name": name, "synergy": syn, "num_decks": int(inc * pot), "potential_decks": pot}


PAGE = {"container": {"json_dict": {"card": {"num_decks": 1000}, "cardlists": [
    {"tag": "highsynergycards", "cardviews": [view("Cultivate", 0.5, 0.3), view("Rhystic Study", 0.3, 0.1),
                                              view("Lightning Bolt", 0.2, 0.2)]},
    {"tag": "manaartifacts", "cardviews": [view("Sol Ring", 0.9, 0.05), view("Arcane Signet", 0.8, 0.02),
                                           view("Cultivate", 0.5, 0.3)]},
    {"tag": "topcards", "cardviews": [view("Sol Ring", 0.9, 0.05), view("Illegal Card", 0.7, 0.4),
                                      view("Forest", 0.9, 0.0)]},
]}}}

CARDS = {
    "atraxa": {"name": "Atraxa", "color_identity": "WUBG", "legal": True, "type_line": "Legendary Creature", "oracle_text": ""},
    "sol ring": {"name": "Sol Ring", "oracle_id": "o1", "color_identity": "", "legal": True, "type_line": "Artifact",
                 "oracle_text": "{T}: Add {C}{C}."},
    "cultivate": {"name": "Cultivate", "color_identity": "G", "legal": True, "type_line": "Sorcery",
                  "oracle_text": "Search your library for up to two basic land cards, put one onto the battlefield tapped"},
    "arcane signet": {"name": "Arcane Signet", "color_identity": "", "legal": True, "type_line": "Artifact",
                      "oracle_text": "{T}: Add one mana of any color in your commander's color identity."},
    "rhystic study": {"name": "Rhystic Study", "color_identity": "U", "legal": True, "type_line": "Enchantment",
                      "oracle_text": "Whenever an opponent casts a spell, you may draw a card"},
    "lightning bolt": {"name": "Lightning Bolt", "color_identity": "R", "legal": True, "type_line": "Instant",
                       "oracle_text": "Lightning Bolt deals 3 damage to any target."},
    "illegal card": {"name": "Illegal Card", "color_identity": "G", "legal": False, "type_line": "Sorcery", "oracle_text": "x"},
}


def lookup(names):
    return {k: v for k, v in CARDS.items() if k in {n.lower() for n in names}}


class FakeEdhrec:
    def __init__(self, page=PAGE, exc=None):
        self.page, self.exc, self.calls = page, exc, 0

    def get_commander_page(self, names):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.page


class FakeHF(HFClassifier):
    def __init__(self, scores=None, exc=None):
        super().__init__(token="t", mode="api", model="m/x")
        self._scores, self._exc = scores, exc

    def classify(self, text):
        if self._exc:
            raise self._exc
        return self._scores


DECK = DeckContext(commanders=["Atraxa"], cards=["Sol Ring", "Forest"], kind="digital")


def run(r=None, card="Sol Ring", deck=DECK, **kw):
    kw.setdefault("edhrec_client", FakeEdhrec())
    kw.setdefault("classifier", FakeHF({"ramp": 0.93, "threat": 0.1}))
    return evaluate_card(r or FakeRedis(), card, deck, card_lookup=lookup, **kw)


def test_full_evaluation():
    res = run()
    assert res.card.name == "Sol Ring"
    assert res.roles[0].role == "ramp" and res.roles[0].source in ("model", "heuristic")
    assert res.edhrec.status == "ok" and res.edhrec.inclusion == 0.9 and res.edhrec.slot == "manaartifacts"
    assert "Arcane Signet" in res.edhrec.slot_peers
    assert 0 < res.strength <= 100 and res.model.status == "ok"
    names = [c.name for c in res.candidates]
    assert names[0] == "Cultivate" and res.candidates[0].same_role  # ramp peer first
    assert "Illegal Card" not in names and "Forest" not in names and "Sol Ring" not in names
    assert "Lightning Bolt" not in names  # outside WUBG
    assert res.candidates[0].roles[0] == "ramp"
    assert any(not c.same_role for c in res.candidates)
    json.loads(res.model_dump_json())  # serialisable


def test_edhrec_cached_between_calls():
    r, client = FakeRedis(), FakeEdhrec()
    run(r, edhrec_client=client)
    run(r, edhrec_client=client)
    assert client.calls == 1


def test_card_not_listed_still_scores_with_model():
    res = run(card="Cultivate", deck=DeckContext(commanders=["Atraxa"]))
    assert res.edhrec.status == "ok"
    res2 = evaluate_card(FakeRedis(), "Arcane Signet", DECK, card_lookup=lambda n: lookup(n), edhrec_client=FakeEdhrec({"container": {"json_dict": {"cardlists": []}}}),
                         classifier=FakeHF({"ramp": 0.9}))
    assert res2.edhrec.status == "card_not_listed" and res2.edhrec.inclusion is None
    assert res2.strength > 0 and res2.candidates == []
    assert any("not on the commander" in w for w in res2.warnings)


def test_unknown_card():
    with pytest.raises(UnknownCardError):
        run(card="Not A Real Card")
    with pytest.raises(UnknownCardError):
        run(card="")
    with pytest.raises(UnknownCardError):  # oracle id missing from Redis
        run(card="12345678-1234-1234-1234-123456789abc")


def test_oracle_id_resolves_via_redis():
    r = FakeRedis()
    oid = "12345678-1234-1234-1234-123456789abc"
    r.set(f"card:{oid}", json.dumps({"oracle_id": oid, "name": "Sol Ring"}))
    assert run(r, card=oid).card.name == "Sol Ring"


def test_no_commander():
    with pytest.raises(InvalidDeckError):
        run(deck=DeckContext(commanders=[" "]))


def test_commander_without_edhrec_page():
    r = FakeRedis()
    client = FakeEdhrec(exc=EDHRecNotFoundError("404"))
    res = run(r, edhrec_client=client)
    assert res.edhrec.status == "no_commander_page" and res.candidates == []
    assert res.roles and res.strength > 0  # model + heuristic still score
    run(r, edhrec_client=client)
    assert client.calls == 1  # negative-cached


def test_edhrec_down_serves_stale_or_degrades():
    res = run(edhrec_client=FakeEdhrec(exc=EDHRecRequestError("429")))
    assert res.edhrec.status == "unavailable" and res.candidates == []
    r = FakeRedis()
    run(r)
    key = next(iter(r.store))
    rec = json.loads(r.store[key]); rec["fetched_at"] = "2000-01-01T00:00:00+00:00"; r.store[key] = json.dumps(rec)
    res = run(r, edhrec_client=FakeEdhrec(exc=EDHRecRequestError("down")))
    assert res.edhrec.status == "ok" and res.edhrec.stale and res.edhrec.inclusion == 0.9


def test_malformed_edhrec_response_degrades():
    res = run(edhrec_client=FakeEdhrec(page={"nope": 1}))
    assert res.edhrec.status == "unavailable"


@pytest.mark.parametrize("exc", [HFError("HTTP 429"), HFError("loading")])
def test_hf_failure_falls_back_to_heuristics(exc):
    res = run(classifier=FakeHF(exc=exc))
    assert res.model.status == "unavailable" and res.model.error
    assert res.roles[0].role == "ramp" and res.roles[0].source == "heuristic"
    assert res.strength > 0


def test_hf_disabled():
    res = run(classifier=HFClassifier(token=None, env={}))
    assert res.model.status == "disabled" and res.roles


def test_physical_deck_flags_owned_and_bonus():
    deck = DeckContext(commanders=["Atraxa"], cards=["Sol Ring"], kind="physical")
    res = run(deck=deck, owned_lookup=lambda names: {"Rhystic Study"})
    owned = {c.name: c.owned for c in res.candidates}
    assert owned["Rhystic Study"] is True and owned["Cultivate"] is False
    assert run().candidates[0].owned is None  # digital: not annotated


def test_weights_are_configurable():
    hi = ScoringWeights(inclusion=1.0, synergy=0, role_confidence=0, role_need=0)
    assert run(weights=hi).strength == 100.0  # 0.9 inclusion >= inclusion_full
    s, br = compute_strength(None, None, None, None, ScoringWeights())
    assert s == 50.0 and br.inclusion is None
    assert ScoringWeights.from_env({"CARD_EVAL_WEIGHTS": '{"inclusion": 0.9, "bogus": 1}'}).inclusion == 0.9
    assert ScoringWeights.from_env({"CARD_EVAL_WEIGHTS": "not json"}) == ScoringWeights()


def test_parse_page_keeps_slots():
    page = edhrec_data.parse_page(PAGE)
    assert page["cards"]["cultivate"]["tags"] == ["highsynergycards", "manaartifacts"]


# ---- mainboard slot gate --------------------------------------------------

def test_pool_candidates_all_carry_a_mainboard_slot_gate_reason():
    res = run()
    assert res.candidates
    for c in res.candidates:
        assert c.slot_gate is not None and c.slot_gate.slot == "mainboard" and c.slot_gate.legal is True
        assert isinstance(c.slot_gate.reason, str) and c.slot_gate.reason


def test_variant_cards_never_enter_the_mainboard_pool():
    # Missy-style planar deck: Plane/Phenomenon/Attraction cards on the
    # commander's EDHREC page must never be suggested for a mainboard slot,
    # even when Commander-legal and inside the colour identity.
    cards = {**CARDS,
             "the matrix of time": {"name": "The Matrix of Time", "color_identity": "", "legal": True,
                                    "type_line": "Phenomenon", "oracle_text": "x"},
             "balloon stand": {"name": "Balloon Stand", "color_identity": "W", "legal": True,
                               "type_line": "Artifact — Attraction", "oracle_text": "x"}}
    page = {"container": {"json_dict": {"card": {"num_decks": 1000}, "cardlists": [
        {"tag": "topcards", "cardviews": [view("The Matrix of Time", 0.95, 0.9), view("Balloon Stand", 0.9, 0.9),
                                          view("Rhystic Study", 0.3, 0.1), view("Cultivate", 0.5, 0.3)]}]}}}
    res = evaluate_card(FakeRedis(), "Sol Ring", DECK,
                        card_lookup=lambda names: {k: v for k, v in cards.items()
                                                   if k in {n.lower() for n in names}},
                        edhrec_client=FakeEdhrec(page), classifier=FakeHF({"ramp": 0.9}))
    names = {c.name for c in res.candidates}
    assert "The Matrix of Time" not in names and "Balloon Stand" not in names
    assert names  # ordinary mainboard-legal cards still flow through


def test_card_source_record_exposes_mainboard_gate_alongside_commander_legality():
    from analytics.card_eval.card_source import _record, mainboard_gate, variant_type_class
    plane = _record({"name": "The Abyssal Plane", "type_line": "Plane — The Abyss",
                     "color_identity": [], "legalities": {"commander": "legal"}})
    assert plane["legal"] is True  # Commander-legal is NOT mainboard-legal
    assert plane["type_class"] == "plane"
    assert plane["mainboard_gate"]["legal"] is False
    assert plane["mainboard_gate"]["reason"] == \
        "plane not legal in mainboard slot (belongs in planar_deck)"
    # Attraction is a subtype: the gate must still catch it.
    attraction = _record({"name": "Balloon Stand", "type_line": "Artifact — Attraction",
                          "color_identity": ["R"], "legalities": {"commander": "legal"}})
    assert attraction["type_class"] == "attraction"
    assert attraction["mainboard_gate"]["legal"] is False
    ordinary = _record({"name": "Sol Ring", "type_line": "Artifact",
                        "color_identity": [], "legalities": {"commander": "legal"}})
    assert ordinary["type_class"] is None
    assert ordinary["mainboard_gate"]["legal"] is True and ordinary["mainboard_gate"]["reason"]
    # Legacy cached record without the new fields: re-derived from type_line.
    gate = mainboard_gate({"name": "Chaos Plane", "type_line": "Plane — Segovia", "legal": True})
    assert gate["legal"] is False and gate["type_class"] == "plane"
    assert mainboard_gate({"name": "X", "legal": None})["legal"] is False  # unknown fails closed
    assert variant_type_class("Legendary Creature — Human Rogue") is None


# ---- HF client ----------------------------------------------------------

class Resp:
    def __init__(self, status, data=None, headers=None):
        self.status_code, self._d, self.headers = status, data, headers or {}

    def json(self):
        if isinstance(self._d, Exception):
            raise self._d
        return self._d


class Sess:
    def __init__(self, *resps):
        self.resps, self.calls = list(resps), []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        return self.resps.pop(0)


def hf(sess):
    return HFClassifier(token="tok", mode="api", model="a/b", session=sess, sleep_fn=lambda s: None)


def test_hf_parses_both_shapes_and_sends_auth():
    labels = list(ROLE_LABELS.values())
    sess = Sess(Resp(200, [{"label": labels[0], "score": 0.8}, {"label": "bogus", "score": 1}]))
    assert hf(sess).classify("text") == {"ramp": 0.8}
    url, kw = sess.calls[0]
    assert url.endswith("/models/a/b") and kw["headers"]["Authorization"] == "Bearer tok"
    assert hf(Sess(Resp(200, {"labels": labels[:2], "scores": [0.1, 0.7]}))).classify("t")["card_advantage"] == 0.7


def test_hf_retries_rate_limit_then_succeeds_or_fails():
    labels = list(ROLE_LABELS.values())
    ok = Resp(200, [{"label": labels[0], "score": 0.5}])
    assert hf(Sess(Resp(429, headers={"Retry-After": "1"}), Resp(503), ok)).classify("t") == {"ramp": 0.5}
    with pytest.raises(HFError, match="after 3 attempts"):
        hf(Sess(Resp(429), Resp(429), Resp(429))).classify("t")


@pytest.mark.parametrize("resp", [Resp(401), Resp(400), Resp(200, ValueError("bad")), Resp(200, {"x": 1})])
def test_hf_hard_failures(resp):
    with pytest.raises(HFError):
        hf(Sess(resp)).classify("t")


def test_hf_env_config():
    c = HFClassifier(env={"HF_TOKEN": "t", "CARD_EVAL_HF_MODEL": "org/model", "CARD_EVAL_HF_URL": "http://x/{model}"})
    assert c.enabled and c.url == "http://x/org/model"
    assert not HFClassifier(env={}).enabled
    assert not HFClassifier(env={"HF_TOKEN": "t", "CARD_EVAL_HF_MODE": "off"}).enabled
