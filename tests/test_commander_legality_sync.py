"""Regression: sync must not store/erase commander legality as None (Kaalia of the Vast)."""
import json

import src.server.domain.inventory.cards as cards
from src.server.domain.draft.deck_fill import fill_deck
from src.server.domain.draft.deck_validator import CardCatalog, CardInfo

KAALIA = "9b0fc3b0-0000-4000-8000-000000000001"


class FakeRedis:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v):
        self.d[k] = v

    def pipeline(self):
        outer = self

        class P:
            def __init__(self):
                self.ops = []

            def set(self, k, v):
                self.ops.append((k, v))

            def execute(self):
                for k, v in self.ops:
                    outer.d[k] = v
        return P()


def _rec(legality):
    return {"oracle_id": KAALIA, "name": "Kaalia of the Vast", "color": "WBR",
            "types": ["Creature"], "super_types": ["Legendary"], "sub_types": ["Human", "Cleric"],
            "oracle_text": "", "commander_legality": legality, "power": "2", "toughness": "2"}


def test_legality_parsing():
    assert cards.commander_legality_of({"legalities": {"commander": "legal"}}) == "legal"
    assert cards.commander_legality_of({"legalities": {"Commander": " Legal "}}) == "legal"
    assert cards.commander_legality_of({"legalities": {"commander": None}}) is None
    assert cards.commander_legality_of({"legalities": None}) is None
    assert cards.commander_legality_of({}) is None
    assert cards.commander_legality_of(None) is None


def test_missing_legality_does_not_overwrite_known():
    r = FakeRedis()
    cards.upsert_card(r, _rec("legal"))
    cards.upsert_card(r, _rec(None))  # later deck/printing without legalities
    assert cards.get_card(r, KAALIA)["commander_legality"] == "legal"


def test_missing_legality_is_logged(caplog):
    with caplog.at_level("WARNING"):
        cards.upsert_card(FakeRedis(), _rec(None))
    assert "legality missing" in caplog.text


def test_kaalia_with_legality_passes_deck_fill_check():
    r = FakeRedis()
    cards.upsert_card(r, _rec("legal"))
    cards.upsert_card(r, _rec(None))
    info = CardInfo.from_record(cards.get_card(r, KAALIA))
    cat = CardCatalog([info])
    # Gets past the legality gate (may fail later only for lack of other cards).
    try:
        fill_deck("Kaalia of the Vast", [], None, cat)
    except ValueError as e:
        assert "not Commander-legal" not in str(e)
