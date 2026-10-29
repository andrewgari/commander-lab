"""GET /api/v2/cards/search (docs/ui/card-search.md §3, §3.1, §5).

Pure translation tests (one per §3 row + combos), the commander-eligibility
predicate, and endpoint tests on in-memory SQLite (the repo's Postgres
fixture) with Scryfall and Redis stubbed: owned / not_owned / any, pairing,
degraded fallback, and the cache rules (a failure is never cached; ownership
is never cached; a partial eligible-set pull is never written).
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.server.app.api import cards as cards_api
from src.server.app.api import deps
from src.server.domain.cards import commander_set
from src.server.domain.cards.commander_eligibility import is_commander_eligible
from src.server.domain.cards.search_query import (
    SearchParamError,
    build_scryfall_query,
    parse_filters,
)
from src.server.domain.draft.deck_validator import CardInfo, check_partner_pair
from src.server.infra.db import repositories as repo
from src.server.infra.db.orm_models import Base
from src.server.infra.db.session import session_scope
from src.server.infra.services import card_search_service as svc

ELIGIBLE_Q = "is:commander legal:commander game:paper"
BASE = "legal:commander game:paper"


# ---------------------------------------------------------------------------
# §3 translation table: one case per row
# ---------------------------------------------------------------------------

def q(**params):
    return build_scryfall_query(parse_filters(**params))


@pytest.mark.parametrize("params, expected", [
    # Name: each token -> name:<tok>
    ({"name": "sol ring"}, f"{BASE} name:sol name:ring"),
    ({"name": "atraxa, praetors'"}, f'{BASE} name:atraxa name:"praetors\'"'),
    # Color identity x mode
    ({"ci": "wub"}, f"{BASE} id<=wub"),
    ({"ci": "wub", "ci_mode": "within"}, f"{BASE} id<=wub"),
    ({"ci": "bug", "ci_mode": "exact"}, f"{BASE} id=ubg"),
    ({"ci": "ub", "ci_mode": "includes"}, f"{BASE} id>=ub"),
    ({"ci": "C", "ci_mode": "within"}, f"{BASE} id=c"),
    ({"ci": "c", "ci_mode": "includes"}, f"{BASE} id=c"),
    # Type: chips OR, free text ANDed
    ({"type": "creature"}, f"{BASE} (t:creature)"),
    ({"type": "creature,artifact"}, f"{BASE} (t:creature or t:artifact)"),
    ({"type": "creature,artifact", "type_text": "elf"},
     f"{BASE} (t:creature or t:artifact) t:elf"),
    ({"type_text": "legendary elf"}, f"{BASE} t:legendary t:elf"),
    # Mana value chips OR, 7+ is open ended
    ({"mv": "1,2,7+"}, f"{BASE} (mv=1 or mv=2 or mv>=7)"),
    ({"mv": "0"}, f"{BASE} (mv=0)"),
    # Rarity
    ({"rarity": "rare,mythic"}, f"{BASE} (r:rare or r:mythic)"),
    # Oracle text: quoted phrase stays one term
    ({"oracle": "flying"}, f"{BASE} o:flying"),
    ({"oracle": 'draw "a card" flying'}, f'{BASE} o:draw o:"a card" o:flying'),
    ({"oracle": '"draw a card"'}, f'{BASE} o:"draw a card"'),
    # Owned is server-side only: never reaches Scryfall
    ({"owned": "owned"}, BASE),
    ({"owned": "not_owned"}, BASE),
    ({"owned": "any"}, BASE),
    # Can be commander
    ({"cmdr": True}, f"{BASE} is:commander"),
    ({}, BASE),
])
def test_translation_row(params, expected):
    assert q(**params) == expected


def test_translation_combo_matches_spec_example_shape():
    got = q(ci="wub", ci_mode="within", type="creature", oracle='"draw a card"', cmdr=True)
    assert got == f'{BASE} id<=wub (t:creature) o:"draw a card" is:commander'


def test_translation_combo_every_filter():
    got = q(name="elf", ci="g", ci_mode="exact", type="creature,instant", type_text="elf",
            mv="2,3", rarity="uncommon", oracle="mana", owned="owned")
    assert got == (f"{BASE} name:elf id=g (t:creature or t:instant) t:elf "
                   "(mv=2 or mv=3) (r:uncommon) o:mana")


def test_cmdr_absent_has_no_is_commander():
    assert "is:commander" not in q(type="creature")
    assert "is:commander" in q(type="creature", cmdr=True)


def test_user_text_cannot_inject_scryfall_syntax():
    # stray quotes are stripped and anything with syntax characters is quoted,
    # so user text stays inside its own name:/o: term
    got = q(name='sol" or t:land', oracle='x" -is:commander')
    assert got == f'{BASE} name:sol name:or name:"t:land" o:x o:"-is:commander"'


@pytest.mark.parametrize("params", [
    {"ci_mode": "sideways"},
    {"ci": "wx"},
    {"type": "creature,dragon"},
    {"mv": "8"},
    {"mv": "x"},
    {"rarity": "legendary"},
    {"owned": "maybe"},
])
def test_invalid_params_raise(params):
    with pytest.raises(SearchParamError):
        parse_filters(**params)


# ---------------------------------------------------------------------------
# §3.1.2 predicate fallback
# ---------------------------------------------------------------------------

def _row(type_line, text="", legal="legal"):
    return {"type_line": type_line, "oracle_text": text,
            "legalities": {"commander": legal}}


@pytest.mark.parametrize("row, expected", [
    (_row("Legendary Creature — Phyrexian Angel"), True),
    (_row("Legendary Planeswalker — Ob Nixilis", "Ob Nixilis can be your commander."), True),
    (_row("Legendary Enchantment Creature — Background"), True),
    (_row("Legendary Enchantment — Background"), True),
    (_row("Legendary Artifact — Vehicle"), True),
    (_row("Legendary Artifact — Spacecraft"), True),
    (_row("Artifact", ""), False),
    (_row("Creature — Elf"), False),
    (_row("Legendary Creature — Human", legal="banned"), False),
    # only the FRONT face counts
    (_row("Legendary Creature — Human // Creature — Wolf"), True),
    (_row("Creature — Human // Legendary Creature — Wolf"), False),
])
def test_is_commander_eligible(row, expected):
    assert is_commander_eligible(row) is expected


def test_is_commander_eligible_known_gap_grist_style_walker():
    # No P/T column: a Grist-style walker with no "can be your commander" text
    # is not recognised. Documented gap of the LAST fallback only.
    assert is_commander_eligible(_row("Legendary Planeswalker — Grist")) is False


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeRedis:
    def __init__(self):
        self.store, self.sets, self.ttls = {}, {}, {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, ex=None):
        self.store[k] = v
        if ex:
            self.ttls[k] = ex

    def sadd(self, k, *vals):
        self.sets.setdefault(k, set()).update(vals)

    def smembers(self, k):
        return set(self.sets.get(k, set()))

    def sismember(self, k, v):
        return v in self.sets.get(k, set())

    def exists(self, k):
        return int(k in self.store or bool(self.sets.get(k)))

    def expire(self, k, seconds):
        self.ttls[k] = seconds

    def delete(self, *ks):
        for k in ks:
            self.store.pop(k, None)
            self.sets.pop(k, None)

    def keys(self, pat):
        pre = pat.rstrip("*")
        return [k for k in list(self.store) + list(self.sets) if k.startswith(pre)]

    def search_keys(self):
        return [k for k in self.store if k.startswith("cards:search:")]


def sf(name, oid, type_line="Creature — Elf", ci=(), cmc=2, rarity="common", text="",
       keywords=(), legal="legal", mana="{1}{G}"):
    return {"id": f"sf-{oid}", "oracle_id": oid, "name": name, "type_line": type_line,
            "oracle_text": text, "mana_cost": mana, "cmc": cmc, "rarity": rarity,
            "color_identity": list(ci), "colors": list(ci), "keywords": list(keywords),
            "legalities": {"commander": legal},
            "image_uris": {"art_crop": f"https://img/{oid}-art.jpg",
                           "small": f"https://img/{oid}.jpg"}}


class FakeScryfall:
    """Stands in for httpx.get inside the search service."""

    def __init__(self):
        self.calls = []
        self.pages = {}            # upstream page -> (cards, has_more, total)
        # the is:commander pull; default is a healthy one-page set
        self.eligible_pages = [[sf("Placeholder Commander", "o-placeholder",
                                   "Legendary Creature — Elf")]]
        self.eligible_fail_page = None
        self.fail = False          # whole upstream is down
        self.status = None         # force a non-200 status on card search

    def __call__(self, url, params=None, headers=None, timeout=None):
        params = dict(params or {})
        self.calls.append((url, params))
        if self.fail:
            raise httpx.ConnectError("down")
        qtext = params.get("q", "")
        if qtext == ELIGIBLE_Q:
            page = int(params.get("page", 1))
            if self.eligible_fail_page == page:
                return SimpleNamespace(status_code=500, text="boom", json=lambda: {})
            idx = page - 1
            cards = self.eligible_pages[idx] if idx < len(self.eligible_pages) else []
            body = {"object": "list", "data": cards,
                    "has_more": idx + 1 < len(self.eligible_pages),
                    "total_cards": sum(len(p) for p in self.eligible_pages)}
            return SimpleNamespace(status_code=200, text="", json=lambda: body)
        if self.status:
            return SimpleNamespace(status_code=self.status, text="err", json=lambda: {})
        page = int(params.get("page", 1))
        if page not in self.pages:
            body = {"object": "error", "code": "not_found", "status": 404}
            return SimpleNamespace(status_code=404, text="nf", json=lambda: body)
        cards, has_more, total = self.pages[page]
        body = {"object": "list", "data": cards, "has_more": has_more, "total_cards": total}
        return SimpleNamespace(status_code=200, text="", json=lambda: body)

    def search_calls(self):
        return [c for c in self.calls if c[1].get("q") != ELIGIBLE_Q]

    def eligible_calls(self):
        return [c for c in self.calls if c[1].get("q") == ELIGIBLE_Q]


@pytest.fixture()
def factory():
    eng = create_engine("sqlite://", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield sessionmaker(bind=eng, expire_on_commit=False)
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def redis():
    return FakeRedis()


@pytest.fixture()
def scry(monkeypatch):
    fake = FakeScryfall()
    monkeypatch.setattr(svc.httpx, "get", fake)
    monkeypatch.setattr(svc, "_sleep", lambda s: None)
    return fake


@pytest.fixture()
def client(factory, redis, scry):
    from src.server.app.main import app

    def _db():
        s = factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    app.dependency_overrides[deps.db_session] = _db
    app.dependency_overrides[cards_api.get_redis] = lambda: redis
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _card(oid, name, type_line="Creature — Elf", text="", ci=("G",), cmc=2, keywords=(),
          legal="legal", mana="{1}{G}"):
    return {"oracle_id": oid, "name": name, "type_line": type_line, "oracle_text": text,
            "mana_cost": mana, "colors": list(ci), "color_identity": list(ci), "cmc": cmc,
            "keywords": list(keywords), "legalities": {"commander": legal}}


def seed(factory, cards=(), instances=(), printings=()):
    with session_scope(factory) as s:
        for c in cards:
            repo.upsert_card(s, c)
        for p in printings:
            repo.upsert_printing(s, p)
        for i, (oid, status) in enumerate(instances):
            repo.insert_instance(s, {"id": f"inst-{i}-{oid}", "oracle_id": oid,
                                     "status": status, "finish": "nonfoil"})


def printing(sid, oid, rarity):
    return {"scryfall_id": sid, "oracle_id": oid, "set_code": "tst",
            "collector_number": sid, "rarity": rarity}


def mark_eligible(redis, *oids, names=()):
    redis.sadd(commander_set.SET_KEY, *oids)
    if names:
        redis.sadd(commander_set.NAMES_KEY, *[n.casefold() for n in names])


def get(client, **params):
    return client.get("/api/v2/cards/search", params=params)


# ---------------------------------------------------------------------------
# any / cache / degraded
# ---------------------------------------------------------------------------

def test_any_returns_contract_shape_and_translated_query(client, scry, factory):
    scry.pages[1] = ([sf("Llanowar Elves", "o-llan", ci="G", rarity="common",
                         text="{T}: Add {G}.")], False, 1)
    res = get(client, ci="g", type="creature")
    assert res.status_code == 200
    body = res.json()
    assert body["query"] == f"{BASE} id<=g (t:creature)"
    assert body["source"] == "scryfall" and body["degraded"] is False
    assert body["page"] == 1 and body["has_more"] is False and body["total"] == 1
    hit = body["results"][0]
    assert set(hit) == {"oracle_id", "name", "mana_cost", "cmc", "type_line", "color_identity",
                        "rarity", "oracle_text", "image", "owned_qty", "free_qty",
                        "can_be_commander", "pairing"}
    assert hit["oracle_id"] == "o-llan" and hit["owned_qty"] == 0 and hit["free_qty"] == 0
    assert scry.search_calls()[0][1]["q"] == body["query"]
    assert scry.search_calls()[0][1]["unique"] == "cards"


def test_any_annotates_ownership_from_card_instances(client, scry, factory):
    seed(factory, [_card("o-sol", "Sol Ring", "Artifact", ci=())],
         instances=[("o-sol", "in_collection"), ("o-sol", "in_deck"), ("o-sol", "in_mail"),
                    ("o-sol", "not_owned")])
    scry.pages[1] = ([sf("Sol Ring", "o-sol", "Artifact", ci=()),
                      sf("Mind Stone", "o-mind", "Artifact", ci=())], False, 2)
    hits = {h["name"]: h for h in get(client, type="artifact").json()["results"]}
    # in_collection + in_deck + in_mail; the not_owned placeholder never counts
    assert hits["Sol Ring"]["owned_qty"] == 3
    assert hits["Sol Ring"]["free_qty"] == 1
    assert hits["Mind Stone"]["owned_qty"] == 0


def test_ownership_is_never_cached(client, scry, factory, redis):
    seed(factory, [_card("o-sol", "Sol Ring", "Artifact", ci=())])
    scry.pages[1] = ([sf("Sol Ring", "o-sol", "Artifact", ci=())], False, 1)
    first = get(client, type="artifact").json()
    assert first["results"][0]["owned_qty"] == 0
    # the cached envelope carries card facts only
    (key,) = redis.search_keys()
    assert "owned_qty" not in redis.store[key] and "free_qty" not in redis.store[key]
    seed(factory, instances=[("o-sol", "in_collection")])
    second = get(client, type="artifact").json()
    assert second["source"] == "cache"
    assert len(scry.search_calls()) == 1
    assert second["results"][0]["owned_qty"] == 1 and second["results"][0]["free_qty"] == 1


def test_cache_envelope_ttls_nonempty_24h_empty_60s(client, scry, redis, monkeypatch):
    scry.pages[1] = ([sf("Sol Ring", "o-sol", "Artifact", ci=())], False, 1)
    get(client, type="artifact")
    (key,) = redis.search_keys()
    env = json.loads(redis.store[key])
    assert set(env) >= {"cached_at", "results"}
    # fresh -> cached
    assert get(client, type="artifact").json()["source"] == "cache"
    # 23h later still cached, 25h later refetched
    real = time.time
    monkeypatch.setattr(svc.time, "time", lambda: real() + 23 * 3600)
    assert get(client, type="artifact").json()["source"] == "cache"
    monkeypatch.setattr(svc.time, "time", lambda: real() + 25 * 3600)
    assert get(client, type="artifact").json()["source"] == "scryfall"

    # empty result: Scryfall 404 == "no matches", cached only for 60 s
    monkeypatch.setattr(svc.time, "time", real)
    scry.pages = {}
    body = get(client, oracle="zzzz").json()
    assert body["results"] == [] and body["degraded"] is False and body["source"] == "scryfall"
    assert get(client, oracle="zzzz").json()["source"] == "cache"
    monkeypatch.setattr(svc.time, "time", lambda: real() + 61)
    assert get(client, oracle="zzzz").json()["source"] == "scryfall"


@pytest.mark.parametrize("mode", ["exception", "status"])
def test_scryfall_failure_is_degraded_local_and_never_cached(client, scry, factory, redis, mode):
    seed(factory, [_card("o-llan", "Llanowar Elves", ci=("G",), keywords=[]),
                   _card("o-bolt", "Lightning Bolt", "Instant", ci=("R",))],
         instances=[("o-llan", "in_collection")])
    if mode == "exception":
        scry.fail = True
    else:
        scry.status = 503
    body = get(client, type="creature").json()
    assert body["degraded"] is True and body["source"] == "local"
    assert [h["name"] for h in body["results"]] == ["Llanowar Elves"]
    assert body["results"][0]["owned_qty"] == 1
    assert redis.search_keys() == []
    # recovery: the next healthy request is served from Scryfall, not a stale degraded cache
    scry.fail, scry.status = False, None
    scry.pages[1] = ([sf("Llanowar Elves", "o-llan", ci="G")], False, 1)
    again = get(client, type="creature").json()
    assert again["degraded"] is False and again["source"] == "scryfall"


def test_degraded_local_filters_name_type_oracle_cmc_identity_rarity(client, scry, factory):
    scry.fail = True
    seed(factory, [
        _card("o-a", "Elvish Mystic", "Creature — Elf Druid", "{T}: Add {G}.", ("G",), 1),
        _card("o-b", "Elvish Visionary", "Creature — Elf Shaman", "Draw a card.", ("G",), 2),
        _card("o-c", "Dark Ritual", "Instant", "Add {B}{B}{B}.", ("B",), 1),
        _card("o-d", "Atraxa", "Legendary Creature — Angel", "Flying", ("W", "U", "B", "G"), 4),
    ], printings=[printing("p-a", "o-a", "common"), printing("p-b", "o-b", "rare"),
                  printing("p-d", "o-d", "mythic")])

    def names(**p):
        return sorted(h["name"] for h in get(client, **p).json()["results"])

    assert names(name="elvish") == ["Elvish Mystic", "Elvish Visionary"]
    assert names(type="instant") == ["Dark Ritual"]
    assert names(type="creature", type_text="shaman") == ["Elvish Visionary"]
    assert names(oracle="draw a") == ["Elvish Visionary"]
    assert names(oracle='"draw a"') == ["Elvish Visionary"]
    assert names(mv="1") == ["Dark Ritual", "Elvish Mystic"]
    assert names(mv="4") == ["Atraxa"] and names(mv="7+") == []
    assert names(rarity="rare,mythic") == ["Atraxa", "Elvish Visionary"]
    assert names(ci="g") == ["Elvish Mystic", "Elvish Visionary"]            # within
    assert names(ci="wubg", ci_mode="exact") == ["Atraxa"]
    assert names(ci="g", ci_mode="includes") == ["Atraxa", "Elvish Mystic", "Elvish Visionary"]
    assert names(ci="c") == []


# ---------------------------------------------------------------------------
# owned / not_owned
# ---------------------------------------------------------------------------

def test_owned_mode_is_local_postgres_only_and_never_calls_scryfall(client, scry, factory, redis):
    seed(factory, [_card("o-own", "Llanowar Elves"), _card("o-no", "Elvish Mystic"),
                   _card("o-ph", "Fyndhorn Elves")],
         instances=[("o-own", "in_collection"), ("o-own", "in_deck"), ("o-no", "not_owned")])
    mark_eligible(redis, "o-own")           # set present: no build call either
    body = get(client, owned="owned", type="creature").json()
    assert [h["name"] for h in body["results"]] == ["Llanowar Elves"]
    assert body["results"][0]["owned_qty"] == 2 and body["results"][0]["free_qty"] == 1
    assert body["source"] == "local" and body["degraded"] is False
    assert body["query"] == f"{BASE} (t:creature)"
    assert scry.calls == []
    assert redis.search_keys() == []


def test_not_owned_drops_owned_rows_and_hides_total(client, scry, factory):
    seed(factory, [_card("o-own", "Owned Elf")],
         instances=[("o-own", "in_collection")])
    scry.pages[1] = ([sf("Owned Elf", "o-own"), sf("Free Elf", "o-free")], False, 2)
    body = get(client, owned="not_owned").json()
    assert [h["name"] for h in body["results"]] == ["Free Elf"]
    assert body["total"] is None
    # the owned filter never reaches Scryfall
    assert scry.search_calls()[0][1]["q"] == BASE


def test_not_owned_treats_not_owned_placeholder_as_not_owned(client, scry, factory):
    seed(factory, [_card("o-ph", "Placeholder Elf")], instances=[("o-ph", "not_owned")])
    scry.pages[1] = ([sf("Placeholder Elf", "o-ph")], False, 1)
    assert [h["name"] for h in get(client, owned="not_owned").json()["results"]] == ["Placeholder Elf"]


def test_not_owned_degraded_local_excludes_owned(client, scry, factory):
    scry.fail = True
    seed(factory, [_card("o-own", "Owned Elf"), _card("o-free", "Free Elf")],
         instances=[("o-own", "in_mail")])
    body = get(client, owned="not_owned").json()
    assert body["degraded"] is True
    assert [h["name"] for h in body["results"]] == ["Free Elf"]


# ---------------------------------------------------------------------------
# paging
# ---------------------------------------------------------------------------

def test_paging_maps_pages_onto_scryfall_pages_with_one_upstream_call(client, scry):
    page1 = [sf(f"Card {i:03d}", f"o-{i:03d}") for i in range(175)]
    page2 = [sf(f"Card {i:03d}", f"o-{i:03d}") for i in range(175, 200)]
    scry.pages = {1: (page1, True, 200), 2: (page2, False, 200)}
    p1 = get(client, page=1).json()
    p2 = get(client, page=2).json()
    p3 = get(client, page=3).json()
    p4 = get(client, page=4).json()
    assert [len(p["results"]) for p in (p1, p2, p3, p4)] == [60, 60, 55, 25]
    assert [p["has_more"] for p in (p1, p2, p3, p4)] == [True, True, True, False]
    names = [h["name"] for p in (p1, p2, p3, p4) for h in p["results"]]
    assert names == [f"Card {i:03d}" for i in range(200)]       # no gaps, no repeats
    assert p1["total"] == 200
    # one upstream call per distinct upstream page (the rest is cache)
    assert [c[1]["page"] for c in scry.search_calls()] == [1, 2]


def test_page_size_param_and_bounds(client, scry):
    scry.pages = {1: ([sf(f"Card {i:03d}", f"o-{i:03d}") for i in range(30)], False, 30)}
    assert len(get(client, page_size=10).json()["results"]) == 10
    assert get(client, page_size=101).status_code == 422
    assert get(client, page=0).status_code == 422


# ---------------------------------------------------------------------------
# Can be commander (§3.1)
# ---------------------------------------------------------------------------

def test_cmdr_one_translates_to_is_commander_and_flags_every_hit(client, scry, redis):
    scry.pages[1] = ([sf("Atraxa", "o-atraxa", "Legendary Creature — Angel", ci="WUBG")], False, 1)
    body = get(client, cmdr=1).json()
    assert "is:commander" in body["query"]
    assert body["results"][0]["can_be_commander"] is True
    assert scry.eligible_calls() == []           # Scryfall already decided eligibility


def test_cmdr_absent_flags_hits_from_eligible_set_built_from_scryfall(client, scry, redis):
    scry.pages[1] = ([sf("Atraxa", "o-atraxa", "Legendary Creature — Angel"),
                      sf("Sol Ring", "o-sol", "Artifact")], False, 2)
    scry.eligible_pages = [[sf("Atraxa", "o-atraxa", "Legendary Creature — Angel")]]
    body = get(client, name="a").json()
    assert "is:commander" not in body["query"]
    flags = {h["name"]: h["can_be_commander"] for h in body["results"]}
    assert flags == {"Atraxa": True, "Sol Ring": False}
    assert redis.sismember(commander_set.SET_KEY, "o-atraxa")
    assert redis.ttls[commander_set.SET_KEY] == 24 * 3600
    # built once, then reused
    get(client, name="b")
    assert len(scry.eligible_calls()) == 1


def test_partial_eligible_set_pull_is_never_written(client, scry, factory, redis):
    seed(factory, [_card("o-sol", "Sol Ring", "Artifact", ci=()),
                   _card("o-atraxa", "Atraxa", "Legendary Creature — Angel", ci=("W", "U", "B", "G"))],
         instances=[("o-sol", "in_collection"), ("o-atraxa", "in_collection")])
    scry.eligible_pages = [[sf("Atraxa", "o-atraxa")], [sf("Other", "o-other")]]
    scry.eligible_fail_page = 2
    body = get(client, owned="owned", cmdr=1).json()
    assert not redis.exists(commander_set.SET_KEY)
    assert not redis.sets.get(commander_set.SET_KEY)
    assert not redis.sets.get(commander_set.NAMES_KEY)
    # predicate fallback answered, and says so
    assert body["degraded"] is True
    assert [h["name"] for h in body["results"]] == ["Atraxa"]
    # the failed attempt is not retried on every request
    n = len(scry.eligible_calls())
    get(client, owned="owned", cmdr=1)
    assert len(scry.eligible_calls()) == n


def test_owned_cmdr_uses_the_eligible_set_for_vehicles_backgrounds_brisela(client, scry, factory, redis):
    seed(factory, [
        _card("o-esika", "Esika's Chariot", "Legendary Artifact — Vehicle", "Crew 4", ("G",), 4),
        _card("o-bg", "Agent of the Iron Throne", "Legendary Enchantment — Background",
              "Commander creatures you own have ...", ("B",), 3),
        _card("o-bris", "Brisela, Voice of Nightmares", "Legendary Creature — Eldrazi Angel",
              "Flying", ("W",), 9),
        _card("o-elf", "Llanowar Elves", "Creature — Elf Druid", "{T}: Add {G}.", ("G",), 1),
    ], instances=[("o-esika", "in_collection"), ("o-bg", "in_deck"),
                  ("o-bris", "in_collection"), ("o-elf", "in_collection")])
    mark_eligible(redis, "o-esika", "o-bg")          # Brisela/Elf are not in Scryfall's set
    body = get(client, owned="owned", cmdr=1).json()
    assert sorted(h["name"] for h in body["results"]) == [
        "Agent of the Iron Throne", "Esika's Chariot"]
    assert all(h["can_be_commander"] for h in body["results"])
    assert body["degraded"] is False and scry.calls == []
    # without cmdr every owned card is returned, flagged by the set
    flags = {h["name"]: h["can_be_commander"]
             for h in get(client, owned="owned").json()["results"]}
    assert flags["Brisela, Voice of Nightmares"] is False
    assert flags["Esika's Chariot"] is True


def test_degraded_cmdr_filter_uses_set_when_present(client, scry, factory, redis):
    scry.fail = True
    seed(factory, [_card("o-a", "Legend A", "Legendary Creature — Elf"),
                   _card("o-b", "Legend B", "Legendary Creature — Elf")])
    mark_eligible(redis, "o-a")
    body = get(client, cmdr=1).json()
    assert body["degraded"] is True
    assert [h["name"] for h in body["results"]] == ["Legend A"]
    assert scry.eligible_calls() == []            # no rebuild attempt while degraded


# ---------------------------------------------------------------------------
# Pairing (§3.1.3)
# ---------------------------------------------------------------------------

PAIR_CASES = [
    # (id, first-card oracle text/type, expected clause fragment)
    ("plain-partner", "Legendary Creature — Human", "Partner", 'o:/^partner$/'),
    ("partner-group", "Legendary Creature — Human", "Partner\u2014Survivors",
     'o:"partner\u2014survivors"'),
    ("partner-with", "Legendary Creature — Human", "Partner with Rowan Kenrith",
     '!"Rowan Kenrith"'),
    ("friends-forever", "Legendary Creature — Human", "Friends forever", 'o:"friends forever"'),
    ("choose-background", "Legendary Creature — Human", "Choose a Background", "t:background"),
    ("is-background", "Legendary Enchantment — Background", "Commander creatures you own have x.",
     'kw:"choose a background"'),
    ("doctors-companion", "Legendary Creature — Human", "Doctor's companion", "t:doctor"),
    ("is-doctor", "Legendary Creature — Time Lord Doctor", "Whatever.",
     'kw:"doctor\'s companion"'),
]


@pytest.mark.parametrize("case_id, type_line, text, fragment", PAIR_CASES,
                         ids=[c[0] for c in PAIR_CASES])
def test_pair_clause_per_row(client, scry, factory, case_id, type_line, text, fragment):
    seed(factory, [_card("o-first", "First Commander", type_line, text, ("G",))])
    scry.pages[1] = ([], False, 0)
    body = get(client, cmdr=1, pair="o-first").json()
    assert "is:commander" in body["query"]
    assert fragment in body["query"]
    assert '-!"First Commander"' in body["query"]          # excluded from its own results


def test_pair_without_cmdr_is_400(client, factory):
    seed(factory, [_card("o-first", "Akiri", "Legendary Creature — Kor", "Partner")])
    res = get(client, pair="o-first")
    assert res.status_code == 400


def test_pair_first_card_without_pairing_ability_is_400(client, factory):
    seed(factory, [_card("o-first", "Atraxa", "Legendary Creature — Angel", "Flying")])
    res = get(client, cmdr=1, pair="o-first")
    assert res.status_code == 400
    assert "can't have a partner" in json.dumps(res.json())


def test_pair_unknown_first_card_is_404(client, scry):
    scry.pages = {}                 # Scryfall lookup by oracle id finds nothing either
    assert get(client, cmdr=1, pair="nope").status_code == 404


def test_pair_first_card_resolved_via_scryfall_when_not_in_postgres(client, scry):
    first = sf("Akiri, Line-Slinger", "o-akiri", "Legendary Creature — Kor Soldier",
               ci="RW", text="Partner")
    scry.pages[1] = ([first], False, 1)
    body = get(client, cmdr=1, pair="o-akiri")
    assert body.status_code == 200
    assert scry.search_calls()[0][1]["q"] == "oracleid:o-akiri"
    assert 'o:/^partner$/' in body.json()["query"]


def test_plain_partner_never_returns_a_partner_group_card(client, scry, factory):
    seed(factory, [_card("o-first", "Akiri", "Legendary Creature — Kor", "Partner")])
    partner = sf("Silas Renn", "o-silas", "Legendary Creature — Human", text="Partner", ci="UB")
    group = sf("Abby, Merciless Medic", "o-abby", "Legendary Creature — Human",
               text="Partner\u2014Survivors", ci="W")
    scry.pages[1] = ([partner, group], False, 2)
    hits = get(client, cmdr=1, pair="o-first").json()["results"]
    assert [h["name"] for h in hits] == ["Silas Renn"]
    first = CardInfo.from_record({"name": "Akiri", "type_line": "Legendary Creature — Kor",
                                  "oracle_text": "Partner"})
    for h in hits:
        other = CardInfo.from_record({"name": h["name"], "type_line": h["type_line"],
                                      "oracle_text": h["oracle_text"]})
        assert check_partner_pair(first, other) is None


def test_pair_partner_with_requires_the_named_card_to_name_back(client, scry, factory):
    seed(factory, [_card("o-first", "Pir, Imaginative Rascal", "Legendary Creature — Human",
                         "Partner with Toothy, Imaginary Friend")])
    good = sf("Toothy, Imaginary Friend", "o-toothy", "Legendary Creature — Illusion",
              text="Partner with Pir, Imaginative Rascal")
    imposter = sf("Toothy, Imaginary Friend", "o-toothy2", "Legendary Creature — Illusion",
                  text="Flying")
    scry.pages[1] = ([good, imposter], False, 2)
    hits = get(client, cmdr=1, pair="o-first").json()["results"]
    assert [h["oracle_id"] for h in hits] == ["o-toothy"]


def test_pair_total_is_hidden_and_first_card_is_dropped(client, scry, factory):
    seed(factory, [_card("o-first", "Akiri", "Legendary Creature — Kor", "Partner")])
    self_hit = sf("Akiri", "o-first", "Legendary Creature — Kor", text="Partner")
    other = sf("Silas Renn", "o-silas", "Legendary Creature — Human", text="Partner")
    scry.pages[1] = ([self_hit, other], False, 2)
    body = get(client, cmdr=1, pair="o-first").json()
    assert [h["name"] for h in body["results"]] == ["Silas Renn"]
    assert body["total"] is None


def test_pairing_label_set_on_every_hit_with_cmdr_off(client, scry, redis):
    mark_eligible(redis, "o-akiri")
    scry.pages[1] = ([
        sf("Akiri", "o-akiri", "Legendary Creature — Kor", text="Partner"),
        sf("Abby", "o-abby", "Legendary Creature — Human", text="Partner\u2014Survivors"),
        sf("Pir", "o-pir", "Legendary Creature — Human", text="Partner with Toothy"),
        sf("Wyll", "o-wyll", "Legendary Creature — Human", text="Choose a Background"),
        sf("Agent", "o-agent", "Legendary Enchantment — Background", text="x"),
        sf("Doc", "o-doc", "Legendary Creature — Time Lord Doctor", text="x"),
        sf("Atraxa", "o-atraxa", "Legendary Creature — Angel", text="Flying"),
    ], False, 7)
    res = {h["name"]: h for h in get(client, name="x").json()["results"]}
    assert res["Akiri"]["pairing"] == "partner"
    assert res["Abby"]["pairing"] == "partner_group"
    assert res["Pir"]["pairing"] == "partner_with"
    assert res["Wyll"]["pairing"] == "choose_background"
    assert res["Agent"]["pairing"] == "background"
    assert res["Doc"]["pairing"] == "doctor"
    assert res["Atraxa"]["pairing"] is None
    assert all("can_be_commander" in h for h in res.values())


def test_owned_mode_pair_runs_locally(client, scry, factory, redis):
    seed(factory, [
        _card("o-first", "Akiri", "Legendary Creature — Kor", "Partner"),
        _card("o-silas", "Silas Renn", "Legendary Creature — Human", "Partner"),
        _card("o-abby", "Abby", "Legendary Creature — Human", "Partner\u2014Survivors"),
        _card("o-atraxa", "Atraxa", "Legendary Creature — Angel", "Flying"),
    ], instances=[(o, "in_collection") for o in ("o-first", "o-silas", "o-abby", "o-atraxa")])
    mark_eligible(redis, "o-first", "o-silas", "o-abby", "o-atraxa")
    hits = get(client, owned="owned", cmdr=1, pair="o-first").json()["results"]
    assert [h["name"] for h in hits] == ["Silas Renn"]
    assert scry.calls == []


# ---------------------------------------------------------------------------
# Autocomplete: oracle_id + commander-scope local fallback (§5.3, §3.1.2)
# ---------------------------------------------------------------------------

def test_autocomplete_shape_carries_oracle_id():
    from src.server.domain.inventory import card_search as cs
    row = cs._shape_card({"id": "print-1", "oracle_id": "oracle-1", "name": "Sol Ring"})
    assert row["oracle_id"] == "oracle-1" and row["uid"] == "print-1"
    dfc = cs._shape_card({"id": "p", "name": "A // B",
                          "card_faces": [{"oracle_id": "face-oracle", "image_uris": {}}]})
    assert dfc["oracle_id"] == "face-oracle"
    assert cs._shape_local("Sol Ring", {})["oracle_id"] == ""


def test_autocomplete_commander_fallback_prefers_eligible_set(monkeypatch):
    from src.server.domain.inventory import card_search as cs
    r = FakeRedis()
    r.set("card_meta:Esika's Chariot", json.dumps({"type": "Legendary Artifact — Vehicle"}))
    r.set("card_meta:Esika's Other", json.dumps({"type": "Legendary Creature — Elf"}))
    mark_eligible(r, "o-esika", names=["Esika's Chariot"])
    out = cs._local_matches(r, "esika", 8, legendary_only=True)
    assert [c["name"] for c in out] == ["Esika's Chariot"]


def test_autocomplete_commander_fallback_predicate_when_set_missing():
    from src.server.domain.inventory import card_search as cs
    r = FakeRedis()
    r.set("card_meta:Esika's Chariot", json.dumps({"type": "Legendary Artifact — Vehicle"}))
    r.set("card_meta:Esika's Wall", json.dumps({"type": "Creature — Wall"}))
    r.set("card_meta:Agent of the Iron Throne",
          json.dumps({"type": "Legendary Enchantment — Background"}))
    out = cs._local_matches(r, "e", 8, legendary_only=True)
    assert sorted(c["name"] for c in out) == ["Agent of the Iron Throne", "Esika's Chariot"]
