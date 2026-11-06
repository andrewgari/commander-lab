"""Unit tests for deck_fill.fill_deck (pure; no Redis/network)."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.server.domain.draft.deck_validator as dv
from src.server.domain.draft.deck_fill import fill_deck
from src.server.domain.draft.deck_validator import (
    CardCatalog, CardInfo, DraftCard, DraftDeck, validate_deck,
)


def card(name, ci="", types=("Artifact",), supers=(), text="", legality="legal"):
    return CardInfo(name=name, color_identity=dv.parse_color_identity(ci), types=tuple(types),
                    supertypes=tuple(supers), oracle_text=text, commander_legality=legality)


def build_catalog():
    cards = [
        card("Atraxa", "WUBG", ("Creature",), ("Legendary",)),
        card("Rakdos Guy", "BR", ("Creature",), ("Legendary",)),
        card("Mono Red Guy", "R", ("Creature",), ("Legendary",)),
        card("Karn Colorless", "", ("Creature",), ("Legendary",)),
        card("Sol Ring"), card("Arcane Signet"), card("Mind Stone"),
        card("Lightning Bolt", "R", ("Instant",)),
        card("Doom Blade", "B", ("Instant",)),
        card("Counterspell", "U", ("Instant",)),
        card("Cultivate", "G", ("Sorcery",)),
        card("Swords to Plowshares", "W", ("Instant",)),
        card("Mana Crypt", "", ("Artifact",), legality="banned"),
        card("Rat Pack", "B", ("Creature",),
             text="A deck can have any number of cards named Rat Pack."),
    ]
    # 60 generic synergy cards per colour so source pools can fill a deck
    for ci in ("", "W", "U", "B", "R", "G"):
        for i in range(30):
            cards.append(card(f"Src {ci or 'C'} {i}", ci, ("Creature",)))
    return CardCatalog(cards)


STAPLES = ["Sol Ring", "Arcane Signet", "Mind Stone", "Mana Crypt", "Lightning Bolt",
           "Doom Blade", "Counterspell", "Cultivate", "Swords to Plowshares", "Not In Catalog"]
CAT = build_catalog()


def src_for(ci_list, n=30):
    return [f"Src {ci or 'C'} {i}" for ci in ci_list for i in range(n)]


def assert_legal_100(commander, out):
    deck = DraftDeck(commanders=[commander], cards=[DraftCard(n, 1) for n in out if n != commander])
    res = validate_deck(deck, CAT)
    assert res.valid, res.to_json()
    assert len(out) == 99 + 1 and out[0] == commander


def boom():
    raise RuntimeError("EDHREC down")


def slow():
    time.sleep(5)
    return ["Src C 0"]


def test_normal_fill_uses_sources_first():
    out = fill_deck("Atraxa", [], [("synergy", src_for(["W", "U"], 30)),
                                   ("edhrec", src_for(["B", "G", "C"], 30))], CAT, staples=STAPLES)
    assert_legal_100("Atraxa", out)
    assert "Sol Ring" not in out  # sources alone suffice; staples untouched
    assert not any(n in dv.BASIC_LAND_NAMES for n in out)


def test_edhrec_down():
    out = fill_deck("Atraxa", [], [("synergy", src_for(["W"], 30)), ("edhrec", boom)],
                    CAT, staples=STAPLES)
    assert_legal_100("Atraxa", out)
    assert "Sol Ring" in out


def test_analytics_down_timeout():
    t = time.time()
    out = fill_deck("Atraxa", [], [("synergy", src_for(["W"], 30)), ("analytics", slow)],
                    CAT, staples=STAPLES, source_timeout=0.2)
    assert time.time() - t < 3
    assert_legal_100("Atraxa", out)


def test_all_sources_down_staples_plus_basics():
    out = fill_deck("Atraxa", [], [("edhrec", boom), ("analytics", boom)], CAT, staples=STAPLES)
    assert_legal_100("Atraxa", out)
    assert "Mana Crypt" not in out and "Not In Catalog" not in out
    assert "Sol Ring" in out
    for b in ("Plains", "Island", "Swamp", "Forest"):
        assert out.count(b) > 1 and abs(out.count(b) - out.count("Plains")) <= 1  # 4-colour split


def test_no_sources_at_all():
    assert_legal_100("Atraxa", fill_deck("Atraxa", [], None, CAT, staples=STAPLES))


def test_mono_color_commander_only_on_color():
    out = fill_deck("Mono Red Guy", [], [("edhrec", boom)], CAT, staples=STAPLES)
    assert_legal_100("Mono Red Guy", out)
    assert "Lightning Bolt" in out
    assert "Doom Blade" not in out and "Counterspell" not in out
    assert out.count("Mountain") > 80 and "Island" not in out


def test_colorless_commander_uses_wastes():
    out = fill_deck("Karn Colorless", [], [("a", boom), ("b", src_for(["R", "U"]))],
                    CAT, staples=STAPLES)
    assert_legal_100("Karn Colorless", out)
    assert out.count("Wastes") > 80
    assert not any(n in out for n in ("Plains", "Island", "Swamp", "Mountain", "Forest"))
    assert "Lightning Bolt" not in out


def test_dirty_inputs_and_dupes_are_cleaned():
    dirty = ["Sol Ring", "Sol Ring", "Mana Crypt", "Lightning Bolt", "Doom Blade", "Rat Pack",
             "Rat Pack", "Mountain", "Mountain", "Garbage Name"]
    out = fill_deck("Rakdos Guy", dirty, [("s", ["Sol Ring", "Doom Blade", "Src R 0", "Src R 0"])],
                    CAT, staples=STAPLES)
    assert_legal_100("Rakdos Guy", out)
    assert out.count("Sol Ring") == 1 and out.count("Rat Pack") == 2
    assert "Mana Crypt" not in out and "Garbage Name" not in out


def test_oversized_input_is_trimmed_and_deterministic():
    big = src_for(["R"], 30) + src_for(["C"], 30) + src_for(["B"], 30)
    out = fill_deck("Rakdos Guy", big, None, CAT, staples=STAPLES)
    assert_legal_100("Rakdos Guy", out)
    assert out == fill_deck("Rakdos Guy", big, None, CAT, staples=STAPLES)


def test_unknown_commander_raises():
    with pytest.raises(ValueError):
        fill_deck("Nobody", [], None, CAT, staples=STAPLES)


def test_illegal_commanders_rejected():
    for bad in ("Sol Ring", "Mana Crypt"):
        with pytest.raises(ValueError):
            fill_deck(bad, [], None, CAT, staples=STAPLES)


def test_basic_record_without_legality_does_not_shorten_deck():
    cat = build_catalog()
    cat.add(card("Mountain", "R", ("Land",), ("Basic",), legality=None))
    out = fill_deck("Mono Red Guy", [], None, cat, staples=[])
    assert len(out) == 100 and out.count("Mountain") == 99


def test_generator_source_is_bounded_by_timeout():
    def gen():
        time.sleep(5)
        yield "Src C 0"

    def lazy():
        def g():
            time.sleep(5)
            yield "Src C 0"
        return g()

    t = time.time()
    out = fill_deck("Atraxa", [], [("lazy", lazy)], CAT, staples=STAPLES, source_timeout=0.2)
    assert time.time() - t < 3
    assert_legal_100("Atraxa", out)


def test_hung_sources_do_not_spawn_unbounded_threads():
    import threading
    before = threading.active_count()
    ev = threading.Event()
    for _ in range(12):
        fill_deck("Atraxa", [], [("hung", lambda: ev.wait(10))], CAT, staples=STAPLES,
                  source_timeout=0.01)
    assert threading.active_count() - before <= 4
    ev.set()


def test_scheme_card_from_source_never_enters_mainboard():
    cat = build_catalog()
    cat.add(card("Evil Scheme", "", ("Scheme",), ("Ongoing",)))
    out = fill_deck("Atraxa", ["Evil Scheme"], [("s", ["Evil Scheme"])], cat, staples=["Evil Scheme"])
    assert "Evil Scheme" not in out and len(out) == 100


def test_commander_in_cards_or_sources_is_not_duplicated():
    out = fill_deck("Atraxa", ["Atraxa"], [("s", ["Atraxa", "atraxa"])], CAT, staples=["Atraxa"])
    assert out.count("Atraxa") == 1 and len(out) == 100


def test_banned_or_unknown_legality_commander_rejected():
    cat = build_catalog()
    cat.add(card("Banned Legend", "R", ("Creature",), ("Legendary",), legality="banned"))
    cat.add(card("Unknown Legend", "R", ("Creature",), ("Legendary",), legality=None))
    for bad in ("Banned Legend", "Unknown Legend"):
        with pytest.raises(ValueError, match="legal"):
            fill_deck(bad, [], None, cat, staples=STAPLES)


def test_variant_type_commander_rejected_by_commander_slot_gate():
    cat = build_catalog()
    # legal + legendary creature (so eligibility passes) but also a Scheme: only the slot gate stops it
    cat.add(card("Scheme Legend", "R", ("Creature", "Scheme"), ("Legendary",)))
    with pytest.raises(ValueError, match="commander slot"):
        fill_deck("Scheme Legend", [], None, cat, staples=STAPLES)
