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
