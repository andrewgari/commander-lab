"""Unit tests for archive.py — rename + re-file existing Moxfield decks.

The central guarantee under test: NOTHING IS EVER DELETED, and every original
name is recorded before it is changed. The fake client raises if any delete
endpoint is touched.

Run: python tests/test_archive.py
"""
import json
import os
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.server.domain.decks.archive as archive
import src.server.domain.decks.transcribe as transcribe
from src.server.infra.providers import moxfield_writer
from src.server.infra.providers.moxfield_writer import MoxfieldWriteError

moxfield_writer.throttle = lambda: None


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value

    def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in self.store if k.startswith(prefix)]


class FakeMoxfieldClient:
    """Records mutations. Any delete attempt fails the test loudly."""

    def __init__(self, decks, fail_update_for=(), fail_folder_for=(), folder_id="folder-1"):
        self.decks = [dict(d) for d in decks]
        # Original name per id: update_deck renames in place, so failure
        # lookups must not depend on the current name.
        self.original_names = {d["id"]: d["name"] for d in decks}
        self.updates = []
        self.folder_moves = []
        self.folders_created = []
        self.deletes = []           # must stay empty
        self.fail_update_for = set(fail_update_for)
        self.fail_folder_for = set(fail_folder_for)
        self.folder_id = folder_id

    def list_my_decks(self):
        return [dict(d) for d in self.decks]

    def ensure_folder(self, name):
        self.folders_created.append(name)
        return {"id": self.folder_id, "name": name}

    def update_deck(self, deck, **changes):
        if deck.get("name") in self.fail_update_for:
            raise MoxfieldWriteError(f"could not update deck {deck.get('name')!r} (500)")
        if "cards" in changes or "mainboard" in changes:
            raise AssertionError("update_deck must never touch the cardlist")
        self.updates.append({"deck_id": deck["id"], "old_name": deck["name"], **changes})
        for stored in self.decks:
            if stored["id"] == deck["id"]:
                stored.update(changes)
        return {**deck, **changes}

    def add_deck_to_folder(self, deck_id, folder_id):
        if self.original_names.get(deck_id) in self.fail_folder_for:
            raise MoxfieldWriteError(f"could not move deck {deck_id} (500)")
        self.folder_moves.append({"deck_id": deck_id, "folder_id": folder_id})

    # Deliberately present so a stray call is caught instead of silently
    # hitting the real API surface.
    def delete_deck(self, *args, **kwargs):
        self.deletes.append(args)
        raise AssertionError("archive.py must never delete a deck")

    def remove_deck_from_folder(self, *args, **kwargs):
        self.deletes.append(args)
        raise AssertionError("archive.py must never remove decks from folders")


def mox_deck(name, deck_id="d1", public_id=None, visibility="public", version=1):
    return {
        "id": deck_id,
        "publicId": public_id or f"pub-{deck_id}",
        "publicUrl": f"https://moxfield.com/decks/{public_id or f'pub-{deck_id}'}",
        "name": name,
        "format": "commander",
        "visibility": visibility,
        "version": version,
        "areCommentsEnabled": True,
    }


class ArchiveTestBase(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()


class TestNothingIsDeleted(ArchiveTestBase):
    def test_archive_never_calls_any_delete(self):
        client = FakeMoxfieldClient([mox_deck("Kyra Fireball", "d1"), mox_deck("BIRD UP!!!", "d2")])
        archive.archive_all(self.r, client=client, dry_run=False)
        self.assertEqual(client.deletes, [])

    def test_archive_module_does_not_reference_the_delete_endpoint(self):
        """Static guard: the source must not contain a deck-delete call."""
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "server", "domain", "decks", "archive.py")
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        # The docstring names the endpoint to explain why it's unused; make sure
        # it never appears as an actual request.
        self.assertNotIn('"DELETE"', source)
        self.assertNotIn("delete_deck", source)

    def test_every_deck_still_exists_after_archiving(self):
        decks = [mox_deck("A", "d1"), mox_deck("B", "d2"), mox_deck("C", "d3")]
        client = FakeMoxfieldClient(decks)
        archive.archive_all(self.r, client=client, dry_run=False)
        self.assertEqual(len(client.list_my_decks()), 3)


class TestBackupBeforeMutation(ArchiveTestBase):
    def test_original_name_and_visibility_are_recorded(self):
        client = FakeMoxfieldClient([mox_deck("Kyra Fireball", "d1", visibility="public")])
        archive.archive_all(self.r, client=client, dry_run=False)

        backup = archive.get_backup(self.r, "d1")
        self.assertEqual(backup["original_name"], "Kyra Fireball")
        self.assertEqual(backup["original_visibility"], "public")
        self.assertEqual(backup["archived_to_folder"], "Archive")

    def test_backup_is_written_even_when_the_update_fails(self):
        """A failed rename must still leave a recoverable record."""
        client = FakeMoxfieldClient([mox_deck("Doomed", "d1")], fail_update_for={"Doomed"})
        report = archive.archive_all(self.r, client=client, dry_run=False)

        self.assertEqual(report["failed"], 1)
        self.assertIsNotNone(archive.get_backup(self.r, "d1"))

    def test_second_archive_does_not_clobber_the_true_original(self):
        client = FakeMoxfieldClient([mox_deck("Kyra Fireball", "d1")])
        archive.archive_all(self.r, client=client, dry_run=False)
        # simulate an odd second pass with a different prefix
        archive.archive_all(self.r, prefix="[Old] ", client=client, dry_run=False)

        backup = archive.get_backup(self.r, "d1")
        self.assertEqual(backup["original_name"], "Kyra Fireball")


class TestRenaming(ArchiveTestBase):
    def test_decks_are_renamed_with_the_prefix(self):
        client = FakeMoxfieldClient([mox_deck("Kyra Fireball", "d1")])
        archive.archive_all(self.r, client=client, dry_run=False)
        self.assertEqual(client.updates[0]["name"], "[Archived] Kyra Fireball")

    def test_prefix_is_configurable(self):
        client = FakeMoxfieldClient([mox_deck("Kyra Fireball", "d1")])
        archive.archive_all(self.r, prefix="2026-09 ", client=client, dry_run=False)
        self.assertEqual(client.updates[0]["name"], "2026-09 Kyra Fireball")

    def test_already_prefixed_deck_is_skipped(self):
        client = FakeMoxfieldClient([mox_deck("[Archived] Kyra Fireball", "d1")])
        report = archive.archive_all(self.r, client=client, dry_run=False)

        self.assertEqual(report["skipped_already"], 1)
        self.assertEqual(report["archived"], 0)
        self.assertEqual(client.updates, [])

    def test_rerun_is_idempotent(self):
        client = FakeMoxfieldClient([mox_deck("Kyra Fireball", "d1")])
        archive.archive_all(self.r, client=client, dry_run=False)
        report = archive.archive_all(self.r, client=client, dry_run=False)

        self.assertEqual(report["archived"], 0)
        self.assertEqual(report["skipped_already"], 1)
        self.assertEqual(len(client.updates), 1)  # not renamed twice

    def test_archived_name_helper_is_idempotent(self):
        once = archive.archived_name("Foo")
        self.assertEqual(archive.archived_name(once), "[Archived] Foo")


class TestVisibilityAndFolder(ArchiveTestBase):
    def test_decks_are_set_private_by_default(self):
        client = FakeMoxfieldClient([mox_deck("Kyra Fireball", "d1", visibility="public")])
        archive.archive_all(self.r, client=client, dry_run=False)
        self.assertEqual(client.updates[0]["visibility"], "private")

    def test_visibility_none_leaves_it_untouched(self):
        client = FakeMoxfieldClient([mox_deck("Kyra Fireball", "d1", visibility="public")])
        archive.archive_all(self.r, visibility=None, client=client, dry_run=False)
        self.assertNotIn("visibility", client.updates[0])

    def test_invalid_visibility_is_rejected_before_any_write(self):
        client = FakeMoxfieldClient([mox_deck("Kyra Fireball", "d1")])
        with self.assertRaises(archive.ArchiveError):
            archive.archive_all(self.r, visibility="secret", client=client, dry_run=False)
        self.assertEqual(client.updates, [])

    def test_decks_are_moved_into_the_archive_folder(self):
        client = FakeMoxfieldClient([mox_deck("A", "d1"), mox_deck("B", "d2")])
        archive.archive_all(self.r, client=client, dry_run=False)

        self.assertEqual(client.folders_created, ["Archive"])
        self.assertEqual([m["deck_id"] for m in client.folder_moves], ["d1", "d2"])
        self.assertTrue(all(m["folder_id"] == "folder-1" for m in client.folder_moves))

    def test_folder_name_is_configurable(self):
        client = FakeMoxfieldClient([mox_deck("A", "d1")])
        archive.archive_all(self.r, folder_name="Old Decks", client=client, dry_run=False)
        self.assertEqual(client.folders_created, ["Old Decks"])

    def test_folder_move_failure_keeps_the_rename_and_reports_it(self):
        client = FakeMoxfieldClient([mox_deck("Kyra Fireball", "d1")], fail_folder_for={"Kyra Fireball"})
        report = archive.archive_all(self.r, client=client, dry_run=False)

        self.assertEqual(report["archived"], 1)
        self.assertIn("folder_error", report["results"][0])
        self.assertEqual(client.updates[0]["name"], "[Archived] Kyra Fireball")


    def test_rename_then_failed_folder_move_is_retried_next_run(self):
        decks = [mox_deck("Kyra Fireball", "d1")]
        first = FakeMoxfieldClient(decks, fail_folder_for={"Kyra Fireball"})
        archive.archive_all(self.r, client=first, dry_run=False)
        self.assertEqual(first.folder_moves, [])

        # Second run: the deck is already renamed, but was never filed.
        renamed = [dict(d) for d in first.decks]
        second = FakeMoxfieldClient(renamed)
        report = archive.archive_all(self.r, client=second, dry_run=False)

        self.assertEqual(second.folder_moves, [{"deck_id": "d1", "folder_id": "folder-1"}])
        self.assertEqual(second.updates, [])  # no second rename
        self.assertEqual(report["results"][0]["action"], "folder_move_retried")

        # Third run: now genuinely done.
        third = FakeMoxfieldClient(renamed)
        report = archive.archive_all(self.r, client=third, dry_run=False)
        self.assertEqual(third.folder_moves, [])
        self.assertEqual(report["results"][0]["action"], "skipped_already")


class TestTranscribedDecksAreProtected(ArchiveTestBase):
    def _mark_transcribed(self, public_id):
        self.r.set(
            f"{transcribe.MAP_KEY_PREFIX}archidekt:1",
            json.dumps({"registry_id": "archidekt:1", "moxfield_public_id": public_id}),
        )

    def test_decks_created_by_transcribe_are_skipped(self):
        self._mark_transcribed("pub-d2")
        client = FakeMoxfieldClient([mox_deck("Old One", "d1"), mox_deck("Kaalia", "d2", "pub-d2")])
        report = archive.archive_all(self.r, client=client, dry_run=False)

        self.assertEqual(report["skipped_transcribed"], 1)
        self.assertEqual(report["archived"], 1)
        self.assertEqual([u["old_name"] for u in client.updates], ["Old One"])

    def test_skip_transcribed_false_archives_them_too(self):
        self._mark_transcribed("pub-d2")
        client = FakeMoxfieldClient([mox_deck("Kaalia", "d2", "pub-d2")])
        report = archive.archive_all(self.r, skip_transcribed=False, client=client, dry_run=False)
        self.assertEqual(report["archived"], 1)


class TestDryRun(ArchiveTestBase):
    def test_dry_run_changes_nothing(self):
        client = FakeMoxfieldClient([mox_deck("Kyra Fireball", "d1"), mox_deck("BIRD UP!!!", "d2")])
        report = archive.archive_all(self.r, dry_run=True, client=client)

        self.assertTrue(report["dry_run"])
        self.assertEqual(report["archived"], 2)
        self.assertEqual(client.updates, [])
        self.assertEqual(client.folder_moves, [])
        self.assertEqual(client.folders_created, [])
        self.assertIsNone(archive.get_backup(self.r, "d1"))

    def test_bare_call_defaults_to_dry_run_and_makes_no_writes(self):
        client = FakeMoxfieldClient([mox_deck("Kyra Fireball", "d1")])
        report = archive.archive_all(self.r, client=client)
        self.assertTrue(report["dry_run"])
        self.assertEqual(client.updates, [])
        self.assertEqual(client.folder_moves, [])
        self.assertEqual(client.folders_created, [])
        self.assertIsNone(archive.get_backup(self.r, "d1"))

    def test_cli_defaults_to_dry_run_and_live_is_opt_in(self):
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "archive_moxfield_decks.py")
        spec = importlib.util.spec_from_file_location("archive_cli", path)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        import collections
        report = collections.defaultdict(int, results=[], folder="Archive", prefix="", visibility="private")
        for argv, expected in ((["x"], True), (["x", "--dry-run"], True), (["x", "--live"], False)):
            with unittest.mock.patch.object(sys, "argv", argv), \
                 unittest.mock.patch.object(cli.redis, "from_url", return_value=FakeRedis()), \
                 unittest.mock.patch.object(cli.archive, "archive_all", return_value=report) as m:
                cli.main()
            self.assertIs(m.call_args.kwargs["dry_run"], expected, argv)

    def test_cli_restore_without_live_makes_no_client_calls(self):
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "archive_moxfield_decks.py")
        spec = importlib.util.spec_from_file_location("archive_cli_restore", path)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        archive.archive_all(self.r, client=FakeMoxfieldClient([mox_deck("Kyra", "d1")]), dry_run=False)
        with unittest.mock.patch.object(sys, "argv", ["x", "--restore", "d1"]), \
             unittest.mock.patch.object(cli.redis, "from_url", return_value=self.r), \
             unittest.mock.patch.object(cli.archive, "restore") as restore, \
             unittest.mock.patch.object(cli.archive.moxfield_writer, "MoxfieldClient") as client_cls:
            rc = cli.main()
        self.assertEqual(rc, 0)
        restore.assert_not_called()
        client_cls.assert_not_called()
        with unittest.mock.patch.object(sys, "argv", ["x", "--restore", "d1", "--live"]), \
             unittest.mock.patch.object(cli.redis, "from_url", return_value=self.r), \
             unittest.mock.patch.object(cli.archive, "restore", return_value={"deck_id": "d1", "restored_name": "Kyra", "restored_visibility": "private"}) as restore:
            cli.main()
        restore.assert_called_once()

    def test_dry_run_shows_the_new_names(self):
        client = FakeMoxfieldClient([mox_deck("Kyra Fireball", "d1")])
        report = archive.archive_all(self.r, dry_run=True, client=client)
        self.assertEqual(report["results"][0]["new_name"], "[Archived] Kyra Fireball")
        self.assertEqual(report["results"][0]["action"], "would_archive")


class TestPartialFailure(ArchiveTestBase):
    def test_one_failure_does_not_abort_the_batch(self):
        client = FakeMoxfieldClient(
            [mox_deck("Good A", "d1"), mox_deck("Bad", "d2"), mox_deck("Good B", "d3")],
            fail_update_for={"Bad"},
        )
        report = archive.archive_all(self.r, client=client, dry_run=False)

        self.assertEqual(report["archived"], 2)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["total"], 3)
        self.assertEqual(len(client.list_my_decks()), 3)


class TestRestore(ArchiveTestBase):
    def test_restore_puts_the_original_name_and_visibility_back(self):
        client = FakeMoxfieldClient([mox_deck("Kyra Fireball", "d1", visibility="public")])
        archive.archive_all(self.r, client=client, dry_run=False)
        self.assertEqual(client.list_my_decks()[0]["name"], "[Archived] Kyra Fireball")

        result = archive.restore(self.r, "d1", client=client)

        self.assertEqual(result["restored_name"], "Kyra Fireball")
        self.assertEqual(client.list_my_decks()[0]["name"], "Kyra Fireball")
        self.assertEqual(client.list_my_decks()[0]["visibility"], "public")

    def test_restore_without_a_backup_raises(self):
        client = FakeMoxfieldClient([mox_deck("Never Archived", "d1")])
        with self.assertRaises(archive.ArchiveError):
            archive.restore(self.r, "d1", client=client)

    def test_restore_of_an_unknown_deck_raises(self):
        client = FakeMoxfieldClient([mox_deck("A", "d1")])
        archive.archive_all(self.r, client=client, dry_run=False)
        client.decks = []  # deck vanished from the account
        with self.assertRaises(archive.ArchiveError):
            archive.restore(self.r, "d1", client=client)


if __name__ == "__main__":
    unittest.main()
