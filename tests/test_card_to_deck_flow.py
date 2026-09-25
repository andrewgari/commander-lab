"""
Integration coverage for the card-lookup -> available-instance-filter ->
deck-assignment flow (cards.py + instances.py working together), plus the
error-handling gaps not covered by test_cards.py / test_instances.py in
isolation: transitioning/reading instances that don't exist, and looking
up a card that was never registered in the oracle_id ORM.
"""
import json
import unittest

import cards
import instances as instance_store
from instances import InstanceError

from tests.test_instances import FakeRedis


class TestCardLookupToDeckAssignmentFlow(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_full_flow_lookup_filter_assign(self):
        # 1. Register the canonical card record (as sync.py would).
        cards.upsert_card(
            self.r, {"oracle_id": "abc-123", "name": "Sol Ring", "type_line": "Artifact"}
        )
        card = cards.get_card_by_name(self.r, "Sol Ring")
        self.assertIsNotNone(card)
        self.assertEqual(card["oracle_id"], "abc-123")

        # 2. Create a mix of instances; only in_collection ones are assignable.
        instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="not_owned")
        available = instance_store.create_instance(
            self.r, card_name="Sol Ring", ownership_status="in_collection"
        )
        instance_store.create_instance(
            self.r, card_name="Sol Ring", ownership_status="in_deck", deck_id=1, deck_name="Other Deck"
        )

        candidates = instance_store.list_instances(
            self.r, card_name=card["name"], ownership_status="in_collection"
        )
        self.assertEqual([c["id"] for c in candidates], [available["id"]])

        # 3. Assign the chosen instance into a deck.
        assigned = instance_store.transition_status(
            self.r, candidates[0]["id"], "in_deck", deck_id=42, deck_name="Meren"
        )
        self.assertEqual(assigned["ownership_status"], "in_deck")
        self.assertEqual(assigned["deck_id"], 42)

        # It's no longer offered as an in_collection candidate for this card.
        remaining = instance_store.list_instances(
            self.r, card_name=card["name"], ownership_status="in_collection"
        )
        self.assertEqual(remaining, [])

    def test_get_card_by_name_missing_from_orm_returns_none(self):
        # Card never upserted into the oracle_id ORM (e.g. legacy card_meta
        # only) -- callers must handle None and fall back, not KeyError.
        self.assertIsNone(cards.get_card_by_name(self.r, "Some Unregistered Card"))

    def test_list_instances_for_card_with_no_available_instances(self):
        instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="not_owned")
        candidates = instance_store.list_instances(
            self.r, card_name="Sol Ring", ownership_status="in_collection"
        )
        self.assertEqual(candidates, [])

    def test_transition_status_nonexistent_instance_raises(self):
        with self.assertRaises(InstanceError):
            instance_store.transition_status(self.r, "does-not-exist", "in_deck", deck_id=1)

    def test_get_instance_nonexistent_returns_none(self):
        self.assertIsNone(instance_store.get_instance(self.r, "does-not-exist"))

    def test_assign_requires_deck_id_even_with_valid_candidate(self):
        instance = instance_store.create_instance(
            self.r, card_name="Sol Ring", ownership_status="in_collection"
        )
        with self.assertRaises(InstanceError):
            instance_store.transition_status(self.r, instance["id"], "in_deck")


if __name__ == "__main__":
    unittest.main()
