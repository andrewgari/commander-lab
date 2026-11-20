"""Unit tests for transcribe.py — Archidekt -> Moxfield deck transcription.

No network: the Moxfield client is a fake recording every call. Uses the same
in-memory FakeRedis stand-in as tests/test_instances.py.

Run: python tests/test_transcribe.py
"""
import os
import sys
import json
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.server.domain.decks.registry as registry
import src.server.domain.decks.transcribe as transcribe
from src.server.infra.providers import moxfield_writer
from src.server.infra.providers.moxfield_writer import MoxfieldWriteError

# The real throttle sleeps 1.5s per write to stay under Moxfield's rate limit;
# these tests never touch Moxfield, so stub it out rather than sleeping.
moxfield_writer.throttle = lambda: None


class FakeRedis:
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


class FakeMoxfieldClient:
    """Records folder/deck writes instead of calling Moxfield."""

    def __init__(self, fail_on_names=(), fail_description_for=()):
        self.folders = []
        self.created = []
        self.descriptions = []
        self.fail_on_names = set(fail_on_names)
        self.fail_description_for = set(fail_description_for)
        self._next_id = 1

    def ensure_folder(self, name):
        self.folders.append(name)
        return {"id": "folder-1", "name": name}

    def create_deck(self, name, import_text, visibility="private", folder_id=None, deck_format="commander"):
        if name in self.fail_on_names:
            raise MoxfieldWriteError(f"could not create deck {name!r} (500)")
        deck_id = f"mx{self._next_id}"
        self._next_id += 1
        deck = {
            "id": deck_id,
            "publicId": f"pub-{deck_id}",
            "publicUrl": f"https://moxfield.com/decks/pub-{deck_id}",
            "name": name,
            "format": deck_format,
            "visibility": visibility,
            "version": 1,
        }
        self.created.append({
            "name": name, "import_text": import_text, "visibility": visibility,
            "folder_id": folder_id, "format": deck_format,
        })
        return deck

    def update_deck_description(self, deck, description):
        if deck.get("name") in self.fail_description_for:
            raise MoxfieldWriteError("could not set description (500)")
        self.descriptions.append({"deck": deck["name"], "description": description})
        return deck


def deck(name="Kaalia", status="physical", commanders=("Kaalia of the Vast",),
         cards=None, color="WBR", registry_id="archidekt:1"):
    if cards is None:
        cards = [
            {"name": "Kaalia of the Vast", "quantity": 1},
            {"name": "Sol Ring", "quantity": 1},
            {"name": "Arcane Signet", "quantity": 1},
        ]
    source, source_id = registry_id.split(":", 1)
    return {
        "id": source_id if not source_id.isdigit() else int(source_id),
        "name": name,
        "status": status,
        "color": color,
        "commanders": list(commanders),
        "commander_uids": [],
        "folder": "Commander Decks",
        "source": source,
        "source_id": source_id,
        "registry_id": registry_id,
        "cards": cards,
        "url": f"https://archidekt.com/decks/{source_id}",
    }


class TranscribeTestBase(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.client = FakeMoxfieldClient()

    def set_decks(self, decks):
        self.r.set(registry.DECKS_KEY, json.dumps(decks))


class TestEligibility(TranscribeTestBase):
    def test_only_physical_decks_are_eligible(self):
        self.set_decks([
            deck("Physical One", status="physical", registry_id="archidekt:1"),
            deck("Brewing", status="testing", registry_id="archidekt:2"),
            deck("Old", status="retired", registry_id="archidekt:3"),
            deck("Digital", status="digital", registry_id="archidekt:4"),
        ])
        names = [d["name"] for d in transcribe.eligible_decks(self.r)]
        self.assertEqual(names, ["Physical One"])

    def test_transcribe_all_ignores_non_physical_entirely(self):
        self.set_decks([
            deck("Physical One", status="physical", registry_id="archidekt:1"),
            deck("Brewing", status="testing", registry_id="archidekt:2"),
        ])
        report = transcribe.transcribe_all(self.r, client=self.client, dry_run=False)
        self.assertEqual(report["eligible"], 1)
        self.assertEqual(report["created"], 1)
        self.assertEqual([c["name"] for c in self.client.created], ["Physical One"])

    def test_non_physical_deck_passed_directly_is_still_refused(self):
        """Defence in depth: even a direct call can't push a testing deck."""
        result = transcribe.transcribe_deck(
            self.r, self.client, deck("Brewing", status="testing"), folder_id="folder-1"
        )
        self.assertEqual(result["action"], "skipped_not_physical")
        self.assertEqual(self.client.created, [])


class TestImportText(unittest.TestCase):
    def test_commanders_go_under_commander_header(self):
        text = transcribe.build_import_text(deck())
        self.assertIn("// Commander\n1 Kaalia of the Vast", text)
        self.assertIn("// Mainboard", text)
        # commander must not be repeated in the mainboard section
        mainboard = text.split("// Mainboard")[1]
        self.assertNotIn("Kaalia of the Vast", mainboard)
        self.assertIn("1 Sol Ring", mainboard)

    def test_quantities_are_preserved(self):
        d = deck(cards=[{"name": "Forest", "quantity": 12}, {"name": "Sol Ring", "quantity": 1}],
                 commanders=())
        text = transcribe.build_import_text(d)
        self.assertIn("12 Forest", text)

    def test_partner_commanders_both_listed(self):
        d = deck(
            commanders=("Thrasios, Triton Hero", "Tymna the Weaver"),
            cards=[
                {"name": "Thrasios, Triton Hero", "quantity": 1},
                {"name": "Tymna the Weaver", "quantity": 1},
                {"name": "Sol Ring", "quantity": 1},
            ],
        )
        text = transcribe.build_import_text(d)
        commander_block = text.split("// Mainboard")[0]
        self.assertIn("Thrasios, Triton Hero", commander_block)
        self.assertIn("Tymna the Weaver", commander_block)

    def test_commander_missing_from_cardlist_is_still_included(self):
        d = deck(commanders=("Kaalia of the Vast",),
                 cards=[{"name": "Sol Ring", "quantity": 1}])
        text = transcribe.build_import_text(d)
        self.assertIn("1 Kaalia of the Vast", text.split("// Mainboard")[0])

    def test_cards_without_names_or_zero_quantity_are_skipped(self):
        d = deck(cards=[{"name": "", "quantity": 1}, {"name": "Sol Ring", "quantity": 0},
                        {"name": "Arcane Signet", "quantity": 1}], commanders=())
        text = transcribe.build_import_text(d)
        self.assertIn("1 Arcane Signet", text)
        self.assertNotIn("Sol Ring", text)


class TestDescription(unittest.TestCase):
    def test_includes_commander_colors_count_and_archidekt_link(self):
        desc = transcribe.build_description(deck())
        self.assertIn("Commander: Kaalia of the Vast", desc)
        self.assertIn("Color identity: White / Black / Red", desc)
        self.assertIn("Cards: 3", desc)
        self.assertIn("https://archidekt.com/decks/1", desc)

    def test_plural_commanders_label(self):
        desc = transcribe.build_description(
            deck(commanders=("Thrasios, Triton Hero", "Tymna the Weaver"))
        )
        self.assertIn("Commanders: Thrasios, Triton Hero + Tymna the Weaver", desc)

    def test_colorless_identity(self):
        self.assertEqual(transcribe.color_identity_label("C"), "Colorless")
        self.assertEqual(transcribe.color_identity_label(""), "Colorless")

    def test_color_identity_uses_wubrg_order(self):
        self.assertEqual(transcribe.color_identity_label("GWU"), "White / Blue / Green")

    def test_deck_with_no_commander_still_gets_a_description(self):
        desc = transcribe.build_description(deck(commanders=()))
        self.assertNotIn("Commander:", desc)
        self.assertIn("Color identity:", desc)


class TestTranscribeWrites(TranscribeTestBase):
    def test_decks_are_created_private_in_the_folder(self):
        self.set_decks([deck("Kaalia", registry_id="archidekt:1")])
        report = transcribe.transcribe_all(self.r, client=self.client, dry_run=False)

        self.assertEqual(self.client.folders, ["Physical"])
        created = self.client.created[0]
        self.assertEqual(created["visibility"], "private")
        self.assertEqual(created["folder_id"], "folder-1")
        self.assertEqual(created["format"], "commander")
        self.assertEqual(report["visibility"], "private")
        self.assertEqual(report["folder"], "Physical")

    def test_folder_name_and_visibility_are_configurable(self):
        self.set_decks([deck("Kaalia", registry_id="archidekt:1")])
        transcribe.transcribe_all(self.r, folder_name="Paper Decks", visibility="unlisted", client=self.client, dry_run=False)
        self.assertEqual(self.client.folders, ["Paper Decks"])
        self.assertEqual(self.client.created[0]["visibility"], "unlisted")

    def test_description_is_set_after_creation(self):
        self.set_decks([deck("Kaalia", registry_id="archidekt:1")])
        transcribe.transcribe_all(self.r, client=self.client, dry_run=False)
        self.assertEqual(len(self.client.descriptions), 1)
        self.assertIn("Kaalia of the Vast", self.client.descriptions[0]["description"])

    def test_mapping_is_recorded_for_idempotency(self):
        self.set_decks([deck("Kaalia", registry_id="archidekt:1")])
        transcribe.transcribe_all(self.r, client=self.client, dry_run=False)
        mapping = transcribe.get_mapping(self.r, "archidekt:1")
        self.assertEqual(mapping["moxfield_public_id"], "pub-mx1")
        self.assertEqual(mapping["folder"], "Physical")
        self.assertTrue(mapping["moxfield_url"].startswith("https://moxfield.com/decks/"))

    def test_rerun_skips_already_transcribed_decks(self):
        self.set_decks([deck("Kaalia", registry_id="archidekt:1")])
        transcribe.transcribe_all(self.r, client=self.client, dry_run=False)
        second = transcribe.transcribe_all(self.r, client=self.client, dry_run=False)

        self.assertEqual(second["created"], 0)
        self.assertEqual(second["skipped_already"], 1)
        self.assertEqual(len(self.client.created), 1)  # no duplicate on Moxfield

    def test_force_retranscribes(self):
        self.set_decks([deck("Kaalia", registry_id="archidekt:1")])
        transcribe.transcribe_all(self.r, client=self.client, dry_run=False)
        report = transcribe.transcribe_all(self.r, force=True, client=self.client, dry_run=False)
        self.assertEqual(report["created"], 1)
        self.assertEqual(len(self.client.created), 2)

    def test_one_failing_deck_does_not_abort_the_batch(self):
        self.set_decks([
            deck("Good One", registry_id="archidekt:1"),
            deck("Bad One", registry_id="archidekt:2"),
            deck("Good Two", registry_id="archidekt:3"),
        ])
        client = FakeMoxfieldClient(fail_on_names={"Bad One"})
        report = transcribe.transcribe_all(self.r, client=client, dry_run=False)

        self.assertEqual(report["created"], 2)
        self.assertEqual(report["failed"], 1)
        failed = [x for x in report["results"] if x["action"] == "failed"]
        self.assertEqual(failed[0]["name"], "Bad One")
        self.assertIsNone(transcribe.get_mapping(self.r, "archidekt:2"))

    def test_description_failure_still_keeps_the_created_deck(self):
        self.set_decks([deck("Kaalia", registry_id="archidekt:1")])
        client = FakeMoxfieldClient(fail_description_for={"Kaalia"})
        report = transcribe.transcribe_all(self.r, client=client, dry_run=False)

        self.assertEqual(report["created"], 1)
        result = report["results"][0]
        self.assertEqual(result["action"], "created")
        self.assertIn("description_error", result)
        self.assertIsNotNone(transcribe.get_mapping(self.r, "archidekt:1"))

    def test_mapping_saved_before_description_so_retry_does_not_duplicate(self):
        self.set_decks([deck("Kaalia", registry_id="archidekt:1")])

        class Boom(Exception):
            pass

        client = FakeMoxfieldClient()

        def explode(*a, **k):
            raise Boom()

        client.update_deck_description = explode
        with self.assertRaises(Boom):
            transcribe.transcribe_all(self.r, client=client, dry_run=False)
        self.assertIsNotNone(transcribe.get_mapping(self.r, "archidekt:1"))

        retry = FakeMoxfieldClient()
        report = transcribe.transcribe_all(self.r, client=retry, dry_run=False)
        self.assertEqual(retry.created, [])
        self.assertEqual(report["results"][0]["action"], "skipped_already")

    def test_empty_decklist_is_reported_not_pushed(self):
        self.set_decks([deck("Empty", cards=[], commanders=(), registry_id="archidekt:1")])
        report = transcribe.transcribe_all(self.r, client=self.client, dry_run=False)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(self.client.created, [])


class TestDryRun(TranscribeTestBase):
    def test_dry_run_touches_no_moxfield_calls(self):
        self.set_decks([deck("Kaalia", registry_id="archidekt:1")])
        report = transcribe.transcribe_all(self.r, dry_run=True, client=self.client)

        self.assertTrue(report["dry_run"])
        self.assertEqual(report["created"], 1)
        self.assertEqual(self.client.created, [])
        self.assertEqual(self.client.folders, [])
        self.assertIsNone(transcribe.get_mapping(self.r, "archidekt:1"))

    def test_bare_call_defaults_to_dry_run_and_makes_no_writes(self):
        self.set_decks([deck("Kaalia", registry_id="archidekt:1")])
        report = transcribe.transcribe_all(self.r, client=self.client)
        self.assertTrue(report["dry_run"])
        self.assertEqual(self.client.created, [])
        self.assertEqual(self.client.folders, [])
        self.assertIsNone(transcribe.get_mapping(self.r, "archidekt:1"))

    def test_cli_defaults_to_dry_run_and_live_is_opt_in(self):
        import importlib.util
        import sys
        import unittest.mock as um
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "transcribe_to_moxfield.py")
        spec = importlib.util.spec_from_file_location("transcribe_cli", path)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        import collections
        report = collections.defaultdict(int, results=[], folder="F", visibility="private")
        for argv, expected in ((["x"], True), (["x", "--dry-run"], True), (["x", "--live"], False)):
            with um.patch.object(sys, "argv", argv), \
                 um.patch.object(cli.redis, "from_url", return_value=self.r), \
                 um.patch.object(cli.transcribe, "transcribe_all", return_value=report) as m:
                cli.main()
            self.assertIs(m.call_args.kwargs["dry_run"], expected, argv)

    def test_dry_run_includes_the_generated_description(self):
        self.set_decks([deck("Kaalia", registry_id="archidekt:1")])
        report = transcribe.transcribe_all(self.r, dry_run=True, client=self.client)
        self.assertIn("Kaalia of the Vast", report["results"][0]["description"])

    def test_no_physical_decks_is_an_empty_report_not_an_error(self):
        self.set_decks([deck("Brewing", status="testing", registry_id="archidekt:2")])
        report = transcribe.transcribe_all(self.r, client=self.client, dry_run=False)
        self.assertEqual(report["eligible"], 0)
        self.assertEqual(report["created"], 0)
        self.assertEqual(self.client.folders, [])


if __name__ == "__main__":
    unittest.main()
