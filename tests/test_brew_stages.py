"""Brew stages 1-2 (skeleton, core construction) and the staged build end to end. No network: EDHREC and
Scryfall are injected through brew_stages.Deps; the panel LLM is a fake."""
import json
from datetime import datetime, timezone

import pytest

from src.server.domain.analytics.providers.edhrec import EDHRecRequestError, EDHRecNotFoundError
from src.server.domain.draft import brew, brew_builder, brew_edhrec_pool as pool, brew_panel, brew_stages as st
from src.server.domain.draft import reconstruct_pools
from tests.test_brew import FakeRedis


# -- EDHREC fake --------------------------------------------------------------------------------

def page(cards, decks=1000):
    """cards: {name: (num_decks, tag)} -> raw EDHREC commander page."""
    views = {}
    for n, (num, tag) in cards.items():
        views.setdefault(tag, []).append({"name": n, "num_decks": num, "potential_decks": decks, "synergy": 0.1})
    return {"container": {"json_dict": {"card": {"num_decks": decks},
                                        "cardlists": [{"tag": t, "cardviews": v} for t, v in views.items()]}}}


class FakeEdhrec:
    def __init__(self, base, themes=None, down=False):
        self.base, self.themes, self.down, self.calls = base, themes or {}, down, []

    def get_commander_page(self, names, theme=None):
        self.calls.append((tuple(names), theme))
        if self.down:
            raise EDHRecRequestError("boom")
        if theme:
            if theme.lower() not in self.themes:
                raise EDHRecNotFoundError("no theme")
            return self.themes[theme.lower()]
        return self.base


BASE = page({"Sol Ring": (900, "manaartifacts"), "Arcane Signet": (800, "manaartifacts"),
             "Rhystic Study": (450, "topcards"), "Cultivate": (420, "topcards"), "Low Card": (50, "topcards"),
             "Command Tower": (950, "lands")})
THEME = page({"Sol Ring": (400, "manaartifacts"), "Rhystic Study": (100, "topcards"), "Cultivate": (350, "topcards"),
              "Arcane Signet": (390, "manaartifacts")}, decks=800)


# -- stage 1 --------------------------------------------------------------------------------------

def test_split_evenly_and_land_target():
    assert st.split_evenly(38, ["U", "G"]) == {"Island": 19, "Forest": 19}
    assert st.split_evenly(37, ["W", "B", "G"]) == {"Plains": 13, "Swamp": 12, "Forest": 12}
    assert st.split_evenly(30, []) == {"Wastes": 30}
    assert st.land_target({"slots": [{"key": "lands", "target": 35}]}) == 35
    assert st.land_target({"slots": []}) == st.DEFAULT_LANDS


def test_fetch_pool_merges_commander_and_archetype_pages():
    r = FakeRedis()
    client = FakeEdhrec(BASE, {"counters": THEME})
    p = pool.fetch_pool(r, ["Atraxa, Praetors' Voice"], ["Mystery Theme", "Counters"], client=client)
    assert p["theme"] == "Counters"
    assert p["themes_skipped"] == ["Mystery Theme"]
    rs = p["cards"]["rhystic study"]
    assert rs["inclusion"] == 0.45 and rs["theme_inclusion"] == 0.125
    assert p["cards"]["low card"]["theme_inclusion"] is None
    assert pool.core_cards(p)[0]["name"] == "Sol Ring"
    names = [c["name"] for c in pool.core_cards(p)]
    assert names == ["Sol Ring", "Arcane Signet", "Cultivate"]  # Rhystic Study is 12% in the archetype; Command Tower isn't there


def test_core_cards_without_archetype_use_commander_inclusion_only():
    r = FakeRedis()
    p = pool.fetch_pool(r, ["Atraxa"], [], client=FakeEdhrec(BASE))
    assert {c["name"] for c in pool.core_cards(p)} == {"Sol Ring", "Arcane Signet", "Rhystic Study", "Command Tower", "Cultivate"}


def test_partners_use_one_combined_request():
    client = FakeEdhrec(BASE)
    pool.fetch_pool(FakeRedis(), ["Tymna the Weaver", "Thrasios, Triton Hero"], [], client=client)
    assert client.calls == [(("Tymna the Weaver", "Thrasios, Triton Hero"), None)]


def test_edhrec_down_degrades_instead_of_raising():
    p = pool.fetch_pool(FakeRedis(), ["Atraxa"], ["counters"], client=FakeEdhrec(BASE, down=True))
    assert p["cards"] == {} and "EDHREC unavailable" in p["degraded"]


def test_temp_pool_roundtrip_ttl_and_discard():
    r = FakeRedis()
    pool.store_pool(r, "s1", {"cards": {}})
    assert pool.load_pool(r, "s1") == {"cards": {}}
    pool.discard_pool(r, "s1")
    assert pool.load_pool(r, "s1") is None


def test_skeleton_plans_basics_and_stores_pool():
    r = FakeRedis()
    st.set_deps(st.Deps(edhrec_client=FakeEdhrec(BASE), scryfall_search=lambda q: []))
    sk = st.run_skeleton(r, {"session_id": "s9", "commanders": ["Atraxa"], "themes": []}, ["W", "U"],
                         {"slots": [{"key": "lands", "target": 36}]})
    assert sk["basic_plan"] == {"Plains": 18, "Island": 18} and sk["lands_target"] == 36
    assert pool.load_pool(r, "s9")["cards"]
    assert st.skeleton_summary(sk)["edhrec_cards"] == 6


# -- stage 2 helpers ------------------------------------------------------------------------------

def land_facts(names, identity=frozenset("WU")):
    return lambda n: ({"type_line": "Land", "identity": identity, "legal": True} if n in names else None)


def test_plan_lands_respects_owned_price_caps_and_colour_count():
    names = {"Command Tower", "Exotic Orchard", "Hallowed Fountain", "Glacial Fortress", "Reliquary Tower"}
    prices = {"Command Tower": 0.5, "Exotic Orchard": 1.0, "Hallowed Fountain": 20.0, "Glacial Fortress": 3.0,
              "Reliquary Tower": 5.0}
    common = dict(colors=["W", "U"], identity=frozenset("WU"), lands_target=38, card_fact=land_facts(names),
                  price_of=prices.get, is_owned=lambda n: n == "Hallowed Fountain")
    owned_first = st.plan_lands(session={"build_mode": "owned_first"}, **common)
    got = [l["name"] for l in owned_first]
    assert got[0] == "Command Tower" and "Hallowed Fountain" in got        # owned = free, whatever it costs
    assert sum(l["price"] for l in owned_first) <= st.OWNED_FIRST_LAND_BUDGET
    assert "Reliquary Tower" not in got or prices["Reliquary Tower"] <= 8.0
    tiny = st.plan_lands(session={"build_mode": "best_build", "budget": 8.0}, **{**common, "is_owned": lambda n: False})
    assert sum(l["price"] for l in tiny) <= 8.0 * st.BUDGET_LAND_SHARE and "Hallowed Fountain" not in [l["name"] for l in tiny]
    mono = st.plan_lands(session={"build_mode": "best_build"}, **{**common, "colors": ["W"]})
    assert [l["name"] for l in mono] == ["Reliquary Tower"]                # multi-colour staples need 2+ colours


def test_plan_lands_skips_unknown_unpriced_offcolour_and_caps_at_half():
    common = dict(colors=["W", "U"], identity=frozenset("WU"), session={"build_mode": "best_build"},
                  is_owned=lambda n: False)
    facts = {"Command Tower": {"type_line": "Land", "identity": frozenset(), "legal": True},
             "Blood Crypt": {"type_line": "Land — Swamp Mountain", "identity": frozenset("BR"), "legal": True},
             "Exotic Orchard": {"type_line": "Land", "identity": frozenset(), "legal": False}}
    out = st.plan_lands(lands_target=38, card_fact=facts.get, price_of=lambda n: 1.0, **common)
    assert [l["name"] for l in out] == ["Command Tower"]
    assert st.plan_lands(lands_target=38, card_fact=facts.get, price_of=lambda n: None, **common) == []
    allland = lambda n: {"type_line": "Land", "identity": frozenset(), "legal": True}
    assert len(st.plan_lands(lands_target=10, card_fact=allland, price_of=lambda n: 1.0, **common)) == 5


def test_category_sources_and_scryfall_candidates_cache_budget_and_failure():
    from src.server.domain.draft.brew_scoring_config import skeleton_from_template
    from src.server.domain.analytics import composition_service
    sk = skeleton_from_template(composition_service.app_default_template())
    src = st.category_sources(sk)
    assert src["ramp"][0] == "ramp" and src["card_advantage"][0] == "draw" and src["targeted_disruption"][0] == "removal"
    assert "plan" not in src and "lands" not in src
    r, queries = FakeRedis(), []
    deps = st.Deps(scryfall_search=lambda q: queries.append(q) or ["Sol Ring", "Mind Stone"])
    budget = [2]
    assert st.scryfall_candidates(r, "ramp", ["W", "U"], deps, budget) == ["Sol Ring", "Mind Stone"]
    st.scryfall_candidates(r, "ramp", ["W", "U"], deps, budget)           # cached: no second query
    assert queries == ["otag:ramp f:commander id<=wu"] and budget == [1]
    bad = st.Deps(scryfall_search=lambda q: (_ for _ in ()).throw(RuntimeError("down")))
    assert st.scryfall_candidates(r, "removal", [], bad, [4]) == []
    assert st.scryfall_candidates(r, "sweeper", [], deps, [0]) == []     # per-build call budget exhausted


def test_stage_tracker_persists_stage_records():
    r = FakeRedis()
    s = {"session_id": "brew_t", "status": "building"}
    brew._save_session(r, s)
    t = st.StageTracker(r, s)
    t.start(st.STAGE_SKELETON, "planning")
    t.finish(st.STAGE_SKELETON, {"lands_target": 38}, message="done")
    t.start(st.STAGE_CORE, "landbase")
    saved = brew.get_session(r, "brew_t")
    assert saved["stage"] == "core" and saved["progress"].startswith("Core construction")
    assert saved["build_stages"]["skeleton"]["status"] == "done" and saved["build_stages"]["core"]["status"] == "running"
    t.finish(st.STAGE_CORE, {}, degraded="no candidates")
    assert brew.get_session(r, "brew_t")["build_stages"]["core"]["status"] == "degraded"


# -- the staged build, end to end ------------------------------------------------------------------

def _catalog(r):
    def card(name, color, type_, **kw):
        r.set(f"card_meta:{name}", json.dumps({"color": color, "type": type_, "commander_legality": "legal", **kw}))
    card("Kaalia", "RWB", "Legendary Creature")
    for n in ("Sol Ring", "Arcane Signet"):
        card(n, "", "Artifact", cmc=1)
    card("Rhystic Study", "U", "Enchantment")              # off-colour for Kaalia: must never be added
    card("Cultivate", "G", "Sorcery")
    card("Command Tower", "", "Land")
    card("Exotic Orchard", "", "Land")
    card("Sacred Foundry", "RW", "Land — Mountain Plains")
    card("Blood Crypt", "BR", "Land — Swamp Mountain")
    for i in range(70):
        card(f"Filler {i:02d}", "R", "Creature", cmc=3)


@pytest.fixture
def staged(monkeypatch):
    monkeypatch.setattr(reconstruct_pools, "redis_price_lookup",
                        lambda r, refresh=True: (lambda names: {n: 2.0 for n in names}))
    r = FakeRedis()
    _catalog(r)
    base = page({"Sol Ring": (900, "manaartifacts"), "Arcane Signet": (800, "manaartifacts"),
                 "Rhystic Study": (700, "topcards"), "Command Tower": (950, "lands"), "Sacred Foundry": (500, "lands")}
                | {f"Filler {i:02d}": (420, "topcards") for i in range(10)})
    st.set_deps(st.Deps(edhrec_client=FakeEdhrec(base), scryfall_search=lambda q: []))
    brew_builder.set_builder(brew_builder.StubBrewBuilder())
    brew_panel.set_client_factory(None)
    yield r
    brew_panel.set_client_factory(None)


def test_staged_build_runs_every_stage_and_cleans_up(staged):
    r = staged
    s = brew.start_brew(r, "Kaalia", [], "best_build", power_level="5.5")
    sid = s["session_id"]
    s["status"] = brew.STATUS_BUILDING
    brew._save_session(r, s)
    drafts = brew_builder.get_builder().build(brew.get_session(r, sid), r)
    cards = drafts[0]["cards"]
    names = [c["name"] for c in cards]
    assert sum(c["quantity"] for c in cards) == 100
    assert "Rhystic Study" not in names                         # outside the commander's colour identity
    assert {"Sol Ring", "Arcane Signet", "Command Tower"} <= set(names)
    assert "Sacred Foundry" in names                            # on EDHREC's 40%+ list, priced, in identity
    basics = {c["name"]: c["quantity"] for c in cards if c["name"] in {"Plains", "Swamp", "Mountain"}}
    assert abs(basics["Plains"] - basics["Swamp"]) <= 1 and abs(basics["Swamp"] - basics["Mountain"]) <= 1
    stages = brew.get_session(r, sid)["build_stages"]
    assert [k for k in st.STAGE_ORDER if k in stages] == ["skeleton", "core", "deck"]
    assert all(v["status"] in ("done", "degraded") for v in stages.values())
    assert stages["skeleton"]["summary"]["edhrec_cards"] == 15
    assert pool.load_pool(r, sid) is not None                    # build() leaves cleanup to execute_build


def test_execute_build_discards_temp_pool_and_survives_edhrec_outage(staged):
    r = staged
    st.set_deps(st.Deps(edhrec_client=FakeEdhrec(BASE, down=True), scryfall_search=lambda q: []))
    s = brew.start_brew(r, "Kaalia", [], "best_build", power_level="5.5")
    brew.approve_rules(r, s["session_id"], [])
    from src.server.domain.draft import brew_review
    previous = brew_review.get_reviewer()
    brew_review.set_reviewer(lambda d, sess: {"status": "pass", "reasons": []})
    try:
        brew.trigger_build(r, s["session_id"], sync=True)
    finally:
        brew_review.set_reviewer(previous)
    done = brew.get_session(r, s["session_id"])
    assert done["status"] == brew.STATUS_DRAFTS_READY
    assert done["build_stages"]["skeleton"]["status"] == "degraded"
    assert "EDHREC unavailable" in done["build_stages"]["skeleton"]["degraded"]
    assert pool.load_pool(r, s["session_id"]) is None             # temp EDHREC data is gone after the build
    assert "review" in done["build_stages"]


class ScriptedPanel:
    def chat(self, messages, max_tokens=None):
        text = messages[1]["content"]
        if "ROLE: critic" in text:
            return json.dumps({"attacks": []})
        return json.dumps({"votes": [{"card": "Arcane Signet", "vote": "cut", "reason": "redundant"}], "suggest": []})


def test_panel_cuts_are_applied_and_recorded_on_the_session(staged):
    r = staged
    brew_panel.set_client_factory(lambda task: ScriptedPanel())
    s = brew.start_brew(r, "Kaalia", [], "best_build", power_level="5.5")
    session = brew.get_session(r, s["session_id"])
    draft = brew_builder.get_builder().build(session, r)[0]
    assert "Arcane Signet" not in [c["name"] for c in draft["cards"]]
    assert sum(c["quantity"] for c in draft["cards"]) == 100
    rec = session["stage_reviews"]["core"]
    assert rec["status"] == "ok" and "Arcane Signet" in rec["cuts"]
    assert set(rec["lenses"]) == set(brew_panel.STAGE_LENSES["core"])


# -- stage 3: plan-driven, EDHREC is only scaffold ---------------------------------------------------

def test_plan_items_from_themes_commander_text_and_types():
    from src.server.domain.draft import brew_plan as bpl
    items = bpl.build_plan(["Treasure"], "make lots of tokens", "Whenever a creature you control dies, sacrifice a Treasure.",
                           "Legendary Creature — Goblin Shaman")
    labels = [i.label for i in items]
    assert labels[:2] == ["Treasure", "Treasure (tagged)"] and "tokens" in labels and "sacrifice" in labels
    assert items[0].query == 'o:"treasure"' and items[1].query == "otag:treasure"
    assert bpl.full_query(items[0], "rw") == 'o:"treasure" f:commander id<=rw -t:land'
    assert bpl.full_query(items[0], "").endswith("id<=c -t:land")
    assert len(bpl.build_plan(["a", "b", "c", "d"], "", "", "")) == bpl.MAX_ITEMS
    assert bpl.creature_types("Legendary Creature — Elf Druid") == ["Elf", "Druid"] and bpl.creature_types("Sorcery") == []


def _plan_catalog(r):
    _catalog(r)
    for i in range(20):
        r.set(f"card_meta:Plan {i:02d}", json.dumps({"color": "R", "type": "Creature", "cmc": 3, "commander_legality": "legal",
                                                     "oracle_text": "Haste. Whenever this attacks, you may sacrifice a Treasure to deal 1 damage."}))


def _scry(q):
    return [f"Plan {i:02d}" for i in range(20)] if "treasure" in q else []


def _build_themed(r, themes=("Treasure",)):
    s = brew.start_brew(r, "Kaalia", [], "best_build", power_level="5.5", themes=list(themes))
    session = brew.get_session(r, s["session_id"])
    return session, brew_builder.get_builder().build(session, r)[0]


def test_stage3_is_plan_driven_and_marks_provenance(staged):
    r = staged
    _plan_catalog(r)
    base = page({"Sol Ring": (900, "manaartifacts"), "Command Tower": (950, "lands")}
                | {f"Filler {i:02d}": (420, "topcards") for i in range(10)})
    st.set_deps(st.Deps(edhrec_client=FakeEdhrec(base), scryfall_search=_scry))
    session, draft = _build_themed(r)
    by = {c["name"]: c["provenance"] for c in draft["cards"]}
    assert sum(c["quantity"] for c in draft["cards"]) == 100
    assert by["Kaalia"] == "commander" and by["Sol Ring"].startswith("scaffold_")
    assert sum(1 for v in by.values() if v == "plan") >= 15 and by["Plan 00"] == "plan"
    assert any(v == "basic" for v in by.values())
    summary = session["build_stages"]["deck"]["summary"]
    assert summary["plan"][0] == "Treasure" and summary["plan_picks"] >= 15
    cut = summary["scaffold_replaced"]
    assert len(cut) == 5 and all(n.startswith("Filler") for n in cut)       # half of the 10 popular-only picks
    assert all(n not in by for n in cut)
    assert "Sol Ring" not in cut                                              # functional (ramp) scaffold stays


def test_pipeline_works_with_edhrec_down_scryfall_only(staged):
    r = staged
    _plan_catalog(r)
    st.set_deps(st.Deps(edhrec_client=FakeEdhrec(BASE, down=True), scryfall_search=_scry))
    session, draft = _build_themed(r)
    by = {c["name"]: c["provenance"] for c in draft["cards"]}
    assert sum(c["quantity"] for c in draft["cards"]) == 100
    assert sum(1 for v in by.values() if v == "plan") >= 15
    assert session["build_stages"]["skeleton"]["status"] == "degraded"
    assert not any(v == "scaffold_edhrec" for v in by.values())


def test_pipeline_works_with_edhrec_and_scryfall_both_down(staged):
    r = staged
    _plan_catalog(r)
    boom = lambda q: (_ for _ in ()).throw(RuntimeError("down"))
    st.set_deps(st.Deps(edhrec_client=FakeEdhrec(BASE, down=True), scryfall_search=boom))
    session, draft = _build_themed(r)
    assert sum(c["quantity"] for c in draft["cards"]) == 100                  # pools + ranker fallback + basics still fill the deck
    assert session["build_stages"]["deck"]["status"] == "degraded"
    assert not any(c["provenance"] in ("plan", "scaffold_edhrec") for c in draft["cards"])


def test_panel_can_cut_scaffold_but_not_owned_and_sees_the_plan(staged):
    r = staged
    _plan_catalog(r)
    seen = {}

    class Panel:
        def chat(self, messages, max_tokens=None):
            text = messages[1]["content"]
            if "Stage: deck" not in text:   # stage 2 (core) has no opinion
                return json.dumps({"votes": [], "suggest": []})
            seen["text"] = text
            if "ROLE: critic" in text:
                return json.dumps({"attacks": []})
            return json.dumps({"votes": [{"card": "Filler 09", "vote": "cut", "reason": "just popular"}], "suggest": []})

    base = page({f"Filler {i:02d}": (420, "topcards") for i in range(10)})
    st.set_deps(st.Deps(edhrec_client=FakeEdhrec(base), scryfall_search=lambda q: []))   # no plan: nothing auto-cut
    brew_panel.set_client_factory(lambda task: Panel())
    session, draft = _build_themed(r)
    assert "Filler 09" not in [c["name"] for c in draft["cards"]]
    assert "Filler 09" in session["stage_reviews"]["deck"]["cuts"]
    assert "SCAFFOLD" in seen["text"] and "Treasure" in seen["text"]


# -- regression: lands and ramp stay inside the template (live brew had far too many) ---------------

def _rocky_catalog(r):
    _catalog(r)
    for i in range(30):
        r.set(f"card_meta:Rock {i:02d}", json.dumps({"color": "", "type": "Artifact", "cmc": 2, "commander_legality": "legal",
                                                     "oracle_text": "{T}: Add one mana of any color."}))
    for i in range(30):
        r.set(f"card_meta:Utility Land {i:02d}", json.dumps({"color": "", "type": "Land", "commander_legality": "legal"}))
    for i in range(40):
        r.set(f"card_meta:Spell {i:02d}", json.dumps({"color": "R", "type": "Creature", "cmc": 3, "commander_legality": "legal",
                                                      "oracle_text": "Haste. Whenever this attacks, you may sacrifice a Treasure."}))


def _counts(r, session, draft):
    from src.server.domain.draft.brew_builder import count_template_rows
    return count_template_rows(r, session, draft)


def test_lands_and_ramp_stay_within_template(staged):
    r = staged
    _rocky_catalog(r)
    pool_cards = ({f"Rock {i:02d}": (800, "manaartifacts") for i in range(30)}
                  | {f"Utility Land {i:02d}": (700, "lands") for i in range(30)}
                  | {"Plains": (900, "lands"), "Swamp": (900, "lands"), "Mountain": (900, "lands")}
                  | {f"Spell {i:02d}": (450, "topcards") for i in range(20)})
    st.set_deps(st.Deps(edhrec_client=FakeEdhrec(page(pool_cards)), scryfall_search=lambda q: []))
    session, draft = _build_themed(r)
    c = _counts(r, session, draft)
    assert sum(x["quantity"] for x in draft["cards"]) == 100
    assert c["lands"] <= c["high"]["lands"], c
    assert c["ramp"] <= c["high"]["ramp"], c
    assert c["spells"] >= 99 - c["high"]["lands"] - 0, c
    names = {x["name"]: x for x in draft["cards"]}
    assert all(names[b].get("quantity", 1) >= 1 for b in ("Plains",) if b in names)


def test_sanity_trim_cuts_surplus_basics_and_ramp_instead_of_failing(staged):
    from src.server.domain.draft.brew_builder import sanity_trim
    r = staged
    _rocky_catalog(r)
    s = brew.start_brew(r, "Kaalia", [], "best_build", power_level="5.5")
    session = brew.get_session(r, s["session_id"])
    cards = [{"name": "Kaalia", "quantity": 1, "tier": 3, "provenance": "commander"},
             {"name": "Plains", "quantity": 30, "tier": 3}, {"name": "Mountain", "quantity": 20, "tier": 3}]
    cards += [{"name": f"Rock {i:02d}", "quantity": 1, "tier": 3} for i in range(20)]
    cards += [{"name": f"Spell {i:02d}", "quantity": 1, "tier": 3} for i in range(20)]
    draft = {"commanders": ["Kaalia"], "cards": cards}
    before = _counts(r, session, draft)
    info = sanity_trim(r, session, draft)
    after = _counts(r, session, draft)
    assert before["lands"] == 50 and after["lands"] == before["high"]["lands"]
    assert before["ramp"] == 20 and after["ramp"] == before["high"]["ramp"]
    assert after["spells"] == before["spells"] - (before["ramp"] - before["high"]["ramp"])
    assert info["removed"] and draft["sanity_trim"]["after"]["lands"] == after["lands"]
    assert sanity_trim(r, session, {"commanders": ["Kaalia"], "cards": cards, "user_edited": True}) == {}


def test_reviewer_is_given_template_counts_and_calibration(staged):
    from src.server.domain.draft import brew_review
    r = staged
    _rocky_catalog(r)
    s = brew.start_brew(r, "Kaalia", [], "best_build", power_level="5.5")
    session = brew.get_session(r, s["session_id"])
    draft = {"commanders": ["Kaalia"], "cards": [{"name": "Kaalia", "quantity": 1}, {"name": "Plains", "quantity": 36},
                                                   {"name": "Rock 00", "quantity": 1}]}
    line = brew_review._template_line(draft, session, r)
    assert "lands 38" in line and "ramp 10" in line and "36 lands" in line and "1 ramp" in line
    from src.server.domain.knowledge import build_messages
    msgs, _ = build_messages("brew_review", user_text="x")
    assert "Template for this brew" in msgs[0]["content"][0]["text"]


def test_power_nudge_swaps_generic_cards_for_stronger_ones_when_far_below_target(staged):
    from src.server.domain.draft import brew_builder
    r = staged
    _rocky_catalog(r)
    for i in range(150):  # plenty of ordinary spells, so the tutors (sorted last) are not reached by the generic top-up
        r.set(f"card_meta:Spell Extra {i:03d}", json.dumps({"color": "R", "type": "Creature", "cmc": 3, "commander_legality": "legal"}))
    for n in ("Zzz Tutor A", "Zzz Tutor B"):
        r.set(f"card_meta:{n}", json.dumps({"color": "B", "type": "Instant", "cmc": 1, "commander_legality": "legal",
                                            "oracle_text": "Search your library for a card, put it into your hand."}))
    base = page({f"Spell {i:02d}": (450, "topcards") for i in range(30)})
    st.set_deps(st.Deps(edhrec_client=FakeEdhrec(base), scryfall_search=lambda q: ["Zzz Tutor A", "Zzz Tutor B"] if "tutor" in q else []))
    s = brew.start_brew(r, "Kaalia", [], "best_build", power_level="7.5")
    session = brew.get_session(r, s["session_id"])
    brew_builder.get_builder().build(session, r)
    nudge = session["build_stages"]["deck"]["summary"]["power_nudge"]
    assert nudge["before"] is not None and nudge["before"] < 7.5 - 0.75
    assert set(nudge["in"]) <= {"Zzz Tutor A", "Zzz Tutor B"} and nudge["in"]
    assert nudge["after"] > nudge["before"] and len(nudge["in"]) == len(nudge["out"])
