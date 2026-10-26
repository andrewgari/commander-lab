"""Slice 2 tests: full collection migration (Redis -> Postgres), inventory
read model, profile overrides + snapshot history.

Written as unittest.TestCase so CI's ``python -m unittest discover -s tests``
collects every case (bare pytest functions are invisible to it). Runs on
in-memory SQLite with a stub Redis client — no Postgres, Redis, or network.
"""
from __future__ import annotations

import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.server.domain.decks import legacy_import
from src.server.domain.profile import overrides as ov
from src.server.domain.profile import power as power_mod
from src.server.domain.profile import snapshot as snap
from src.server.infra.db import repositories as repo
from src.server.infra.db.orm_models import Base, CardInstanceRow
from src.server.infra.db.session import session_scope
from src.server.infra.services import collection_migration as cm
from src.server.infra.services import profile_service


# ---------------------------------------------------------------------------
# Fixtures: stub redis + sqlite factory
# ---------------------------------------------------------------------------

class StubRedis:
    """Minimal stand-in for the redis client the migration reads (get/scan_iter/exists)."""

    def __init__(self, store: dict):
        self.store = {k: (v if isinstance(v, bytes) else str(v)) for k, v in store.items()}

    def get(self, key):
        return self.store.get(key)

    def scan_iter(self, match="*", count=500):
        import fnmatch

        for key in sorted(self.store):
            if fnmatch.fnmatch(key, match):
                yield key

    def exists(self, key):
        return key in self.store


def make_factory():
    eng = create_engine("sqlite://", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)


def _deck_entry(deck_id=5054882, name="The Return of the King", status="physical"):
    return {
        "id": deck_id,
        "name": name,
        "color": "WURG",
        "commanders": ["Aragorn, the Uniter"],
        "status": status,
        "source": "archidekt",
        "source_id": str(deck_id),
        "registry_id": f"archidekt:{deck_id}",
        "cards": [
            {"name": "Aragorn, the Uniter", "quantity": 1},
            {"name": "Forest", "quantity": 10},
            {"name": "Sol Ring", "quantity": 1},
        ],
    }


def _archidekt_payload(deck_id=5054882, name="The Return of the King"):
    def entry(card_name, oracle_id, qty=1, categories=None, cmc=3.0):
        return {
            "quantity": qty,
            "categories": categories or [],
            "card": {
                "uid": f"sf-{oracle_id}",
                "collectorNumber": "1",
                "rarity": "rare",
                "edition": {"editioncode": "ltr"},
                "prices": {"usd": 1.0},
                "oracleCard": {
                    "name": card_name,
                    "uid": oracle_id,
                    "types": ["Creature"],
                    "superTypes": [],
                    "subTypes": [],
                    "cmc": cmc,
                    "manaCost": "{2}{G}",
                    "colors": ["Green"],
                    "colorIdentity": ["Green"],
                    "text": "",
                    "keywords": [],
                },
            },
        }

    return {
        "id": deck_id,
        "name": name,
        "parentFolderId": 588380,
        "cards": [
            entry("Aragorn, the Uniter", "o-cmd", categories=["Commander"], cmc=7.0),
            entry("Forest", "o-forest", qty=10, categories=["Land"], cmc=0),
            entry("Sol Ring", "o-ring", categories=["Ramp"], cmc=1.0),
        ],
    }


def _instance(inst_id, name="Sol Ring", oracle_id="o-ring", status="in_collection",
              deck_id=None, acquired="2024-05-01T00:00:00Z", created="2024-05-01T00:00:00Z"):
    return {
        "id": inst_id,
        "card_name": name,
        "oracle_id": oracle_id,
        "ownership_status": status,
        "deck_id": deck_id,
        "date_acquired": acquired,
        "created_at": created,
        "finish": "nonfoil",
        "condition": "NM",
        "notes": "",
        "set": "ltr",
        "collector_number": "1",
        "price_paid": 1.5,
        "source": "trade",
    }


def stub_redis(decks=None, instances=None, statuses=None, name_idx=None):
    store = {"decks": json.dumps(decks or [])}
    for key, value in (statuses or {}).items():
        store[f"deck_status:{key}"] = value
    for name, oid in (name_idx or {}).items():
        store[f"idx:card_name_to_oracle:{name}"] = oid
    for rec in instances or []:
        store[f"instance:{rec['id']}"] = json.dumps(rec)
    return StubRedis(store)


# ---------------------------------------------------------------------------
# domain: legacy status mapping + redis deck fallback
# ---------------------------------------------------------------------------

class LegacyStatusTests(unittest.TestCase):
    def test_known_mappings(self):
        self.assertEqual(legacy_import.map_legacy_status("physical"), ("have", False, None))
        self.assertEqual(legacy_import.map_legacy_status("digital"), ("virtual", False, None))
        self.assertEqual(legacy_import.map_legacy_status("testing"), ("pending", False, None))

    def test_retired_archives(self):
        status, archived, flag = legacy_import.map_legacy_status("retired")
        self.assertEqual((status, archived, flag), ("virtual", True, None))

    def test_unknown_never_guesses_have(self):
        status, archived, flag = legacy_import.map_legacy_status("weird")
        self.assertEqual(status, "virtual")
        self.assertFalse(archived)
        self.assertIn("unknown_status:weird", flag)


class RedisDeckNormalizeTests(unittest.TestCase):
    def test_commander_board_and_resolution(self):
        resolver = {"Aragorn, the Uniter": "o-cmd", "Forest": "o-forest",
                    "Sol Ring": "o-ring"}.get
        norm = legacy_import.normalize_redis_deck(_deck_entry(), resolver)
        self.assertEqual(norm["id"], "archidekt:5054882")
        boards = {c["name"]: c["board"] for c in norm["cards"]}
        self.assertEqual(boards["Aragorn, the Uniter"], "commander")
        self.assertEqual(boards["Forest"], "main")
        self.assertEqual(norm["unresolved"], [])
        self.assertEqual(sum(c["qty"] for c in norm["cards"]), 12)

    def test_unresolved_names_counted_not_dropped(self):
        resolver = {"Forest": "o-forest"}.get
        norm = legacy_import.normalize_redis_deck(_deck_entry(), resolver)
        self.assertEqual(sorted(norm["unresolved"]), ["Aragorn, the Uniter", "Sol Ring"])
        self.assertEqual([c["name"] for c in norm["cards"]], ["Forest"])


# ---------------------------------------------------------------------------
# domain: power metric
# ---------------------------------------------------------------------------

class PowerMetricTests(unittest.TestCase):
    def test_vanilla_deck_low_bracket(self):
        deck_cards = [
            {"name": "Llanowar Elves", "board": "main", "qty": 4},
            {"name": "Forest", "board": "main", "qty": 30},
            {"name": "Some Commander", "board": "commander", "qty": 1},
        ]
        power = power_mod.estimate_power(deck_cards)
        self.assertTrue(power["available"])
        self.assertEqual(power["estimated_bracket"], 2)  # min 1, no target -> 2
        self.assertEqual(power["game_changers"], 0)
        self.assertEqual(power["power_source"], "bracket_band")

    def test_game_changers_raise_the_floor(self):
        deck_cards = [
            {"name": "Rhystic Study", "board": "main", "qty": 1},
            {"name": "Cyclonic Rift", "board": "main", "qty": 1},
            {"name": "Forest", "board": "main", "qty": 30},
        ]
        power = power_mod.estimate_power(deck_cards)
        self.assertTrue(power["available"])
        self.assertEqual(power["game_changers"], 2)
        self.assertGreaterEqual(power["estimated_bracket"], 3)

    def test_empty_deck_unavailable_with_reason(self):
        power = power_mod.estimate_power([])
        self.assertFalse(power["available"])
        self.assertTrue(power["reason"])

    def test_deterministic(self):
        deck_cards = [{"name": "Sol Ring", "board": "main", "qty": 1}]
        self.assertEqual(power_mod.estimate_power(deck_cards),
                         power_mod.estimate_power(deck_cards))

    def test_snapshot_carries_power(self):
        out = snap.build_snapshot(
            {"id": "d", "name": "D", "color_identity": ["G"], "commanders": []},
            [{"name": "Forest", "board": "main", "qty": 40, "type_line": "Land",
              "cmc": 0, "category": "Land"}],
            {"totals": {"need": 40, "have": 40, "pending": 0, "buy": 0}},
            {"instances": 40},
            power=power_mod.estimate_power([{"name": "Forest", "board": "main", "qty": 40}]),
        )
        self.assertTrue(out["metrics"]["power"]["available"])
        self.assertIn("bracket", out["summary_text"])


# ---------------------------------------------------------------------------
# domain: overrides
# ---------------------------------------------------------------------------

class OverrideDomainTests(unittest.TestCase):
    def test_validate_known_and_unknown(self):
        self.assertEqual(ov.validate("power_level_target", 7), 7.0)
        self.assertEqual(ov.validate("power_level_target", None), None)
        with self.assertRaises(ov.OverrideError):
            ov.validate("nonsense", 1)
        with self.assertRaises(ov.OverrideError):
            ov.validate("power_level_target", 42)
        with self.assertRaises(ov.OverrideError):
            ov.validate("archetype_focus", "")

    def test_apply_overlays_never_rewrites_metrics(self):
        metrics = {"curve": {"lands": 36}}
        merged = ov.apply_overrides(metrics, {"power_level_target": {"value": 7.0},
                                              "archetype_focus": {"value": None}})
        self.assertEqual(merged["curve"], {"lands": 36})
        self.assertEqual(merged["overrides"], {"power_level_target": 7.0})
        self.assertEqual(metrics, {"curve": {"lands": 36}})  # input untouched

    def test_override_snapshot_without_latest_is_labelled(self):
        out = ov.override_snapshot(None, {"power_level_target": {"value": 5.0}},
                                   "power_level_target")
        self.assertIn("unavailable", out["metrics"])
        self.assertIn("target power level", out["summary_text"])
        self.assertFalse(out["inputs"]["based_on_latest_snapshot"])


# ---------------------------------------------------------------------------
# migration service: dry-run / apply / verify
# ---------------------------------------------------------------------------

NAME_IDX = {"Aragorn, the Uniter": "o-cmd", "Forest": "o-forest", "Sol Ring": "o-ring"}


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.factory = make_factory()

    def _redis(self, decks, instances, statuses=None):
        return stub_redis(decks=decks, instances=instances, statuses=statuses,
                          name_idx=NAME_IDX)

    def test_dry_run_counts_per_family(self):
        r = self._redis(
            decks=[_deck_entry(1, "A", "physical"), _deck_entry(2, "B", "testing")],
            instances=[_instance("i1"), _instance("i2", status="in_mail")],
            statuses={"1": "physical"},
        )
        report = cm.dry_run(r)
        fam = report["families"]
        self.assertEqual(fam["decks"]["redis"], 2)
        self.assertEqual(fam["decks"]["planned_method"], {"archidekt_live": 2})
        self.assertEqual(fam["decks"]["status_values"], {"physical": 1, "testing": 1})
        self.assertEqual(fam["deck_status"]["redis_keys"], 1)
        self.assertEqual(fam["instances"]["redis_records"], 2)
        self.assertEqual(fam["instances"]["by_ownership_status"],
                         {"in_collection": 1, "in_mail": 1})
        self.assertTrue(report["ok"])

    def test_dry_run_flags_unresolved_instances(self):
        bad = _instance("i9", name="Ghost Card", oracle_id=None)
        r = self._redis(decks=[], instances=[bad])
        report = cm.dry_run(r)
        self.assertFalse(report["ok"])
        self.assertEqual(report["families"]["instances"]["unresolved"], ["Ghost Card"])

    def test_apply_live_then_verify(self):
        decks = [_deck_entry(5054882, "ROTK", "physical"),
                 _deck_entry(42, "Brew", "testing")]
        instances = [
            _instance("i1", status="in_deck", deck_id=5054882),
            _instance("i2", status="in_collection"),
            _instance("i3", status="in_mail", acquired=""),
        ]
        statuses = {"5054882": "physical", "42": "testing"}
        r = self._redis(decks, instances, statuses)

        payloads = {"5054882": _archidekt_payload(5054882, "ROTK")}

        def fetcher(identifier):
            if identifier in payloads:
                return payloads[identifier]
            raise RuntimeError("upstream 404")  # deck 42 exercises the fallback

        report = cm.apply(r, self.factory, fetcher=fetcher)
        self.assertEqual(report["deck_methods"], {"archidekt_live": 1, "redis_fallback": 1})
        self.assertEqual(report["instances"]["inserted"], 3)

        with session_scope(self.factory) as s:
            d1 = repo.get_deck(s, "archidekt:5054882")
            d2 = repo.get_deck(s, "archidekt:42")
            self.assertEqual(d1["status"], "have")      # physical
            self.assertEqual(d2["status"], "pending")   # testing
            self.assertEqual(len(repo.get_deck_cards(s, "archidekt:5054882")), 3)
            self.assertEqual(len(repo.get_deck_cards(s, "archidekt:42")), 3)

            i1 = [i for i in repo.list_instances(s) if i["id"] == "i1"][0]
            self.assertEqual(i1["deck_id"], "archidekt:5054882")
            # provenance seeded: acquired (dated) + assigned + migrated
            kinds = sorted(
                row.kind for row in s.query(repo.InstanceEventRow)
                .filter_by(instance_id="i1").all()
            ) if hasattr(repo, "InstanceEventRow") else None
        with session_scope(self.factory) as s:
            counts = repo.event_counts_by_instance(s, ["i1", "i2", "i3"])
            self.assertEqual(counts["i1"], 3)  # acquired + assigned + migrated
            self.assertEqual(counts["i2"], 2)  # acquired + migrated
            self.assertEqual(counts["i3"], 1)  # migrated only (no acquired date)

        v = cm.verify(r, self.factory)
        self.assertTrue(v["ok"], json.dumps(v, indent=2, default=str))
        self.assertEqual(v["families"]["instances"]["redis_resolvable"], 3)
        self.assertEqual(v["families"]["instances"]["postgres"], 3)

    def test_apply_is_idempotent(self):
        decks = [_deck_entry(5054882)]
        instances = [_instance("i1", status="in_deck", deck_id=5054882)]
        r = self._redis(decks, instances, statuses=None)
        fetcher = lambda identifier: _archidekt_payload()
        cm.apply(r, self.factory, fetcher=fetcher)
        cm.apply(r, self.factory, fetcher=fetcher)  # second full run
        with session_scope(self.factory) as s:
            self.assertEqual(len(repo.list_instances(s)), 1)
            self.assertEqual(repo.event_counts_by_instance(s, ["i1"])["i1"], 3)
            self.assertEqual(len(repo.list_snapshots(s, limit=100)), 1)

    def test_verify_catches_missing_instance(self):
        r = self._redis([_deck_entry()], [_instance("i1")])
        cm.apply(r, self.factory, fetcher=lambda _: _archidekt_payload())
        with session_scope(self.factory) as s:
            row = s.get(CardInstanceRow, "i1")
            s.delete(row)
        v = cm.verify(r, self.factory)
        self.assertFalse(v["ok"])
        self.assertEqual(v["families"]["instances"]["mismatches"][0]["redis"], 1)
        self.assertEqual(v["families"]["instances"]["mismatches"][0]["postgres"], 0)

    def test_retired_deck_is_archived(self):
        r = self._redis([_deck_entry(7, "Old", "retired")], [], statuses=None)
        cm.apply(r, self.factory, fetcher=lambda _: _archidekt_payload(7, "Old"))
        with session_scope(self.factory) as s:
            deck = repo.get_deck(s, "archidekt:7")
            self.assertEqual(deck["status"], "virtual")
            self.assertIsNotNone(deck["archived_at"])
        v = cm.verify(r, self.factory)
        self.assertTrue(v["ok"], json.dumps(v, indent=2, default=str))

    def test_deck_status_key_wins_over_array(self):
        entry = _deck_entry(9, "Flip", "testing")
        r = self._redis([entry], [], statuses={"9": "physical"})
        cm.apply(r, self.factory, fetcher=lambda _: _archidekt_payload(9, "Flip"))
        with session_scope(self.factory) as s:
            self.assertEqual(repo.get_deck(s, "archidekt:9")["status"], "have")

    def test_reimport_preserves_lifecycle_status(self):
        """A later live re-import must not clobber the migrated 'pending'
        back to the folder-rule 'virtual'."""
        r = self._redis([_deck_entry(11, "WIP", "testing")], [], statuses=None)
        payload = _archidekt_payload(11, "WIP")
        payload["parentFolderId"] = 1  # not the physical folder
        cm.apply(r, self.factory, fetcher=lambda _: payload)
        with session_scope(self.factory) as s:
            self.assertEqual(repo.get_deck(s, "archidekt:11")["status"], "pending")
        # simulate a card change so the re-import does real work
        payload["cards"].append(payload["cards"][1])
        from src.server.infra.services import import_service
        import_service.import_archidekt_deck("11", session_factory=self.factory,
                                             fetcher=lambda _: payload)
        with session_scope(self.factory) as s:
            self.assertEqual(repo.get_deck(s, "archidekt:11")["status"], "pending")


# ---------------------------------------------------------------------------
# repositories: overrides
# ---------------------------------------------------------------------------

class OverrideRepoTests(unittest.TestCase):
    def setUp(self):
        self.factory = make_factory()

    def test_upsert_and_get(self):
        with session_scope(self.factory) as s:
            row = repo.upsert_override(s, "power_level_target", 7.0)
            self.assertEqual(row["value"], 7.0)
            row = repo.upsert_override(s, "power_level_target", 8.0)
            self.assertEqual(row["value"], 8.0)
            all_rows = repo.get_overrides(s)
            self.assertEqual(list(all_rows), ["power_level_target"])


# ---------------------------------------------------------------------------
# /api/v2 routers
# ---------------------------------------------------------------------------

class ApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from src.server.app.api import deps
        from src.server.app.main import app

        self.factory = make_factory()
        cm.apply(
            stub_redis(decks=[_deck_entry()], instances=[_instance("i1")],
                       name_idx=NAME_IDX),
            self.factory, fetcher=lambda _: _archidekt_payload(),
        )

        def _override():
            s = self.factory()
            try:
                yield s
                s.commit()
            finally:
                s.close()

        app.dependency_overrides[deps.db_session] = _override
        self.app = app
        self.client = TestClient(app)
        self.addCleanup(app.dependency_overrides.clear)

    def test_inventory_endpoint(self):
        r = self.client.get("/api/v2/inventory")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["totals"]["cards"], 1)
        self.assertEqual(body["totals"]["owned"], 1)
        row = body["inventory"][0]
        self.assertEqual(row["name"], "Sol Ring")
        self.assertEqual(row["in_collection"], 1)
        r = self.client.get("/api/v2/inventory", params={"q": "no such card"})
        self.assertEqual(r.json()["inventory"], [])

    def test_profile_history_browsable(self):
        r = self.client.get("/api/v2/profile/history")
        self.assertEqual(r.status_code, 200)
        snaps = r.json()["snapshots"]
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0]["trigger"], "import")
        self.assertTrue(snaps[0]["metrics"]["power"]["available"])

    def test_override_write_appends_snapshot(self):
        r = self.client.put("/api/v2/profile/overrides/power_level_target",
                            json={"value": 7})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["override"]["value"], 7.0)
        snap_id = r.json()["snapshot_id"]

        r = self.client.get("/api/v2/profile/history")
        snaps = r.json()["snapshots"]
        self.assertEqual(snaps[0]["id"], snap_id)
        self.assertEqual(snaps[0]["trigger"], "override")
        self.assertEqual(snaps[0]["metrics"]["overrides"], {"power_level_target": 7.0})
        # history keeps both the import and the override snapshot
        self.assertEqual(len(snaps), 2)

        # effective profile: latest snapshot reflects the override
        r = self.client.get("/api/v2/profile/latest")
        self.assertEqual(r.json()["snapshot"]["id"], snap_id)

        # overrides listing
        r = self.client.get("/api/v2/profile/overrides")
        self.assertEqual(r.json()["overrides"]["power_level_target"]["value"], 7.0)
        self.assertIn("power_level_target", r.json()["known_keys"])

    def test_override_clear_and_invalid(self):
        self.client.put("/api/v2/profile/overrides/curve_avg_mv_target", json={"value": 3.5})
        r = self.client.put("/api/v2/profile/overrides/curve_avg_mv_target",
                            json={"value": None})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["override"]["value"])
        r = self.client.put("/api/v2/profile/overrides/bogus_key", json={"value": 1})
        self.assertEqual(r.status_code, 422)
        r = self.client.put("/api/v2/profile/overrides/power_level_target",
                            json={"value": 99})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
