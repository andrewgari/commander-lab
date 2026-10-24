"""Ad-hoc manual verification script for reconciliation.reconcile_deck.
Not a test file (see tests/ for the real pytest suite) — run directly:
    python3 scripts/_verify_reconciliation.py
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reconciliation import reconcile_deck

decklist = [
    {"name": "Sol Ring", "quantity": 1},
    {"name": "Arcane Signet", "quantity": 1},
    {"name": "Rhystic Study", "quantity": 1, "preferred_set": "wc07", "preferred_collector_number": "2"},
    {"name": "Missing Card", "quantity": 2},
]
instances = [
    {"id": "i1", "card_name": "Sol Ring", "set": "cmr"},
    {"id": "i2", "card_name": "Arcane Signet", "set": "znr"},
    {"id": "i3", "card_name": "Arcane Signet", "set": "znr"},  # overage
    {"id": "i4", "card_name": "Rhystic Study", "set": "jud"},  # wrong printing
    {"id": "i5", "card_name": "Extra Not In List", "set": "m10"},  # pure overage
]

result = reconcile_deck(decklist, instances)
print(json.dumps(result, indent=2))

by_name = {c["card_name"]: c for c in result["cards"]}
assert by_name["Sol Ring"]["status"] == "exact"
assert by_name["Arcane Signet"]["status"] == "overage" and by_name["Arcane Signet"]["overage"] == 1
assert by_name["Missing Card"]["status"] == "unassigned" and by_name["Missing Card"]["shortfall"] == 2
rhystic = by_name["Rhystic Study"]
assert rhystic["status"] == "exact"  # 1 required, 1 assigned (quantity-wise)
assert rhystic["preferred_printing_satisfied"] is False
assert rhystic["wrong_printing_instance_ids"] == ["i4"]
extra = by_name["Extra Not In List"]
assert extra["status"] == "overage" and extra["overage"] == 1

empty = reconcile_deck([], [])
assert empty["cards"] == [] and empty["summary"]["total_cards"] == 0

empty_decklist_with_instances = reconcile_deck([], [{"id": "x", "card_name": "Stray"}])
assert empty_decklist_with_instances["summary"]["overage_cards"] == 1
assert empty_decklist_with_instances["summary"]["total_overage_count"] == 1

print("\nAll manual verification assertions passed.")
