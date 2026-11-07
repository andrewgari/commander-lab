"""Brew output gate (brew_gate.release_draft): every check fails -> repaired,
unrecoverable -> BrewGateError (never a bad deck), sources down -> still full,
and every Brew output path goes through the one gate."""
import ast
import inspect
import json
import os
import sys
import uuid
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.server.domain.draft.brew as brew
import src.server.domain.draft.brew_gate as bg
import src.server.domain.draft.deck_validator as dv
from src.server.domain.draft.brew_gate import BrewGateError, release_draft
from tests.test_brew import FakeRedis

CMD = "Rakdos Guy"


def card(name, ci="", types=("Artifact",), supers=(), text="", legality="legal", subtypes=()):
    return dv.CardInfo(name=name, color_identity=dv.parse_color_identity(ci), types=tuple(types),
                       supertypes=tuple(supers), subtypes=tuple(subtypes), oracle_text=text,
                       commander_legality=legality)


def build_catalog():
    cards = [
        card(CMD, "BR", ("Creature",), ("Legendary",)),
        card("Tymna the Weaver", "WB", ("Creature",), ("Legendary",), "Partner"),
        card("Thrasios, Triton Hero", "UG", ("Creature",), ("Legendary",), "Partner"),
        card("Vanilla Bear", "G", ("Creature",), ("Legendary",)),
        card("Not Legendary", "B", ("Creature",)),
        card("Sol Ring"), card("Arcane Signet"), card("Mind Stone"),
        card("Lightning Bolt", "R", ("Instant",)),
        card("Doom Blade", "B", ("Instant",)),
        card("Counterspell", "U", ("Instant",)),
        card("Mana Crypt", "", ("Artifact",), legality="banned"),
        card("Relentless Rats", "B", ("Creature",),
             text="A deck can have any number of cards named Relentless Rats."),
    ]
    for ci in ("", "B", "R", "W", "U", "G"):
        for i in range(40):
            cards.append(card(f"Src {ci or 'C'} {i}", ci, ("Creature",)))
    return dv.CardCatalog(cards)


CAT = build_catalog()
GOOD = [f"Src B {i}" for i in range(40)] + [f"Src R {i}" for i in range(40)] + \
       [f"Src C {i}" for i in range(19)]  # 99 cards


def draft(names=GOOD, commanders=(CMD,), extra=None, **kw):
    cards = [{"name": c, "quantity": 1, "tier": 3, "tier_label": "new", "cost": 0.0} for c in commanders]
    cards += [{"name": n, "quantity": 1, "tier": 3, "tier_label": "new", "cost": 0.0} for n in names]
    cards += extra or []
    return {"id": "draft_0", "name": "t", "commanders": list(commanders), "cards": cards, **kw}


def srcs(*lists):
    return [(f"s{i}", list(l)) for i, l in enumerate(lists)]


def total(d):
    return sum(c["quantity"] for c in d["cards"])


def counts(d):
    out = {}
    for c in d["cards"]:
        out[c["name"]] = out.get(c["name"], 0) + c["quantity"]
    return out


def gate(d, sources=None, **kw):
    return release_draft(d, None, catalog=CAT, sources=sources if sources is not None else [], **kw)


def assert_legal(d, commanders=(CMD,)):
    assert total(d) == 100
    res = bg.check_draft(d, CAT)
    assert res.valid, res.to_json()
    assert not bg.find_uuid_strings(d)


# -- legal deck passes untouched ---------------------------------------------------

def test_legal_deck_passes_unchanged_and_is_stamped():
    d = draft()
    assert total(d) == 100
    out = gate(d)
    assert [c["name"] for c in out["cards"]] == [c["name"] for c in d["cards"]]
    assert out["gate"]["ok"] and "gate_repaired" not in out


def test_stamp_invalidated_by_later_change():
    out = gate(draft())
    out["cards"].pop()
    out["cards"][-1]["name"] = "Mana Crypt"
    out2 = gate(out)  # stale stamp must not short-circuit; deck is repaired
    assert counts(out2).get("Mana Crypt") is None
    assert_legal(out2)


# -- each check fails and is repaired -------------------------------------------------

def test_short_deck_is_filled_to_100():
    out = gate(draft(GOOD[:40]), srcs([f"Src B {i}" for i in range(40, 40)] + [f"Src R {i}" for i in range(40)]))
    assert_legal(out)
    assert out["missing_slots"] == 0 and out["gate_repaired"]


def test_oversize_deck_is_trimmed_to_100():
    out = gate(draft(GOOD, extra=[{"name": "Src C 30", "quantity": 1}, {"name": "Src C 31", "quantity": 1}]))
    assert_legal(out)


def test_uuid_name_is_dropped_and_refilled():
    bad = str(uuid.uuid4())
    out = gate(draft(GOOD[:98] + [bad]))
    assert_legal(out)
    assert bad not in json.dumps(out)


def test_unresolved_blank_name_is_dropped_and_refilled():
    d = draft(GOOD[:98], extra=[{"name": "   ", "quantity": 1}, {"name": None, "quantity": 1}])
    assert bg.check_draft(d, CAT).codes()  # check_draft flags it
    assert_legal(gate(d))


def test_unknown_card_is_replaced():
    out = gate(draft(GOOD[:98] + ["Totally Invented Card"]))
    assert_legal(out)
    assert "Totally Invented Card" not in counts(out)


def test_singleton_violation_is_repaired():
    out = gate(draft(GOOD[:98], extra=[{"name": "Src B 0", "quantity": 1}]))
    assert_legal(out)
    assert counts(out)["Src B 0"] == 1


def test_singleton_exceptions_survive_the_gate():
    names = GOOD[:90]
    d = draft(names, extra=[{"name": "Relentless Rats", "quantity": 9}])
    out = gate(d)
    assert_legal(out)
    assert counts(out)["Relentless Rats"] == 9
    assert counts(out)["Mind Stone" if "Mind Stone" in counts(out) else "Src B 0"] >= 1


def test_basic_lands_may_repeat():
    d = draft(GOOD[:60], extra=[{"name": "Swamp", "quantity": 20}, {"name": "Mountain", "quantity": 19}])
    out = gate(d)
    assert_legal(out)
    assert counts(out)["Swamp"] == 20 and counts(out)["Mountain"] == 19


def test_off_color_identity_card_is_removed():
    out = gate(draft(GOOD[:98] + ["Counterspell"]))
    assert_legal(out)
    assert "Counterspell" not in counts(out)


def test_banned_card_is_removed():
    out = gate(draft(GOOD[:98] + ["Mana Crypt"]))
    assert_legal(out)
    assert "Mana Crypt" not in counts(out)


def test_fill_uses_sources_then_basics_in_colour():
    out = gate(draft(GOOD[:50]), srcs(["Counterspell", "Mana Crypt", "Doom Blade", "Lightning Bolt"]))
    assert_legal(out)
    n = counts(out)
    assert n["Doom Blade"] == 1 and n["Lightning Bolt"] == 1
    assert not {"Counterspell", "Mana Crypt"} & set(n)
    assert {"Swamp", "Mountain"} <= set(n)  # basics split over B/R


def test_partner_pair_is_repaired_with_union_identity():
    cmds = ("Tymna the Weaver", "Thrasios, Triton Hero")
    d = draft([f"Src {c} {i}" for c in "WBUG" for i in range(10)], commanders=cmds)
    out = gate(d)
    assert total(out) == 100 and bg.check_draft(out, CAT).valid
    assert out["commanders"] == list(cmds)


# -- unrecoverable: explicit error, never a bad deck -----------------------------------------

@pytest.mark.parametrize("cmds", [
    ("Not Legendary",),                     # ineligible commander
    ("Mana Crypt",),                        # banned / not a creature
    ("Does Not Exist",),                    # unknown commander
    (CMD, "Vanilla Bear"),                  # partner pair that does not pair
    (),                                     # no commander
])
def test_unrecoverable_commander_raises(cmds):
    with pytest.raises(BrewGateError):
        gate(draft(GOOD[:40], commanders=cmds))


def test_unresolvable_commander_id_raises():
    with pytest.raises(BrewGateError):
        gate(draft(GOOD[:40], commanders=(str(uuid.uuid4()),)))


def test_repair_that_cannot_converge_raises_not_returns():
    with mock.patch.object(bg, "_repair", side_effect=lambda d, *a, **k: d):  # repair does nothing
        with pytest.raises(BrewGateError) as e:
            gate(draft(GOOD[:40]), max_attempts=2)
    assert e.value.attempts == 2 and e.value.result and not e.value.result.valid


def test_attempts_are_bounded():
    calls = []
    with mock.patch.object(bg, "_repair", side_effect=lambda d, *a, **k: calls.append(1) or d):
        with pytest.raises(BrewGateError):
            gate(draft(GOOD[:40]), max_attempts=3)
    assert len(calls) == 3


def test_validates_after_every_repair():
    seen = []
    real = bg.check_draft
    with mock.patch.object(bg, "check_draft", side_effect=lambda *a, **k: seen.append(1) or real(*a, **k)):
        gate(draft(GOOD[:40]))
    assert len(seen) == 2  # initial + one after repair


# -- EDHREC / analytics unavailable ---------------------------------------------------------

def boom():
    raise RuntimeError("EDHREC 503")


def slow():
    import time
    time.sleep(30)


@pytest.mark.parametrize("sources", [
    [("edhrec", boom), ("analytics", boom)],
    [("edhrec", lambda: None), ("analytics", lambda: [None, 5])],
    [],
])
def test_sources_unavailable_still_returns_full_legal_deck(sources):
    assert_legal(gate(draft(GOOD[:30]), sources))


def test_slow_source_is_skipped(monkeypatch):
    import src.server.domain.draft.deck_fill as df
    monkeypatch.setattr(df, "DEFAULT_SOURCE_TIMEOUT", 0.05)
    assert_legal(gate(draft(GOOD[:30]), [("edhrec", slow)]))


def test_default_sources_survive_redis_without_edhrec_cache():
    r = FakeRedis()
    d = draft(GOOD[:30])
    out = release_draft(d, r, catalog=CAT, session={"donor_decks": []})
    assert_legal(out)


def test_default_sources_survive_redis_raising():
    class Down(FakeRedis):
        def get(self, k):
            raise ConnectionError("redis down")
    out = release_draft(draft(GOOD[:30]), Down(), catalog=CAT, session={})
    assert_legal(out)


# -- user-edited drafts ----------------------------------------------------------------------

def test_user_edited_short_draft_is_not_auto_filled_but_checked():
    d = draft(GOOD[:30], user_edited=True)
    out = release_draft(d, None, catalog=CAT, sources=[])
    assert total(out) == 31  # left as the user made it
    with pytest.raises(BrewGateError):
        release_draft(draft(GOOD[:30] + ["Mana Crypt"], user_edited=True), None, catalog=CAT,
                      sources=[], repair=False)


def test_user_edited_stamp_does_not_satisfy_a_complete_requirement():
    out = release_draft(draft(GOOD[:30], user_edited=True), None, catalog=CAT, sources=[])
    full = release_draft(out, None, catalog=CAT, sources=[], allow_incomplete=False)
    assert_legal(full)


# -- one gate for every Brew output path ---------------------------------------------------------

GATE_NAMES = {"release_draft", "release_session", "get_drafts", "_release_and_persist"}


def _calls(fn):
    return {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
            for n in ast.walk(ast.parse(inspect.getsource(fn))) if isinstance(n, ast.Call)}


@pytest.mark.parametrize("fn", [brew.execute_build, brew.get_drafts, brew.list_sessions,
                                brew.release_session, brew.accept_draft, brew.add_draft_card,
                                brew.remove_draft_card])
def test_domain_output_functions_call_the_gate(fn):
    assert _calls(fn) & GATE_NAMES, f"{fn.__name__} does not route through the Brew gate"


def test_every_brew_route_returning_a_session_or_drafts_uses_the_gate():
    import src.server.app.main as m
    src_ = inspect.getsource(m)
    tree = ast.parse(src_)
    checked = 0
    for fn in tree.body:
        if not isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        routes = [d for d in fn.decorator_list if isinstance(d, ast.Call) and d.args
                  and isinstance(d.args[0], ast.Constant) and str(d.args[0].value).startswith("/api/brew")]
        if not routes:
            continue
        body = ast.get_source_segment(src_, fn)
        emits = "session" in body or "drafts" in body
        gated = any(k in body for k in ("release_session", "get_drafts", "brew.list_sessions"))
        if emits and not any(k in fn.name for k in ("start_brew", "pools", "availability", "commander_search")):
            checked += 1
            assert gated, f"{fn.name} returns Brew session/draft data without the gate"
    assert checked >= 5


def test_cached_bad_draft_is_never_returned_by_any_read_path():
    r = FakeRedis()
    bad = draft(GOOD[:20] + [str(uuid.uuid4()), "Mana Crypt"])
    sess = {"session_id": "s1", "status": brew.STATUS_DRAFTS_READY, "drafts": [bad],
            "commanders": [CMD], "created_at": "2026-01-01", "donor_decks": []}
    r.set(brew._session_key("s1"), json.dumps(sess))
    r.sadd(brew.BREW_SESSIONS_INDEX, "s1")
    with mock.patch.object(bg, "_catalog_for", return_value=CAT), \
            mock.patch.object(bg, "default_sources", return_value=[]):
        for out in (brew.get_drafts(r, "s1"), brew.release_session(r, brew.get_session(r, "s1"))["drafts"],
                    brew.list_sessions(r)[0]["drafts"]):
            assert len(out) == 1
            assert_legal(out[0])


def test_availability_never_leaks_an_illegal_draft():
    r = FakeRedis()
    guid = str(uuid.uuid4())
    bad = draft(GOOD[:20] + [guid, "Mana Crypt"])
    sess = {"session_id": "s4", "status": brew.STATUS_DRAFTS_READY, "drafts": [bad],
            "commanders": [CMD], "created_at": "2026-01-01", "donor_decks": []}
    r.set(brew._session_key("s4"), json.dumps(sess))
    with mock.patch.object(bg, "_catalog_for", return_value=CAT), \
            mock.patch.object(bg, "default_sources", return_value=[]):
        try:
            report = brew.get_availability(r, "s4")
        except BrewGateError:
            return
    assert guid not in json.dumps(report)
    assert "Mana Crypt" not in json.dumps(report)


def test_availability_withholds_unrecoverable_draft():
    r = FakeRedis()
    sess = {"session_id": "s5", "status": brew.STATUS_DRAFTS_READY, "commanders": ["Does Not Exist"],
            "drafts": [draft(GOOD[:20], commanders=("Does Not Exist",))], "created_at": "x"}
    r.set(brew._session_key("s5"), json.dumps(sess))
    with mock.patch.object(bg, "_catalog_for", return_value=CAT):
        with pytest.raises(BrewGateError):
            brew.get_availability(r, "s5")


def test_cached_unrecoverable_draft_is_withheld_not_returned():
    r = FakeRedis()
    sess = {"session_id": "s2", "status": brew.STATUS_DRAFTS_READY, "commanders": ["Does Not Exist"],
            "drafts": [draft(GOOD[:20], commanders=("Does Not Exist",))], "created_at": "x"}
    r.set(brew._session_key("s2"), json.dumps(sess))
    with mock.patch.object(bg, "_catalog_for", return_value=CAT):
        with pytest.raises(BrewGateError):
            brew.get_drafts(r, "s2")
        shown = brew.release_session(r, brew.get_session(r, "s2"))
    assert shown["drafts"] == [] and shown["status"] == brew.STATUS_FAILED


def test_build_never_stores_an_invalid_deck():
    """A builder returning garbage ends as a failed session, never drafts_ready."""
    import src.server.domain.draft.brew_builder as bb
    r = FakeRedis()
    sess = {"session_id": "s3", "status": brew.STATUS_BUILDING, "commanders": ["Does Not Exist"],
            "preferences": [{}], "created_at": "x"}
    r.set(brew._session_key("s3"), json.dumps(sess))

    class Bad(bb.BaseBrewBuilder):
        def build(self, session, r):
            return [draft(GOOD[:10], commanders=("Does Not Exist",))]

    orig = bb.get_builder()
    bb.set_builder(Bad())
    try:
        with pytest.raises(BrewGateError):
            brew.execute_build(r, "s3")
    finally:
        bb.set_builder(orig)
    saved = brew.get_session(r, "s3")
    assert saved["status"] == brew.STATUS_FAILED and not saved.get("drafts")


def test_check_flags_uuid_anywhere_in_output():
    d = draft()
    d["cards"][5]["rationale"] = f"picked via {uuid.uuid4()}"
    assert bg.UUID_CODE in bg.check_draft(d, CAT).codes()
    d2 = draft()
    d2["id"] = str(uuid.uuid4())  # ids in id fields are by design
    assert bg.check_draft(d2, CAT).valid
    with pytest.raises(BrewGateError):  # a UUID the repair cannot remove is an error
        gate(d)


# -- review follow-ups ---------------------------------------------------------------------------

def test_missing_commander_entry_is_flagged_and_repaired():
    d = draft(GOOD)
    d["cards"] = [c for c in d["cards"] if c["name"] != d["commanders"][0]]
    assert any(str(getattr(e.code, "value", e.code)) == "COMMANDER_ENTRY_MISSING"
               for e in bg.check_draft(d, CAT).errors)
    assert_legal(release_draft(d, None, catalog=CAT, sources=[]))


def test_stale_stamp_does_not_bypass_revalidation():
    out = release_draft(draft(GOOD), None, catalog=CAT, sources=[])
    out["cards"][5]["rationale"] = "see 123e4567-e89b-12d3-a456-426614174000"
    with pytest.raises(BrewGateError):
        release_draft(out, None, catalog=CAT, sources=[], repair=False)
