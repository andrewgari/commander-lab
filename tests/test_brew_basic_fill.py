"""StubBrewBuilder tops an exhausted pool up to 100 cards with identity-matching basics."""
from src.server.domain.draft.brew_builder import DECK_SIZE, _fill_basics


def _total(cards):
    return sum(c["quantity"] for c in cards)


def test_fill_basics_splits_over_colors_and_reaches_deck_size():
    cards = [{"name": "Krenko, Mob Boss", "quantity": 1}] * 80
    _fill_basics(cards, DECK_SIZE - 80, ["R", "G"])
    assert _total(cards) == DECK_SIZE
    qty = {c["name"]: c["quantity"] for c in cards[80:]}
    assert qty == {"Mountain": 10, "Forest": 10}


def test_fill_basics_colorless_uses_wastes_and_stacks_existing():
    cards = [{"name": "Wastes", "quantity": 2}]
    _fill_basics(cards, 5, [])
    assert cards == [{"name": "Wastes", "quantity": 7}]


def test_fill_basics_does_not_stack_onto_pool_supplied_basic():
    cards = [{"name": "Mountain", "quantity": 3, "tier": 2}]
    _fill_basics(cards, 10, ["R", "G"])
    by = {c["name"]: c for c in cards}
    assert by["Mountain"]["quantity"] == 3 and by["Mountain"]["tier"] == 2
    assert by["Forest"]["quantity"] == 10 and by["Forest"]["tier"] == 3


def test_fill_basics_separate_tier3_entry_when_all_colors_pooled():
    cards = [{"name": "Mountain", "quantity": 3, "tier": 1}]
    _fill_basics(cards, 4, ["R"])
    assert cards[0]["quantity"] == 3
    assert cards[1] == {**cards[1], "name": "Mountain", "quantity": 4, "tier": 3}
