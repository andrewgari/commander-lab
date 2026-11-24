"""No-donor best_build: ranked (not alphabetical) Tier 3, a real mana base, Game Changers known to the reviewer."""
import json
from datetime import datetime, timezone

from src.server.domain.analytics.card_eval import edhrec_data
from src.server.domain.analytics.providers.edhrec import commander_slug
from src.server.domain.draft import brew, brew_builder, brew_review, reconstruct_pools
from tests.test_brew import FakeRedis


def _catalog(r):
    r.set("card_meta:Kaalia of the Vast", json.dumps({"color": "RWB", "type": "Legendary Creature", "commander_legality": "legal"}))
    # 400 junk spells that sort before everything useful (the old alphabetical cap kept only these).
    for i in range(400):
        r.set(f"card_meta:Aaa Filler {i:03d}", json.dumps({"color": "R", "type": "Creature", "cmc": 3, "commander_legality": "legal"}))
    for n in range(60):
        r.set(f"card_meta:Zed Staple {n:02d}", json.dumps({"color": "W", "type": "Creature", "cmc": 2, "commander_legality": "legal"}))
    for n in range(8):
        r.set(f"card_meta:Zed Land {n}", json.dumps({"color": "", "type": "Land", "commander_legality": "legal"}))


def _cache_edhrec(r, names):
    cards = {n.lower(): {"name": n, "inclusion": 0.9, "synergy": 0.5, "tags": []} for n in names}
    r.set(edhrec_data.CACHE_PREFIX + commander_slug(["Kaalia of the Vast"]),
          json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "cards": cards}))


def _build(r):
    brew_builder.set_builder(brew_builder.StubBrewBuilder())
    s = brew.start_brew(r, "Kaalia of the Vast", [], "best_build", power_level="5.5")
    return brew_builder.get_builder().build(s, r)[0]


def test_tier3_pool_is_ranked_not_alphabetical(monkeypatch):
    monkeypatch.setattr(reconstruct_pools, "redis_price_lookup", lambda r, refresh=True: (lambda names: {}))
    r = FakeRedis()
    _catalog(r)
    staples = [f"Zed Staple {n:02d}" for n in range(60)]
    _cache_edhrec(r, staples)
    s = {"commanders": ["Kaalia of the Vast"], "preferences": [{}]}
    pools = reconstruct_pools.build_candidate_pools(
        s, r, price_lookup=lambda ns: {}, config=brew._pool_config(r, s))
    assert pools.tier3[0].name.startswith("Zed Staple"), [c.name for c in pools.tier3[:3]]
    assert not all(c.name.startswith("Aaa") for c in pools.tier3[:60])


def test_no_donor_build_keeps_land_slots_and_adds_basics(monkeypatch):
    monkeypatch.setattr(reconstruct_pools, "redis_price_lookup", lambda r, refresh=True: (lambda names: {}))
    r = FakeRedis()
    _catalog(r)
    d = _build(r)
    total = sum(c["quantity"] for c in d["cards"])
    assert total == 100
    basics = sum(c["quantity"] for c in d["cards"] if c["name"] in {"Mountain", "Plains", "Swamp"})
    assert basics >= 20, f"expected a real mana base, got {basics} basics"


def test_reviewer_is_told_game_changers_from_snapshot():
    line = brew_review._game_changers_line({"cards": [{"name": "Rhystic Study"}, {"name": "Sol Ring"}]})
    assert "Rhystic Study" in line and "Sol Ring" not in line
    assert "none" in brew_review._game_changers_line({"cards": [{"name": "Sol Ring"}]})


def test_game_changers_line_degrades_without_failing(monkeypatch):
    from src.server.domain.draft import brew_power
    monkeypatch.setattr(brew_power, "game_changer_names", lambda: frozenset())
    assert "Do not fail the deck" in brew_review._game_changers_line({"cards": []})


def test_donor_cards_outside_commander_identity_are_excluded(monkeypatch):
    monkeypatch.setattr(reconstruct_pools, "redis_price_lookup", lambda r, refresh=True: (lambda names: {}))
    r = FakeRedis()
    _catalog(r)
    r.set("card_meta:Blue Thing", json.dumps({"color": "U", "type": "Creature", "commander_legality": "legal"}))
    r.set("decks", json.dumps([{"id": 9, "registry_id": "archidekt:9", "name": "Donor", "status": "physical",
                                "commanders": ["Other"], "cards": [{"name": "Blue Thing", "quantity": 1},
                                                                    {"name": "Zed Staple 01", "quantity": 1}]}]))
    brew_builder.set_builder(brew_builder.StubBrewBuilder())
    s = brew.start_brew(r, "Kaalia of the Vast", ["archidekt:9"], "best_build", power_level="5.5")
    names = {c["name"] for c in brew_builder.get_builder().build(s, r)[0]["cards"]}
    assert "Blue Thing" not in names and "Zed Staple 01" in names
