"""Single Card Review: debate, judge, replacements and endpoint. The LLM is a scripted fake; nothing hits the network."""
import json
import os
import sys
import time
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.card_eval import Candidate, EvaluationResult
from analytics.card_eval.schema import CardInfo, EdhrecStats, ModelSignal, RoleScore, StrengthBreakdown
from analytics.card_review import ReviewContext, ReviewNotAllowed, run_review
from analytics.card_review import jobs
from analytics.card_review.debate import extract_json, final_confidence
from analytics.card_review.packet import build_packet, packet_numbers
from analytics.card_review.replacements import suggest_replacements


# ---- fixtures -------------------------------------------------------------------------------------

def make_ev(strength=34.0, card="Blasphemous Act", ci="R", legal=True, roles=("board_wipe",), edhrec="ok", cands=None,
            role_need=0.2):
    return EvaluationResult(
        card=CardInfo(name=card, oracle_id="oid-card", type_line="Sorcery", oracle_text="Deals 13 damage to each creature.",
                      color_identity=ci, legal=legal),
        deck_kind="digital", commanders=["Krenko, Mob Boss"],
        roles=[RoleScore(role=r, confidence=0.7, source="heuristic") for r in roles], strength=strength,
        strength_breakdown=StrengthBreakdown(inclusion=0.62, synergy=0.4, role_confidence=None, role_need=role_need),
        edhrec=EdhrecStats(status=edhrec, commander_slug="krenko-mob-boss", inclusion=0.375 if edhrec == "ok" else None,
                           synergy=-0.1 if edhrec == "ok" else None, num_decks=16733 if edhrec == "ok" else None,
                           decks_sampled=44579 if edhrec == "ok" else None),
        model=ModelSignal(status="disabled"), candidates=cands if cands is not None else default_cands(), warnings=[])


def cand(name, roles, ci="R", inc=0.3, syn=0.2, owned=None, same=None):
    return Candidate(name=name, roles=list(roles), same_role=same if same is not None else False, inclusion=inc,
                     synergy=syn, score=0.5, color_identity=ci, owned=owned)


def default_cands():
    return [cand("Anger of the Gods", ["board_wipe"], same=True), cand("Chaos Warp", ["targeted_removal"]),
            cand("Gempalm Incinerator", ["targeted_removal"]), cand("Goblin Cratermaker", ["targeted_removal"]),
            cand("Sol Ring", ["ramp"], ci=""), cand("Cultivate", ["ramp"], ci="G"),       # off-identity
            cand("Banned Card", ["targeted_removal"]), cand("Already Here", ["targeted_removal"]),
            cand("Renamed Copy", ["targeted_removal"])]                                 # same oracle id as a deck card


FACTS = {
    "anger of the gods": {"oracle_id": "o1", "color_identity": "R", "legal": True},
    "chaos warp": {"oracle_id": "o2", "color_identity": "R", "legal": True},
    "gempalm incinerator": {"oracle_id": "o3", "color_identity": "R", "legal": True},
    "goblin cratermaker": {"oracle_id": "o4", "color_identity": "R", "legal": True},
    "sol ring": {"oracle_id": "o5", "color_identity": "", "legal": True},
    "cultivate": {"oracle_id": "o6", "color_identity": "G", "legal": True},
    "banned card": {"oracle_id": "o7", "color_identity": "R", "legal": False},
    "already here": {"oracle_id": "o8", "color_identity": "R", "legal": True},
    "renamed copy": {"oracle_id": "o9", "color_identity": "R", "legal": True},
    "deck filler": {"oracle_id": "o9", "color_identity": "R", "legal": True},  # deck card sharing Renamed Copy's id
}
DECK = ["Already Here", "Deck Filler", "Blasphemous Act"]


def lookup(names):
    return {k: v for k, v in FACTS.items() if k in {n.lower() for n in names}}


def ctx(**kw):
    base = dict(deck_name="Krenko", deck_kind="digital", commander_identity={"R"}, thesis="Goblin swarm",
                pillars=[{"name": "Goblin swarm", "description": "go wide"}], journal_confirmed=True)
    base.update(kw)
    return ReviewContext(**base)


class FakeLLM:
    """Scripted by role: a dict of role -> reply (str) or list of replies (consumed in order, last repeats)."""

    def __init__(self, **script):
        self.script, self.calls = script, []

    def complete(self, role, system, user, *, temperature=0.0):
        self.calls.append({"role": role, "system": system, "user": user, "temperature": temperature})
        s = self.script[role]
        if isinstance(s, list):
            idx = sum(1 for c in self.calls if c["role"] == role) - 1
            s = s[min(idx, len(s) - 1)]
        return s(user) if callable(s) else s


def turn(side, claims, rebuttals=(), concede=False):
    return json.dumps({"side": side, "round": 1, "claims": [{"tier": "x", "text": t, "evidence": e} for t, e in claims],
                       "rebuttals": [{"target": tg, "text": t, "evidence": e} for tg, t, e in rebuttals],
                       "concede": concede})


def verdict(v="remove", jc=0.9, reasons=None, brief=None, **kw):
    d = {"verdict": v, "judge_confidence": jc,
         "key_reasons": reasons or ["It fights the goblin pillar. [J2, S1]", "Strength is low. [S1]"],
         "deciding_tier": "spirit", "winning_claims": ["RM-1.1"], "discarded": [],
         "replacement_brief": brief if brief is not None else (
             {"mode": "like_for_like", "target_roles": ["board_wipe"], "avoid": []} if v == "remove" else None)}
    d.update(kw)
    return json.dumps(d)


REMOVE_T = turn("remove", [("Cuts against the pillar.", ["J2"])])
KEEP_T = turn("keep", [("It is a staple.", ["E1"])])


def run(ev=None, c=None, llm=None, **kw):
    return run_review(ev or make_ev(), c or ctx(), DECK, llm=llm or FakeLLM(remove=REMOVE_T, keep=KEEP_T, judge=verdict()),
                      fact_lookup=lookup, **kw)


# ---- gates ----------------------------------------------------------------------------------------

def test_pet_card_is_kept_without_any_llm_call():
    llm = FakeLLM()
    res = run(c=ctx(pet_cards=["blasphemous act"]), llm=llm)
    assert (res["verdict"], res["confidence"], res["gate"]) == ("keep", 1.0, "pet_card")
    assert llm.calls == [] and res["replacements"] == []


@pytest.mark.parametrize("kw", [dict(legal=False), dict(ci="G")])
def test_banned_or_off_identity_is_removed_at_099_with_no_debate(kw):
    llm = FakeLLM()
    res = run(ev=make_ev(**kw), llm=llm)
    assert (res["verdict"], res["confidence"], res["gate"]) == ("remove", 0.99, "illegal")
    assert llm.calls == [] and res["replacement_brief"]["mode"] == "like_for_like"
    assert 3 <= len(res["replacements"]) <= 5


def test_commander_cannot_be_reviewed():
    with pytest.raises(ReviewNotAllowed):
        run(ev=make_ev(card="Krenko, Mob Boss"))


# ---- verdict paths --------------------------------------------------------------------------------

def test_remove_path_returns_verdict_transcript_and_replacements():
    res = run()
    assert res["verdict"] == "remove" and 0.5 <= res["confidence"] <= 1
    assert len(res["key_reasons"]) >= 2 and not res["fallback"]
    assert [(t["side"], t["round"]) for t in res["transcript"]] == [("remove", 1), ("keep", 1), ("remove", 2), ("keep", 2)]
    assert 3 <= len(res["replacements"]) <= 5
    for s in res["replacements"]:
        assert {"card", "role", "like_for_like", "rationale"} <= set(s) and len(s["rationale"]) <= 200
    # 0.5*(1-prior) + 0.5*0.9 for strength 34
    assert res["confidence"] == final_confidence("remove", 34.0, 0.9, edhrec_missing=False, journal_unconfirmed=False,
                                                 fallback=False)


def test_keep_path_has_no_replacements():
    llm = FakeLLM(remove=REMOVE_T, keep=KEEP_T, judge=verdict("keep", 0.8, reasons=["Fine slot. [S1]", "Popular. [E1]"]))
    res = run(ev=make_ev(strength=62.5), llm=llm)
    assert res["verdict"] == "keep" and res["replacements"] == [] and res["replacement_brief"] is None
    assert res["confidence"] == final_confidence("keep", 62.5, 0.8, edhrec_missing=False, journal_unconfirmed=False,
                                                 fallback=False)


def test_rounds_are_bounded_and_round_one_is_blind():
    llm = FakeLLM(remove=REMOVE_T, keep=KEEP_T, judge=verdict())
    assert len(run(llm=llm, rounds=3)["transcript"]) == 6
    assert len(run(llm=FakeLLM(remove=REMOVE_T, keep=KEEP_T, judge=verdict()), rounds=99)["transcript"]) == 6
    assert len(run(llm=FakeLLM(remove=REMOVE_T, keep=KEEP_T, judge=verdict()), rounds=0)["transcript"]) == 4
    r1 = [json.loads(c["user"]) for c in llm.calls if c["role"] in ("remove", "keep")][:2]
    assert all(u["transcript"] == [] for u in r1)
    assert llm.calls[0]["temperature"] == 0.3 and llm.calls[-1]["temperature"] == 0


def test_concession_stops_the_debate_and_decides_for_the_other_side():
    llm = FakeLLM(remove=turn("remove", [], concede=True), keep=KEEP_T, judge=verdict("remove"))
    res = run(llm=llm)
    assert len(res["transcript"]) == 2
    assert res["verdict"] == "keep" and res["deciding_tier"] == "concession" and res["replacements"] == []


def test_uncited_or_invented_number_claims_are_discarded_and_rebuttals_need_a_real_target():
    bad = turn("remove", [("No evidence at all.", []), ("Played in 99.9% of decks.", ["E1"]),
                          ("Real claim.", ["J2"])])
    reb = lambda user: turn("keep", [("x", ["E1"])], rebuttals=[("RM-9.9", "bad target", ["S1"]),
                                                                  ("RM-1.1", "good", ["S1"])])
    llm = FakeLLM(remove=bad, keep=[KEEP_T, reb], judge=verdict())
    res = run(llm=llm)
    r1 = res["transcript"][0]
    assert [c["id"] for c in r1["claims"]] == ["RM-1.1"] and r1["claims"][0]["tier"] == "spirit"
    assert {d["why"].split(":")[0] for d in r1["discarded"]} == {"cites no valid packet ID", "number(s) not in packet"}
    k2 = res["transcript"][3]
    assert [x["target"] for x in k2["rebuttals"]] == ["RM-1.1"] and len(k2["discarded"]) == 1


# ---- judge validation and confidence --------------------------------------------------------------

def test_invalid_judge_json_twice_falls_back_with_capped_confidence():
    llm = FakeLLM(remove=REMOVE_T, keep=KEEP_T, judge="not json at all")
    res = run(llm=llm)
    assert res["fallback"] is True and res["confidence"] <= 0.60
    assert sum(1 for c in llm.calls if c["role"] == "judge") == 2
    assert "Return valid JSON only" in llm.calls[-1]["user"]
    assert res["verdict"] == "remove"  # strength 34 leans remove


def test_judge_retry_recovers_when_second_reply_is_valid():
    res = run(llm=FakeLLM(remove=REMOVE_T, keep=KEEP_T, judge=["oops", verdict()]))
    assert res["fallback"] is False


def test_key_reason_with_number_not_in_packet_is_stripped():
    reasons = ["Fights the pillar. [J2]", "Costs 7 mana. [C1]", "Strength is 34.0. [S1]"]
    res = run(llm=FakeLLM(remove=REMOVE_T, keep=KEEP_T, judge=verdict(reasons=reasons)))
    assert res["key_reasons"] == [reasons[0], reasons[2]] and not res["fallback"]


def test_too_few_valid_reasons_means_fallback():
    res = run(llm=FakeLLM(remove=REMOVE_T, keep=KEEP_T, judge=verdict(reasons=["Costs 7. [C1]", "No ids here"])))
    assert res["fallback"] is True


def test_unconfirmed_journal_marks_remove_provisional_and_caps_confidence():
    res = run(c=ctx(journal_confirmed=False, thesis="", pillars=[]))
    assert res["verdict"] == "remove" and res["provisional"] is True and res["confidence"] <= 0.65
    assert "journal_unconfirmed" in res["flags"] and len(res["replacements"]) >= 3


def test_missing_edhrec_data_caps_confidence_and_sets_flag():
    res = run(ev=make_ev(edhrec="card_not_listed"))
    assert res["confidence"] <= 0.70 and "edhrec_data_missing" in res["flags"]


def test_packet_numbers_are_the_only_ones_advocates_may_use():
    p = build_packet(make_ev(), ctx())
    assert "37.5" in packet_numbers(p) and "16733" in packet_numbers(p) and "7" not in packet_numbers(p)
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1} and extract_json("nope") is None


# ---- replacements: constraints --------------------------------------------------------------------

def test_hard_filters_exclude_off_identity_banned_and_in_deck_cards():
    ev = make_ev()
    res = suggest_replacements(ev, {"mode": "mixed", "target_roles": ["board_wipe", "targeted_removal"], "avoid": []},
                               ctx(), DECK, lookup)
    names = {s["card"] for s in res["suggestions"]}
    assert not names & {"Cultivate", "Banned Card", "Already Here", "Renamed Copy", "Blasphemous Act"}
    reasons = {d["card"]: d["reason"] for d in res["dropped"]}
    assert "colour identity" in reasons["Cultivate"] and "legal" in reasons["Banned Card"]
    assert "in the deck" in reasons["Already Here"] and "in the deck" in reasons["Renamed Copy"]  # by oracle_id
    assert all(fid in {"o1", "o2", "o3", "o4", "o5"} for fid in (s["oracle_id"] for s in res["suggestions"]))


def test_unknown_legality_candidate_is_never_suggested():
    # Regression: a candidate whose fact record has unknown legality (missing
    # or None `legal`, e.g. an unsynced planar card) must fail closed.
    cands = default_cands() + [cand("The Matrix Of Time", ["board_wipe"], syn=0.9)]
    facts = {**FACTS, "the matrix of time": {"oracle_id": "o-plane", "color_identity": "",
                                             "legal": None}}
    res = suggest_replacements(make_ev(cands=cands), {"mode": "mixed", "target_roles": ["board_wipe"], "avoid": []},
                               ctx(), DECK, lambda ns: {k: v for k, v in facts.items() if k in {n.lower() for n in ns}})
    assert "The Matrix Of Time" not in {s["card"] for s in res["suggestions"]}
    assert any(d["card"] == "The Matrix Of Time" and "legality unknown" in d["reason"]
               for d in res["dropped"])


def test_spirit_gate_drops_candidates_with_no_role_or_pillar_fit():
    cands = default_cands() + [cand("Random Rock", ["utility_land"], syn=0.0)]
    facts = {**FACTS, "random rock": {"oracle_id": "o10", "color_identity": "", "legal": True}}
    res = suggest_replacements(make_ev(cands=cands), {"mode": "like_for_like", "target_roles": ["board_wipe"], "avoid": []},
                               ctx(), DECK, lambda ns: {k: v for k, v in facts.items() if k in {n.lower() for n in ns}})
    assert "Random Rock" not in {s["card"] for s in res["suggestions"]}
    assert any(d["card"] == "Random Rock" and "spirit gate" in d["reason"] for d in res["dropped"])


def test_mixed_mode_has_both_like_for_like_and_function_change():
    res = suggest_replacements(make_ev(cands=[cand(f"W{i}", ["board_wipe"], inc=0.4 - i / 100) for i in range(4)]
                                       + [cand(f"R{i}", ["targeted_removal"], inc=0.3 - i / 100) for i in range(4)]),
                               {"mode": "mixed", "target_roles": ["board_wipe", "targeted_removal"], "avoid": []}, ctx(), DECK,
                               lambda ns: {n.lower(): {"oracle_id": n, "color_identity": "R", "legal": True} for n in ns})
    flags = [s["like_for_like"] for s in res["suggestions"]]
    assert True in flags and False in flags and len(flags) == 5 and flags.count(True) == 3


def test_like_for_like_mode_returns_only_same_primary_role():
    res = suggest_replacements(make_ev(cands=[cand(f"W{i}", ["board_wipe"]) for i in range(5)] + [cand("T", ["targeted_removal"])]),
                               {"mode": "like_for_like", "target_roles": ["board_wipe"], "avoid": []}, ctx(), DECK,
                               lambda ns: {n.lower(): {"oracle_id": n, "color_identity": "R", "legal": True} for n in ns})
    assert len(res["suggestions"]) == 5 and all(s["like_for_like"] for s in res["suggestions"])


def test_short_pool_is_reported_not_padded():
    res = suggest_replacements(make_ev(cands=[cand("Only One", ["board_wipe"])]),
                               {"mode": "like_for_like", "target_roles": ["board_wipe"], "avoid": []}, ctx(), DECK,
                               lambda ns: {n.lower(): {"oracle_id": n, "color_identity": "R", "legal": True} for n in ns})
    assert len(res["suggestions"]) == 1 and res["pool_exhausted"] is True


def test_function_change_is_raised_to_mixed_when_the_deck_needs_the_role():
    res = suggest_replacements(make_ev(role_need=0.9), {"mode": "function_change", "target_roles": ["targeted_removal"], "avoid": []},
                               ctx(), DECK, lookup)
    assert res["mode"] == "mixed" and any(s["like_for_like"] for s in res["suggestions"])


def test_game_changer_penalty_is_a_flag_not_a_filter():
    gc = lambda name: [(25.0, "adds-gc (0->1)")] if name == "Chaos Warp" else []
    plain = suggest_replacements(make_ev(), {"mode": "function_change", "target_roles": ["targeted_removal"], "avoid": []},
                                 ctx(), DECK, lookup)
    pen = suggest_replacements(make_ev(), {"mode": "function_change", "target_roles": ["targeted_removal"], "avoid": []},
                               ctx(), DECK, lookup, annotate=gc)
    base = next(s for s in plain["suggestions"] if s["card"] == "Chaos Warp")
    flagged = next(s for s in pen["suggestions"] if s["card"] == "Chaos Warp")
    assert flagged["flags"] == ["adds-gc (0->1)"] and base["score"] - flagged["score"] == pytest.approx(25.0, abs=0.2)
    assert "adds-gc (0->1)" in flagged["rationale"]



def test_card_source_tiers_applied():
    from analytics.card_review.replacements import suggest_replacements
    from analytics.card_review import config as cfg
    cfg.VERY_STRONG_THRESHOLD = 0.0
    cands = [cand("BuyMe", ["board_wipe"], inc=0.9, syn=0.5), cand("DonorMe", ["board_wipe"], inc=0.8, syn=0.4), cand("FreeMe", ["board_wipe"], inc=0.7, syn=0.3, owned=True)]
    
    def mock_source_lookup(name):
        if name == "FreeMe":
            return {"available_copies": 1, "price": 0.5, "budget_cap": 5.0, "donor_decks": []}
        elif name == "BuyMe":
            return {"available_copies": 0, "price": 1.0, "budget_cap": 5.0, "donor_decks": []}
        elif name == "DonorMe":
            return {"available_copies": 0, "price": 10.0, "budget_cap": 5.0, "donor_decks": [{"name": "Deck B", "donor_score": 50.0}]}
        return {}
        
    res = suggest_replacements(make_ev(cands=cands), {"mode": "like_for_like", "target_roles": ["board_wipe"], "avoid": []},
                               ctx(), DECK, lambda ns: {n.lower(): {"oracle_id": n, "color_identity": "R", "legal": True} for n in ns},
                               source_lookup=mock_source_lookup)
                               
    tiers = [s["tier"] for s in res["suggestions"]]
    assert "owned-and-unused" in tiers
    assert "buy-under-budget" in tiers
    assert "donor-deck" in tiers

def test_owned_card_within_margin_is_listed_first_and_unknown_identity_is_flagged():
    cands = [cand("Pricey", ["board_wipe"], inc=0.6, syn=0.4), cand("Binder Card", ["board_wipe"], inc=0.5, syn=0.35, owned=True),
             cand("Third", ["board_wipe"], inc=0.1, syn=0.0), cand("Fourth", ["board_wipe"], inc=0.1, syn=0.0)]
    res = suggest_replacements(make_ev(cands=cands), {"mode": "like_for_like", "target_roles": ["board_wipe"], "avoid": []},
                               ctx(commander_identity=None), DECK,
                               lambda ns: {n.lower(): {"oracle_id": n, "color_identity": "R", "legal": True} for n in ns})
    assert res["suggestions"][0]["card"] == "Binder Card" and res["suggestions"][0]["tier"] == "owned-and-unused"
    assert any("colour identity unknown" in f for f in res["flags"])


# ---- progress events and jobs ---------------------------------------------------------------------

def test_progress_events_stream_in_order():
    events = []
    run(on_event=events.append)
    stages = [e["stage"] for e in events]
    assert stages[0] == "evaluated" and stages[-2:] == ["judging", "replacements"]
    assert stages.count("turn") == 4 and stages.count("round_start") == 2


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, *a, **kw):
        self.store[k] = v


def test_job_records_progress_and_result_then_releases_the_slot():
    r = FakeRedis()
    job = jobs.new_job(r, "archidekt:1", "Blasphemous Act", 2)
    with pytest.raises(jobs.ReviewBusy):
        jobs.new_job(r, "archidekt:1", "blasphemous act", 2)
    out = jobs.run_job(r, job, lambda emit: run(on_event=emit))
    saved = jobs.load_job(r, job["job_id"])
    assert saved["status"] == "done" and saved["result"]["verdict"] == "remove" and len(saved["transcript"]) == 4
    assert out["stage"] == "done"
    jobs.new_job(r, "archidekt:1", "Blasphemous Act", 2)  # slot was released
    jobs.release("archidekt:1|blasphemous act")


def test_job_failure_is_recorded():
    r = FakeRedis()
    job = jobs.new_job(r, "archidekt:2", "X", 2)

    def boom(emit):
        raise ReviewNotAllowed("the commander can't be reviewed")

    saved = jobs.run_job(r, job, boom)
    assert saved["status"] == "failed" and saved["error_kind"] == "not_allowed"


# ---- endpoint -------------------------------------------------------------------------------------

DECK_REC = {"id": 1, "source": "archidekt", "source_id": 1, "name": "Krenko", "commanders": ["Krenko, Mob Boss"],
            "cards": [{"name": n, "quantity": 1} for n in DECK]}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app import app
    r = FakeRedis()
    llm = FakeLLM(remove=REMOVE_T, keep=KEEP_T, judge=verdict())
    with patch("app.r", r), patch("app.registry.find_deck", side_effect=lambda _r, i: DECK_REC if i in ("archidekt:1", "1") else None), \
            patch("app._oracle_preflight", return_value=None), patch("app._card_review_llm", return_value=llm), \
            patch("analytics.card_review.service.build_context", return_value=ctx()), \
            patch("analytics.card_review.service.evaluate_card", return_value=make_ev()), \
            patch("analytics.card_review.service.fetch_cards", side_effect=lambda _r, ns: lookup(ns)):
        yield TestClient(app), llm


def wait_done(c, url, tries=100):
    for _ in range(tries):
        job = c.get(url).json()["job"]
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_endpoint_starts_job_and_poll_returns_full_result(client):
    c, llm = client
    resp = c.post("/api/decks/archidekt:1/cards/Blasphemous%20Act/review", json={"rounds": 3})
    assert resp.status_code == 202 and resp.json()["rounds"] == 3
    job = wait_done(c, resp.json()["status_url"])
    assert job["status"] == "done", job
    res = job["result"]
    assert res["verdict"] == "remove" and len(res["transcript"]) == 6 and 3 <= len(res["replacements"]) <= 5
    assert {"verdict", "confidence", "key_reasons", "transcript"} <= set(res)


def test_endpoint_validation(client):
    c, _ = client
    assert c.post("/api/decks/nope/cards/X/review").status_code == 404
    assert c.post("/api/decks/archidekt:1/cards/Not%20In%20Deck/review").status_code == 404
    assert c.post("/api/decks/archidekt:1/cards/Krenko, Mob Boss/review").status_code == 400
    assert c.post("/api/decks/archidekt:1/cards/Blasphemous%20Act/review", json={"rounds": 9}).status_code == 422
    assert c.get("/api/decks/archidekt:1/cards/Blasphemous%20Act/review/jobs/missing").status_code == 404
