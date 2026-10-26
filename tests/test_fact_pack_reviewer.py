"""Deck-reviewer fact pack (card 05): golden snapshot, failure modes, roles, TTL env. Offline only."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import pytest

from src.server.domain.analytics import fact_sources
from src.server.domain.analytics.fact_pack import build_fact_pack
from src.server.domain.decks.commander_roles import function_roles

F = Path(__file__).parent / "fixtures" / "fact_pack"
ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


def _load(n):
    return json.loads((F / n).read_text())


DECK = _load("golden_deck.json")
COLL = {c["name"].lower(): c for c in _load("scryfall_collection.json")["data"]}
SALT = {"salt_score": 3.2, "cards": {"Rhystic Study": 1.1, "Cyclonic Rift": 0.9}}


def fetchers(**over):
    base = {"scryfall": lambda n: COLL.get(n.lower()), "edhrec": lambda d: _load("edhrec_commander.json"),
            "spellbook": lambda d: _load("spellbook_find_my_combos.json"), "salt": lambda d: SALT}
    base.update(over)
    return base


def pack(**over):
    return build_fact_pack(DECK, fetchers(**over), now=NOW)


def boom(*_a, **_k):
    raise RuntimeError("source down")


def _snapshot(p):
    d = dict(p)
    d["cards"] = [{k: c[k] for k in ("name", "oracle_id", "game_changer", "cmc")} for c in d["cards"]]
    return d


def test_golden_snapshot():
    snap = json.dumps(_snapshot(pack()), indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    path = F / "golden_fact_pack.snapshot.json"
    if os.getenv("UPDATE_SNAPSHOTS"):
        path.write_text(snap)
    assert snap == path.read_text(), "fact pack changed; review the diff, then rerun with UPDATE_SNAPSHOTS=1"


def test_hash_is_stable_across_fetch_times_and_changes_with_data():
    a = pack()
    b = build_fact_pack(DECK, fetchers(), now=datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert a.hash == b.hash and len(a.hash) == 64
    assert pack(salt=lambda d: {"salt_score": 9.9, "cards": {}}).hash != a.hash
    json.dumps(a)  # JSON-serializable


def test_charts_validate_against_review_document_schema():
    schema = json.loads((ROOT / "schemas" / "review_document.schema.json").read_text())
    sub = {"$schema": schema.get("$schema"), "$defs": schema["$defs"], **schema["properties"]["charts"]}
    jsonschema.validate(pack()["charts"], sub)


def test_every_datum_has_source_and_fetched_at_or_is_unavailable():
    p = pack()
    for sec in ("curve", "mana", "roles", "bracket", "combos", "edhrec", "salt", "synergy"):
        assert (p[sec].get("source") and p[sec].get("fetched_at")) or p[sec].get("status") == "unavailable", sec
    for c in p["cards"]:
        assert c["source"] == "scryfall" and c["fetched_at"]
        assert c["oracle_text"] and c["oracle_id"] and isinstance(c["game_changer"], bool)


def test_values_match_the_recorded_sources():
    p = pack()
    assert p["mana"]["lands"] == sum(c["quantity"] for c in DECK["cards"] if "Land" in COLL[c["name"].lower()]["type_line"])
    assert p["mana"]["norms"]["lands"]["target"] == 38 and "edhrec.com/articles" in p["mana"]["norms"]["lands"]["source"]
    assert [c["card_names"] for c in p["combos"]["complete"]] == [["Demonic Consultation", "Thassa's Oracle"]]
    assert all(len(m["missing"]) == 1 for m in p["combos"]["near_misses"])
    sol = next(c for c in p["edhrec"]["present"] if c["name"] == "Sol Ring")
    cv = next(v for cl in _load("edhrec_commander.json")["container"]["json_dict"]["cardlists"] for v in cl["cardviews"] if v["name"] == "Sol Ring")
    assert sol["inclusion"] == round(cv["num_decks"] / cv["potential_decks"], 4)
    assert p["bracket"]["minimum_bracket"] == 4 and {r["rule"] for r in p["bracket"]["reasons"]} >= {"game_changers", "two_card_combos"}
    assert "Thassa's Oracle" in p["synergy"]["orphans"] and p["synergy"]["clusters"][0]["name"] == "counters"


# --- failure modes: each external source degrades explicitly, the rest of the pack survives ---------
@pytest.mark.parametrize("key,section", [("spellbook", "combos"), ("edhrec", "edhrec"), ("salt", "salt")])
def test_source_failure_is_explicit_unavailable(key, section):
    p = pack(**{key: boom})
    assert p[section]["status"] == "unavailable" and "source down" in p[section]["reason"]
    assert p["curve"]["buckets"] and p["mana"]["lands"]  # other sections unaffected


def test_spellbook_down_warns_that_bracket_is_only_a_floor():
    b = pack(spellbook=boom)["bracket"]
    assert any("NOT checked" in n for n in b["notes"]) and b["minimum_bracket"] == 4  # game changers alone


def test_scryfall_down_makes_sections_unavailable_not_zero():
    p = pack(scryfall=boom)
    for sec in ("curve", "mana", "roles", "synergy", "charts"):
        assert p[sec]["status"] == "unavailable", sec
    assert p["cards"][0]["oracle"]["status"] == "unavailable"


@pytest.mark.parametrize("bad", [{}, {"container": {}}, {"container": {"json_dict": {"cardlists": [], "card": {}}}}, "<html>"])
def test_edhrec_shape_change_degrades_with_reason(bad):
    e = pack(edhrec=lambda d: bad)["edhrec"]
    assert e["status"] == "unavailable" and "shape" in e["reason"]


def test_spellbook_shape_change_degrades():
    assert pack(spellbook=lambda d: {"oops": 1})["combos"]["status"] == "unavailable"


# --- roles ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("name,expected", [
    ("Sol Ring", ["ramp"]), ("Farseek", ["ramp"]), ("Swords to Plowshares", ["targeted_removal"]),
    ("Wrath of God", ["wipes"]), ("Toxic Deluge", ["wipes"]), ("Heroic Intervention", ["protection"]),
    ("Demonic Tutor", ["tutors"]), ("Sun Titan", ["recursion"]), ("Animate Dead", ["recursion"]),
    ("Forest", []), ("Command Tower", []),
])
def test_function_roles(name, expected):
    c = COLL[name.lower()]
    assert function_roles({"name": name, "type_line": c["type_line"], "oracle_text": c.get("oracle_text", "")}) == expected


# --- cache TTL env --------------------------------------------------------------------------------
def test_ttl_env_overrides(monkeypatch):
    assert fact_sources.ttl_seconds("FACT_PACK_EDHREC_TTL_HOURS") == 24 * 3600
    monkeypatch.setenv("FACT_PACK_EDHREC_TTL_HOURS", "0.5")
    assert fact_sources.ttl_seconds("FACT_PACK_EDHREC_TTL_HOURS") == 1800
    monkeypatch.setenv("FACT_PACK_EDHREC_TTL_HOURS", "junk")
    assert fact_sources.ttl_seconds("FACT_PACK_EDHREC_TTL_HOURS") == 24 * 3600


def test_cache_respects_ttl_refresh_and_never_caches_failures():
    t = [0.0]
    c = fact_sources.TTLCache(clock=lambda: t[0])
    calls = []
    f = lambda: calls.append(1) or len(calls)  # noqa: E731
    assert c.get_or_fetch("k", 10, f) == 1 and c.get_or_fetch("k", 10, f) == 1
    t[0] = 11
    assert c.get_or_fetch("k", 10, f) == 2
    assert c.get_or_fetch("k", 10, f, refresh=True) == 3
    with pytest.raises(RuntimeError):
        c.get_or_fetch("bad", 10, boom)
    assert c.get_or_fetch("bad", 10, lambda: "ok") == "ok"


def test_default_fetchers_use_cache_and_scryfall_spacing(monkeypatch):
    calls = []
    monkeypatch.setattr(fact_sources, "scryfall_collection", lambda names: calls.append(names) or {"a": 1})
    cache = fact_sources.TTLCache()
    fx = fact_sources.default_fetchers(cache=cache)
    assert fx["scryfall_batch"](["A"]) == fx["scryfall_batch"](["A"]) and len(calls) == 1
    fact_sources.default_fetchers(refresh=True, cache=cache)["scryfall_batch"](["A"])
    assert len(calls) == 2
    assert fact_sources.SCRYFALL_MIN_INTERVAL >= 0.5 and "commander-lab" in fact_sources.SCRYFALL_UA["User-Agent"]


def test_batch_fetcher_failure_is_explicit():
    p = build_fact_pack(DECK, {"scryfall_batch": boom}, now=NOW)
    assert p["cards"][0]["oracle"]["status"] == "unavailable" and "source down" in p["cards"][0]["oracle"]["reason"]
