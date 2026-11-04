"""Unit tests for collection.py — reconciling an Archidekt collection into
card instances. Uses the same in-memory FakeRedis stand-in as
tests/test_instances.py; no network, no real Redis.

Run: python tests/test_collection_import.py
"""
import os
import sys
import json
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.server.domain.inventory.collection as collection
import src.server.domain.inventory.instances as instance_store


class FakeRedis:
    """Minimal in-memory stand-in for the redis calls instances.py makes."""

    def __init__(self):
        self.store = {}
        self.sets = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value

    def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)

    def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    def srem(self, key, member):
        self.sets.get(key, set()).discard(member)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def sinter(self, *keys):
        sets = [self.sets.get(k, set()) for k in keys]
        if not sets:
            return set()
        result = sets[0]
        for s in sets[1:]:
            result = result & s
        return result

    def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in self.store if k.startswith(prefix)]

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, parent):
        self.parent = parent
        self.ops = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.ops.append((name, args, kwargs))
            return self
        return call

    def execute(self):
        for name, args, kwargs in self.ops:
            getattr(self.parent, name)(*args, **kwargs)
        self.ops = []


def row(name="Sol Ring", quantity=1, **kw):
    base = {
        "card_name": name,
        "quantity": quantity,
        "set": "C21",
        "set_name": "Commander 2021",
        "collector_number": "263",
        "finish": "nonfoil",
        "condition": "NM",
        "language": "EN",
        "archidekt_uid": "uid-1",
        "oracle_uid": "oracle-1",
        "date_acquired": "2025-01-02",
        "price_paid": 1.5,
        "tags": [],
    }
    base.update(kw)
    return base


class CollectionTestBase(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def owned(self, card_name):
        return [
            i for i in instance_store.list_instances(self.r, card_name=card_name)
            if i["ownership_status"] in collection.OWNED_STATUSES
        ]


class TestReconcileCreates(CollectionTestBase):
    def test_quantity_becomes_one_instance_per_copy(self):
        report = collection.reconcile(self.r, [row(quantity=3)])
        self.assertEqual(report["created"], 3)
        self.assertEqual(len(self.owned("Sol Ring")), 3)

    def test_created_instances_are_in_collection_with_printing_details(self):
        collection.reconcile(self.r, [row()])
        inst = self.owned("Sol Ring")[0]
        self.assertEqual(inst["ownership_status"], "in_collection")
        self.assertEqual(inst["set"], "C21")
        self.assertEqual(inst["set_name"], "Commander 2021")
        self.assertEqual(inst["collector_number"], "263")
        self.assertEqual(inst["finish"], "nonfoil")
        self.assertEqual(inst["condition"], "NM")
        self.assertEqual(inst["archidekt_uid"], "uid-1")
        self.assertEqual(inst["source"], "archidekt_collection")
        self.assertIsNone(inst["deck_id"])

    def test_distinct_printings_of_same_card_are_separate_instances(self):
        rows = [
            row(set="C21", collector_number="263"),
            row(set="LTC", collector_number="1"),
            row(set="C21", collector_number="263", finish="foil"),
        ]
        report = collection.reconcile(self.r, rows)
        self.assertEqual(report["created"], 3)
        sigs = {collection.printing_signature(i) for i in self.owned("Sol Ring")}
        self.assertEqual(len(sigs), 3)

    def test_duplicate_remote_rows_for_same_printing_are_summed(self):
        # Archidekt can split one printing across rows (e.g. by condition);
        # condition is not part of the signature, so they must collapse.
        rows = [row(quantity=2, condition="NM"), row(quantity=1, condition="LP")]
        report = collection.reconcile(self.r, rows)
        self.assertEqual(report["remote_copies"], 3)
        self.assertEqual(report["created"], 3)
        self.assertEqual(len(self.owned("Sol Ring")), 3)


class TestReconcileIdempotency(CollectionTestBase):
    def test_second_import_creates_nothing(self):
        first = collection.reconcile(self.r, [row(quantity=2)])
        second = collection.reconcile(self.r, [row(quantity=2)])
        self.assertEqual(first["created"], 2)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["already_present"], 2)
        self.assertEqual(len(self.owned("Sol Ring")), 2)

    def test_only_the_shortfall_is_created_when_quantity_grows(self):
        collection.reconcile(self.r, [row(quantity=2)])
        report = collection.reconcile(self.r, [row(quantity=5)])
        self.assertEqual(report["created"], 3)
        self.assertEqual(len(self.owned("Sol Ring")), 5)

    def test_copy_already_in_a_deck_counts_as_owned(self):
        """A card sleeved in a physical deck is still a copy you own and
        Archidekt still counts it — importing must not duplicate it."""
        inst = instance_store.create_instance(
            self.r,
            card_name="Sol Ring",
            ownership_status="in_deck",
            deck_id="archidekt:1",
            deck_name="Meren",
            set="C21",
            collector_number="263",
            finish="nonfoil",
            language="EN",
        )
        report = collection.reconcile(self.r, [row(quantity=1)])
        self.assertEqual(report["created"], 0)
        self.assertEqual(report["already_present"], 1)
        still = instance_store.get_instance(self.r, inst["id"])
        self.assertEqual(still["ownership_status"], "in_deck")
        self.assertEqual(still["deck_id"], "archidekt:1")

    def test_in_mail_copy_counts_as_owned(self):
        instance_store.create_instance(
            self.r,
            card_name="Sol Ring",
            ownership_status="in_mail",
            set="C21",
            collector_number="263",
        )
        report = collection.reconcile(self.r, [row(quantity=1)])
        self.assertEqual(report["created"], 0)


class TestReconcilePlaceholders(CollectionTestBase):
    def test_wishlist_placeholder_is_promoted_not_duplicated(self):
        placeholder = instance_store.create_instance(
            self.r,
            card_name="Sol Ring",
            ownership_status="not_owned",
            considered_for_deck="archidekt:1",
        )
        report = collection.reconcile(self.r, [row(quantity=1)])

        self.assertEqual(report["promoted"], 1)
        self.assertEqual(report["created"], 0)
        promoted = instance_store.get_instance(self.r, placeholder["id"])
        self.assertEqual(promoted["ownership_status"], "in_collection")
        # printing details from the collection row are filled in on promotion
        self.assertEqual(promoted["set"], "C21")
        self.assertEqual(promoted["collector_number"], "263")
        self.assertEqual(len(self.owned("Sol Ring")), 1)

    def test_promotion_then_creation_for_remaining_shortfall(self):
        instance_store.create_instance(
            self.r, card_name="Sol Ring", ownership_status="not_owned"
        )
        report = collection.reconcile(self.r, [row(quantity=3)])
        self.assertEqual(report["promoted"], 1)
        self.assertEqual(report["created"], 2)
        self.assertEqual(len(self.owned("Sol Ring")), 3)


class TestCollapsedPrintingsKeepPerCopyDetails(CollectionTestBase):
    def test_each_copy_keeps_its_own_condition_and_price(self):
        rows = [
            row(quantity=1, condition="NM", price_paid=1.0, date_acquired="2024-01-01"),
            row(quantity=1, condition="LP", price_paid=9.0, date_acquired="2025-06-06"),
        ]
        collection.reconcile(self.r, rows)
        got = sorted(
            (i["condition"], i["price_paid"], i["date_acquired"])
            for i in self.owned("Sol Ring")
        )
        self.assertEqual(got, [("LP", 9.0, "2025-06-06"), ("NM", 1.0, "2024-01-01")])


class TestDryRunMatchesLiveWithPlaceholders(CollectionTestBase):
    def test_preview_counts_wishlist_promotion_like_live(self):
        instance_store.create_instance(
            self.r, card_name="Sol Ring", ownership_status="not_owned"
        )
        preview = collection.reconcile(self.r, [row(quantity=3)], dry_run=True)
        self.assertEqual(self.owned("Sol Ring"), [])
        live = collection.reconcile(self.r, [row(quantity=3)])
        for key in ("promoted", "created", "already_present"):
            self.assertEqual(preview[key], live[key], key)
        self.assertEqual((live["promoted"], live["created"]), (1, 2))


class TestReconcileSurplus(CollectionTestBase):
    def test_local_only_copies_are_reported_not_deleted(self):
        collection.reconcile(self.r, [row(quantity=3)])
        report = collection.reconcile(self.r, [row(quantity=1)])

        self.assertEqual(report["created"], 0)
        self.assertEqual(len(report["surplus"]), 1)
        surplus = report["surplus"][0]
        self.assertEqual(surplus["card_name"], "Sol Ring")
        self.assertEqual(surplus["count"], 2)
        # nothing removed
        self.assertEqual(len(self.owned("Sol Ring")), 3)

    def test_card_absent_from_remote_entirely_is_not_surplus_noise(self):
        """Cards not mentioned by the remote rows at all are out of scope for
        this diff (we only index the names we were asked about), so they are
        left alone and not reported."""
        instance_store.create_instance(
            self.r, card_name="Arcane Signet", ownership_status="in_collection"
        )
        report = collection.reconcile(self.r, [row(quantity=1)])
        self.assertEqual(report["surplus"], [])
        self.assertEqual(len(self.owned("Arcane Signet")), 1)


class TestReconcileDryRun(CollectionTestBase):
    def test_dry_run_reports_without_writing(self):
        report = collection.reconcile(self.r, [row(quantity=2)], dry_run=True)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["created"], 2)
        self.assertEqual(self.owned("Sol Ring"), [])

    def test_dry_run_after_real_import_reports_zero_new(self):
        collection.reconcile(self.r, [row(quantity=2)])
        report = collection.reconcile(self.r, [row(quantity=2)], dry_run=True)
        self.assertEqual(report["created"], 0)


class TestImportCollection(CollectionTestBase):
    def test_import_records_sync_metadata(self):
        result = collection.import_collection(
            self.r, username="CovaDax", fetcher=lambda u: [row(quantity=2)], dry_run=False
        )
        self.assertEqual(result["username"], "CovaDax")
        self.assertEqual(result["report"]["created"], 2)
        self.assertIsNotNone(result["synced_at"])

        last = collection.last_sync(self.r)
        self.assertEqual(last["username"], "CovaDax")
        self.assertEqual(last["report"]["created"], 2)

    def test_dry_run_import_does_not_record_a_sync(self):
        collection.import_collection(
            self.r, username="CovaDax", dry_run=True, fetcher=lambda u: [row()]
        )
        self.assertIsNone(collection.last_sync(self.r))

    def test_bare_call_defaults_to_dry_run_and_writes_nothing(self):
        result = collection.import_collection(
            self.r, username="CovaDax", fetcher=lambda u: [row(quantity=2)]
        )
        self.assertTrue(result["report"]["dry_run"])
        self.assertEqual(self.owned("Sol Ring"), [])
        self.assertIsNone(collection.last_sync(self.r))

    def test_cli_defaults_to_dry_run_and_live_is_opt_in(self):
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "import_collection.py")
        spec = importlib.util.spec_from_file_location("import_collection_cli", path)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        report = {"rows": 0, "remote_copies": 0, "created": 0, "promoted": 0,
                  "already_present": 0, "surplus": []}
        result = {"username": "CovaDax", "report": report}
        for argv, expected in ((["x"], True), (["x", "--dry-run"], True), (["x", "--live"], False)):
            with unittest.mock.patch.object(sys, "argv", argv), \
                 unittest.mock.patch.object(cli.redis, "from_url", return_value=FakeRedis()), \
                 unittest.mock.patch.object(cli.collection, "import_collection", return_value=result) as m:
                cli.main()
            self.assertIs(m.call_args.kwargs["dry_run"], expected, argv)

    def test_missing_username_raises(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(collection.CollectionImportError):
                collection.import_collection(self.r, fetcher=lambda u: [], dry_run=False)

    def test_username_defaults_to_env(self):
        with unittest.mock.patch.dict(os.environ, {"ARCHIDEKT_USERNAME": "CovaDax"}, clear=True):
            result = collection.import_collection(self.r, fetcher=lambda u: [row()], dry_run=False)
        self.assertEqual(result["username"], "CovaDax")

    def test_empty_collection_is_not_an_error(self):
        result = collection.import_collection(
            self.r, username="CovaDax", fetcher=lambda u: [], dry_run=False
        )
        self.assertEqual(result["report"]["created"], 0)
        self.assertEqual(result["report"]["remote_copies"], 0)


if __name__ == "__main__":
    unittest.main()
