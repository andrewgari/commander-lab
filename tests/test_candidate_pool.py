"""Candidate pool (card 05): filters, ownership ranking, D6, degradation. Offline only."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace as NS

from src.server.domain.analytics.candidates import CandidateSources, candidate_pool, owned_from_rows

F = Path(__file__).parent / "fixtures" / "fact_pack"
ROOT = Path(__file__).resolve().parents[1]


def _load(n):
    return json.loads((F / n).read_text())


DECK = _load("golden_deck.json")
COLL = {c["name"].lower(): c for c in _load("scryfall_collection.json")["data"]}
SEARCH = {r: _load(f"scryfall_search_{r}.json")["data"] for r in ("ramp", "draw", "removal", "wipe", "protection", "recursion")}
OTAG = {"otag:ramp": "ramp", "otag:draw": "draw", "otag:removal": "removal", "otag:sweeper": "wipe",
        "otag:protects-creature": "protection", "otag:recursion": "recursion"}


def search(q):
    for k, v in OTAG.items():
        if k in q:
            return SEARCH[v]
    return []  # tutors: no recorded fixture


def sources(**over):
    s = dict(edhrec=lambda c: _load("edhrec_commander.json"),
             scryfall_batch=lambda names: {**COLL, **{c["name"].lower(): c for v in SEARCH.values() for c in v}},
             scryfall_search=search)
    s.update(over)
    return CandidateSources(**s)


def prof(cap=5.0, cards=(), strategies=(), pets=()):
    return NS(pet_cards=list(pets), dislikes=NS(cards=list(cards), strategies=list(strategies)), budget_norms=NS(hard_cap=cap))


def boom(*a, **k):
    raise RuntimeError("down")


def test_every_card_passes_every_constraint():
    pool = candidate_pool(DECK, NS(bracket=2), prof(cap=5.0, cards=["Mulldrifter"]), sources=sources())
    assert pool.cards
    ident = set("WUBG")
    have = {c["name"] for c in DECK["cards"]} | set(DECK["commanders"])
    allc = {**{c["name"]: c for c in COLL.values()}, **{c["name"]: c for v in SEARCH.values() for c in v}}
    for c in pool.cards:
        raw = allc[c["name"]]
        assert raw["legalities"]["commander"] == "legal"
        assert set(raw["color_identity"]) <= ident
        assert c["price_usd"] is not None and c["price_usd"] <= 5.0
        assert c["name"] not in have and c["name"] != "Mulldrifter"
        assert c["oracle_id"]
        if raw.get("game_changer"):
            raise AssertionError(f"game changer {c['name']} in a bracket-2 pool")
    assert pool.excluded.get("already_in_deck", 0) > 0


def test_owned_cards_flagged_and_ranked_first():
    names = [c["name"] for c in candidate_pool(DECK, None, prof(), sources=sources()).cards]
    pick = names[-1]
    free, sleeved = names[-2], names[-3]
    owned = {pick.lower(): {"copies": 1, "available": 1, "decks": []}, free.lower(): {"copies": 1, "available": 1, "decks": []},
             sleeved.lower(): {"copies": 1, "available": 0, "decks": ["Other Deck"]}}
    pool = candidate_pool(DECK, None, prof(), sources=sources(), owned=owned)
    top = [c["name"] for c in pool.cards[:3]]
    assert set(top[:2]) == {pick, free} and top[2] == sleeved
    assert all(c["owned"] for c in pool.cards[:3]) and not any(c["owned"] for c in pool.cards[3:])
    assert pool.cards[2]["owned_in_decks"] == ["Other Deck"]


def test_owned_from_rows():
    rows = [{"name": "Sol Ring", "ownership_status": "in_collection"}, {"name": "Sol Ring", "ownership_status": "in_deck", "deck": "X"},
            {"name": "Wish", "ownership_status": "not_owned"}]
    o = owned_from_rows(rows)
    assert o["sol ring"] == {"name": "Sol Ring", "copies": 2, "available": 1, "decks": ["X"]} and "wish" not in o


def test_budget_dislikes_and_strategies():
    base = candidate_pool(DECK, None, prof(cap=None), sources=sources())
    tight = candidate_pool(DECK, None, prof(cap=1.0), sources=sources())
    assert len(tight.cards) < len(base.cards) and all(c["price_usd"] <= 1.0 for c in tight.cards)
    assert tight.excluded["over_budget_hard_cap"] > 0
    victim = base.cards[0]["name"]
    assert victim not in candidate_pool(DECK, None, prof(cap=None, cards=[victim.upper()]), sources=sources()).names()
    no_tutor = candidate_pool(DECK, None, prof(cap=None, strategies=["ramp"]), sources=sources())
    assert "ramp" not in no_tutor.by_role and no_tutor.excluded["disliked_strategy"] > 0


def test_no_profile_says_so():
    pool = candidate_pool(DECK, None, None, sources=sources())
    assert pool.cards and any("No player profile" in n for n in pool.notes)


def test_grouped_by_role_and_theme_and_hash_stable():
    a = candidate_pool(DECK, None, prof(), sources=sources())
    b = candidate_pool(DECK, None, prof(), sources=sources())
    assert a.hash == b.hash
    assert a.by_role.get("ramp") and a.by_role.get("draw")
    assert all(n in a.names() for ns in a.by_role.values() for n in ns)
    json.dumps(a.to_dict())


# --- degradation ----------------------------------------------------------------------------------
def test_edhrec_down_still_builds_from_scryfall_and_says_so():
    pool = candidate_pool(DECK, None, prof(), sources=sources(edhrec=boom))
    assert pool.cards and pool.sources["edhrec"]["status"] == "unavailable" and "down" in pool.sources["edhrec"]["reason"]


def test_scryfall_search_down_still_builds_from_edhrec():
    pool = candidate_pool(DECK, None, prof(), sources=sources(scryfall_search=boom))
    assert pool.cards and pool.sources["scryfall_search:ramp"]["status"] == "unavailable"
    assert all("edhrec" in c["sources"] for c in pool.cards)


def test_nothing_resolves_means_empty_pool_not_guesses():
    pool = candidate_pool(DECK, None, prof(), sources=sources(scryfall_batch=boom))
    assert pool.cards == [] and any("identity unavailable" in n for n in pool.notes)


def test_unknown_price_excluded_when_cap_set():
    def search_no_price(q):
        return [{**c, "prices": {"usd": None}} for c in SEARCH["ramp"]]
    pool = candidate_pool(DECK, None, prof(cap=5.0), sources=sources(edhrec=boom, scryfall_search=search_no_price))
    assert pool.cards == [] and pool.excluded["price_unknown_with_budget_cap"] > 0


# --- D6 -------------------------------------------------------------------------------------------
def test_curated_catalog_is_not_an_input():
    tree = ast.parse((ROOT / "src/server/domain/analytics/candidates.py").read_text())
    imported = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    imported |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert "CURATED_CARD_CATALOG" not in imported and not any("recommendations" in i for i in imported)
