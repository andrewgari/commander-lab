"""Regression: Kaalia brew (sessions brew_3121250bcf10 / brew_6a53646ec391) released Angels, Demons and a
Dragon despite approved creature_type bans. Canonical card records carry ``types`` + ``sub_types`` (no
``type_line``); the matcher ignored ``sub_types`` so no creature_type rule ever matched."""
import json

import pytest

import src.server.domain.draft.brew_rules as br
from tests.test_brew import FakeRedis

RULE = {"kind": "exclude", "field": "creature_type", "values": ["Angel", "Demon", "Dragon"],
        "label": "No Angels, Demons or Dragons"}

# (name, types, super_types, sub_types) exactly as stored in the live canonical records
LEAKED = [
    ("Lyra Dawnbringer", ["Creature"], ["Legendary"], ["Angel"]),
    ("Rune-Scarred Demon", ["Creature"], [], ["Demon"]),
    ("Balefire Dragon", ["Creature"], [], ["Dragon"]),
    ("Drana and Linvala", ["Creature"], ["Legendary"], ["Vampire", "Angel"]),
    ("Akroma, Angel of Wrath", ["Creature"], ["Legendary"], ["Angel"]),
    ("Razaketh, the Foulblooded", ["Creature"], ["Legendary"], ["Demon"]),
    ("Blast-Furnace Hellkite", ["Creature"], [], ["Dragon"]),
]
FINE = [
    ("Sol Ring", ["Artifact"], [], []),
    ("Mother of Runes", ["Creature"], [], ["Human", "Cleric"]),
    ("Kaalia of the Vast", ["Creature"], ["Legendary"], ["Human", "Cleric"]),
]


def _rec(name, types, supers, subs):
    return {"oracle_id": name, "name": name, "type": types[0], "types": types, "super_types": supers,
            "sub_types": subs, "color": "W", "commander_legality": "legal"}


@pytest.fixture
def r():
    x = FakeRedis()
    for row in LEAKED + FINE:
        x.set(f"card_meta:{row[0]}", json.dumps(_rec(*row)))
    return x


def test_canonical_records_with_sub_types_are_banned(r):
    names = [n for n, *_ in LEAKED + FINE]
    kept, removed = br.filter_names([RULE], names, r, protected=["Kaalia of the Vast"])
    assert {x["card"] for x in removed} == {n for n, *_ in LEAKED}
    assert set(kept) == {"Sol Ring", "Mother of Runes", "Kaalia of the Vast"}


def test_draft_violations_finds_leaks(r):
    draft = {"cards": [{"name": n} for n, *_ in LEAKED + FINE]}
    bad = br.draft_violations([RULE], draft, r, ["Kaalia of the Vast"])
    assert {b["card"] for b in bad} == {n for n, *_ in LEAKED}
