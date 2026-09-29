"""
Unit tests for the pure deck reconciliation diff engine (reconciliation.py).

Run: python -m unittest tests/test_reconciliation.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reconciliation import reconcile_deck


def _card(key, name, **extra):
    d = {"card_key": key, "card_name": name}
    d.update(extra)
    return d


def _find(result, card_name):
    for card in result["cards"]:
        if card["card_name"] == card_name:
            return card
    raise AssertionError(f"card {card_name!r} not found in result: {result}")


class ReconcileDeckTests(unittest.TestCase):
    def test_exact_match(self):
        decklist = [{"name": "Sol Ring", "quantity": 1}]
        instances = [{"id": "i1", "name": "Sol Ring"}]

        result = reconcile_deck(decklist, instances)

        card = _find(result, "Sol Ring")
        self.assertEqual(card["required"], 1)
        self.assertEqual(card["assigned"], 1)
        self.assertEqual(card["shortfall"], 0)
        self.assertEqual(card["overage"], 0)
        self.assertEqual(card["status"], "exact")
        self.assertEqual(result["summary"]["total_cards"], 1)
        self.assertEqual(result["summary"]["exact_matches"], 1)
        self.assertEqual(result["summary"]["shortfall_cards"], 0)
        self.assertEqual(result["summary"]["overage_cards"], 0)

    def test_shortfall(self):
        decklist = [{"name": "Arcane Signet", "quantity": 2}]
        instances = [{"id": "i1", "name": "Arcane Signet"}]

        result = reconcile_deck(decklist, instances)

        card = _find(result, "Arcane Signet")
        self.assertEqual(card["required"], 2)
        self.assertEqual(card["assigned"], 1)
        self.assertEqual(card["shortfall"], 1)
        self.assertEqual(card["overage"], 0)
        self.assertEqual(card["status"], "shortfall")
        self.assertEqual(result["summary"]["shortfall_cards"], 1)
        self.assertEqual(result["summary"]["total_shortfall_count"], 1)

    def test_unassigned_shortfall(self):
        decklist = [{"name": "Command Tower", "quantity": 1}]
        instances = []

        result = reconcile_deck(decklist, instances)

        card = _find(result, "Command Tower")
        self.assertEqual(card["assigned"], 0)
        self.assertEqual(card["shortfall"], 1)
        self.assertEqual(card["status"], "unassigned")

    def test_overage(self):
        decklist = [{"name": "Swamp", "quantity": 1}]
        instances = [
            {"id": "i1", "name": "Swamp"},
            {"id": "i2", "name": "Swamp"},
        ]

        result = reconcile_deck(decklist, instances)

        card = _find(result, "Swamp")
        self.assertEqual(card["required"], 1)
        self.assertEqual(card["assigned"], 2)
        self.assertEqual(card["overage"], 1)
        self.assertEqual(card["status"], "overage")
        self.assertCountEqual(card["assigned_instance_ids"], ["i1", "i2"])
        self.assertEqual(result["summary"]["overage_cards"], 1)
        self.assertEqual(result["summary"]["total_overage_count"], 1)

    def test_pure_overage_not_in_decklist(self):
        decklist = []
        instances = [{"id": "i1", "name": "Island"}]

        result = reconcile_deck(decklist, instances)

        card = _find(result, "Island")
        self.assertEqual(card["required"], 0)
        self.assertEqual(card["assigned"], 1)
        self.assertEqual(card["status"], "overage")

    def test_wrong_printing_flags_but_still_counts_assigned(self):
        decklist = [{
            "name": "Lightning Bolt",
            "quantity": 1,
            "preferred_set": "lea",
            "preferred_collector_number": "161",
        }]
        instances = [{
            "id": "i1",
            "name": "Lightning Bolt",
            "set": "m10",
            "collector_number": "146",
        }]

        result = reconcile_deck(decklist, instances)

        card = _find(result, "Lightning Bolt")
        self.assertEqual(card["required"], 1)
        self.assertEqual(card["assigned"], 1)
        self.assertEqual(card["shortfall"], 0)
        self.assertEqual(card["status"], "exact")
        self.assertFalse(card["preferred_printing_satisfied"])
        self.assertEqual(card["wrong_printing_instance_ids"], ["i1"])
        self.assertEqual(card["preferred_printing"], {"set": "lea", "collector_number": "161"})

    def test_preferred_printing_satisfied_when_matching(self):
        decklist = [{
            "name": "Lightning Bolt",
            "quantity": 1,
            "preferred_set": "lea",
            "preferred_collector_number": "161",
        }]
        instances = [{
            "id": "i1",
            "name": "Lightning Bolt",
            "set": "lea",
            "collector_number": "161",
        }]

        result = reconcile_deck(decklist, instances)

        card = _find(result, "Lightning Bolt")
        self.assertTrue(card["preferred_printing_satisfied"])
        self.assertEqual(card["wrong_printing_instance_ids"], [])

    def test_empty_decklist_and_empty_instances(self):
        result = reconcile_deck([], [])

        self.assertEqual(result["cards"], [])
        self.assertEqual(result["summary"]["total_cards"], 0)
        self.assertEqual(result["summary"]["exact_matches"], 0)
        self.assertEqual(result["summary"]["shortfall_cards"], 0)
        self.assertEqual(result["summary"]["overage_cards"], 0)
        self.assertEqual(result["summary"]["total_shortfall_count"], 0)
        self.assertEqual(result["summary"]["total_overage_count"], 0)

    def test_empty_decklist_with_instances_is_all_overage(self):
        result = reconcile_deck(None, [{"id": "i1", "name": "Forest"}])

        self.assertEqual(result["summary"]["total_cards"], 1)
        self.assertEqual(result["summary"]["overage_cards"], 1)

    def test_oracle_id_used_only_when_present_on_both_sides(self):
        # Decklist has oracle_id, instance does not -> must fall back to
        # name matching so they group under the SAME key instead of
        # splitting into oracle:... vs name:... groups.
        decklist = [{"name": "Sol Ring", "quantity": 1, "oracle_id": "abc-123"}]
        instances = [{"id": "i1", "name": "Sol Ring"}]

        result = reconcile_deck(decklist, instances)

        self.assertEqual(len(result["cards"]), 1)
        card = result["cards"][0]
        self.assertEqual(card["required"], 1)
        self.assertEqual(card["assigned"], 1)
        self.assertEqual(card["status"], "exact")
        self.assertTrue(card["card_key"].startswith("name:"))

    def test_oracle_id_used_when_present_on_both_sides(self):
        decklist = [{"name": "Sol Ring", "quantity": 1, "oracle_id": "abc-123"}]
        instances = [{"id": "i1", "name": "Sol Ring", "oracle_id": "abc-123"}]

        result = reconcile_deck(decklist, instances)

        self.assertEqual(len(result["cards"]), 1)
        card = result["cards"][0]
        self.assertEqual(card["status"], "exact")
        self.assertTrue(card["card_key"].startswith("oracle:"))

    def test_mismatched_oracle_ids_do_not_collapse_different_cards(self):
        # Different oracle_ids present on both sides but for different
        # cards should not incorrectly merge.
        decklist = [
            {"name": "Sol Ring", "quantity": 1, "oracle_id": "sol-ring-id"},
            {"name": "Mana Crypt", "quantity": 1, "oracle_id": "mana-crypt-id"},
        ]
        instances = [
            {"id": "i1", "name": "Sol Ring", "oracle_id": "sol-ring-id"},
        ]

        result = reconcile_deck(decklist, instances)

        self.assertEqual(len(result["cards"]), 2)
        sol_ring = _find(result, "Sol Ring")
        mana_crypt = _find(result, "Mana Crypt")
        self.assertEqual(sol_ring["status"], "exact")
        self.assertEqual(mana_crypt["status"], "unassigned")
        self.assertEqual(mana_crypt["assigned"], 0)


if __name__ == "__main__":
    unittest.main()
