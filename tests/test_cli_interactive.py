"""
Unit tests for the interactive CLI assign flow (cli_interactive.py), using
an injected input_fn/print_fn (scripted input, captured output) plus the
same in-memory FakeRedis pattern used in tests/test_instances.py.

Run: python -m unittest tests.test_cli_interactive
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import instances as instance_store
import registry
import cli_interactive
from tests.test_instances import FakeRedis


def make_input_fn(scripted):
    """Returns an input_fn that pops queued responses in order and raises
    EOFError (mirroring a real Ctrl-D) once exhausted."""
    queue = list(scripted)

    def input_fn(prompt):
        if not queue:
            raise EOFError()
        return queue.pop(0)

    return input_fn


def make_print_fn(sink):
    def print_fn(msg=""):
        sink.append(msg)

    return print_fn


class TestCliInteractive(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def _add_deck(self, registry_id="archidekt:1", name="Meren", status="testing"):
        import json

        decks = registry.list_decks(self.r)
        decks.append(
            {
                "id": registry_id,
                "registry_id": registry_id,
                "name": name,
                "status": status,
                "source": "archidekt",
                "source_id": registry_id.split(":")[1],
            }
        )
        self.r.set(registry.DECKS_KEY, json.dumps(decks))

    def test_happy_path_assigns_card_to_deck(self):
        instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="in_collection")
        self._add_deck()

        output = []
        input_fn = make_input_fn(["Sol Ring", "1", "1", "y"])
        print_fn = make_print_fn(output)

        rc = cli_interactive.run(self.r, input_fn=input_fn, print_fn=print_fn)

        self.assertEqual(rc, 0)
        updated = instance_store.list_instances(self.r, card_name="Sol Ring")[0]
        self.assertEqual(updated["ownership_status"], "in_deck")
        self.assertEqual(updated["deck_id"], "archidekt:1")
        self.assertTrue(any("Assigned" in line for line in output))

    def test_cancel_at_search_prompt(self):
        output = []
        input_fn = make_input_fn(["q"])
        print_fn = make_print_fn(output)

        rc = cli_interactive.run(self.r, input_fn=input_fn, print_fn=print_fn)

        self.assertEqual(rc, 1)
        self.assertTrue(any("Cancelled" in line for line in output))

    def test_cancel_at_confirmation(self):
        instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="in_collection")
        self._add_deck()

        output = []
        input_fn = make_input_fn(["Sol Ring", "1", "1", "n"])
        print_fn = make_print_fn(output)

        rc = cli_interactive.run(self.r, input_fn=input_fn, print_fn=print_fn)

        self.assertEqual(rc, 1)
        updated = instance_store.list_instances(self.r, card_name="Sol Ring")[0]
        self.assertEqual(updated["ownership_status"], "in_collection")

    def test_no_card_matches(self):
        output = []
        input_fn = make_input_fn(["Nonexistent Card"])
        print_fn = make_print_fn(output)

        rc = cli_interactive.run(self.r, input_fn=input_fn, print_fn=print_fn)

        self.assertEqual(rc, 2)
        self.assertTrue(any("No in_collection instances found" in line for line in output))

    def test_no_decks_available(self):
        instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="in_collection")
        # No decks registered at all.

        output = []
        input_fn = make_input_fn(["Sol Ring", "1"])
        print_fn = make_print_fn(output)

        rc = cli_interactive.run(self.r, input_fn=input_fn, print_fn=print_fn)

        self.assertEqual(rc, 2)
        self.assertTrue(any("No decks available" in line for line in output))

    def test_deck_selection_ambiguous_substring_then_resolved_by_number(self):
        instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="in_collection")
        self._add_deck(registry_id="archidekt:1", name="Meren Aristocrats")
        self._add_deck(registry_id="archidekt:2", name="Meren Reanimator")

        output = []
        # "Meren" matches both decks -> ambiguous message, then resolve via index "1".
        input_fn = make_input_fn(["Sol Ring", "1", "Meren", "1", "y"])
        print_fn = make_print_fn(output)

        rc = cli_interactive.run(self.r, input_fn=input_fn, print_fn=print_fn)

        self.assertEqual(rc, 0)
        self.assertTrue(any("decks match" in line for line in output))
        updated = instance_store.list_instances(self.r, card_name="Sol Ring")[0]
        self.assertEqual(updated["deck_id"], "archidekt:1")

    def test_search_card_prints_match_count(self):
        instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="in_collection")
        output = []
        results = cli_interactive._search_card(self.r, make_print_fn(output), "Sol Ring")
        self.assertEqual(len(results), 1)
        self.assertTrue(any("exact match" in line for line in output))


if __name__ == "__main__":
    unittest.main()
