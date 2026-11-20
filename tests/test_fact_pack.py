import json
from pathlib import Path

from src.server.domain.analytics.fact_pack import build_fact_pack

FIXTURES = Path(__file__).parent / "fixtures"


def test_fact_pack_combines_scryfall_and_recorded_edhrec_data():
    edhrec = json.loads((FIXTURES / "edhrec_atraxa_commander_page.json").read_text())
    deck = {
        "commanders": [{"name": "Atraxa, Praetors' Voice"}],
        "cards": [{"name": "Sol Ring", "quantity": 1}, {"name": "Mystery Card"}],
    }

    def scryfall(name):
        return {
            "name": name, "cmc": 1.0, "type_line": "Artifact",
            "colors": [], "color_identity": [],
            "legalities": {"commander": "legal"},
            "prices": {"usd": "1.23"},
        }

    result = build_fact_pack(deck, {
        "scryfall": scryfall,
        "edhrec": lambda _deck: edhrec,
        "salt": lambda _deck: {"salt_score": 1.72},
    })

    assert [card["name"] for card in result["cards"]] == [
        "Atraxa, Praetors' Voice", "Sol Ring", "Mystery Card"
    ]
    atraxa = result["cards"][0]
    assert atraxa["cmc"] == 1.0
    assert atraxa["color_identity"] == []
    assert atraxa["legalities"] == {"commander": "legal"}
    assert atraxa["price"] == "1.23"
    assert atraxa["salt"] == 1.72
    assert atraxa["edhrec"]["num_decks"] == 43941
    assert atraxa["edhrec"]["rank"] == 4
    assert result["cards"][1]["edhrec"]["num_decks"] == 43000


def test_missing_scryfall_data_has_explicit_unavailable_marker():
    result = build_fact_pack(
        {"cards": [{"name": "Unknown Card"}]},
        {"scryfall": lambda _name: None},
    )
    card = result["cards"][0]
    assert card["oracle"]["status"] == "unavailable"
    assert card["oracle"]["reason"]
    assert card["price"]["status"] == "unavailable"
    assert card["legalities"]["status"] == "unavailable"


def test_card_tag_and_salt_fetchers_are_optional_and_visible():
    deck = {"cards": [{"name": "Sol Ring"}]}
    result = build_fact_pack(deck, {
        "scryfall": lambda name: {"name": name, "cmc": 1, "type_line": "Artifact",
                                  "colors": [], "color_identity": [], "legalities": {},
                                  "prices": {}},
        "tags": lambda name: ["ramp", "artifact"] if name == "Sol Ring" else [],
        "salt": lambda _deck: {"cards": {"Sol Ring": 0.5}},
        "edhrec": lambda _deck: {},
    })
    card = result["cards"][0]
    assert card["tags"] == ["ramp", "artifact"]
    assert card["salt"] == 0.5
    assert card["edhrec"]["status"] == "unavailable"


def test_holy_hellfire_fixture_deck_resolves_every_card():
    """Both recorded fixtures are exercised: this one uses holy_hellfire_cards.json."""
    fixture = json.loads((FIXTURES / "holy_hellfire_cards.json").read_text())
    deck = fixture["deck"]

    def scryfall(name):
        return {
            "name": name, "cmc": 2.0, "type_line": "Card",
            "colors": [], "color_identity": [],
            "legalities": {"commander": "legal"},
            "prices": {"usd": "0.10"},
        }

    result = build_fact_pack(deck, {"scryfall": scryfall})

    expected_names = list(dict.fromkeys(
        list(deck["commanders"]) + [c["name"] for c in deck["cards"]]
    ))
    assert [card["name"] for card in result["cards"]] == expected_names
    for card in result["cards"]:
        assert card["oracle"]["type_line"] == "Card"
        assert card["price"] == "0.10"
        assert card["legalities"] == {"commander": "legal"}
        # No edhrec/salt fetchers supplied -> explicit markers, never None.
        assert card["edhrec"]["status"] == "unavailable"
        assert card["salt"]["status"] == "unavailable"
