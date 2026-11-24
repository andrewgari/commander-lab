"""QA probes for PR #533 (brew creature_type bans on canonical records)."""
import json

import pytest

import src.server.domain.draft.brew_rules as br
from tests.test_brew import FakeRedis

ANG = {"kind": "exclude", "field": "creature_type", "values": ["Angel", "Demon", "Dragon"], "label": "x"}


def _viol(rec, name="Card"):
    return br.violates(ANG, br.facts_from_record(name, rec))


def test_sub_types_as_string_is_banned():
    # string instead of list: must still ban Angel
    assert _viol({"type": "Creature", "types": ["Creature"], "super_types": [], "sub_types": "Angel"})


def test_super_types_as_string_does_not_corrupt_type_line():
    assert _viol({"type": "Creature", "types": ["Creature"], "super_types": "Legendary", "sub_types": ["Angel"]})


def test_changeling_all_creature_types_is_kept():
    rec = {"type": "Creature", "types": ["Creature"], "sub_types": [],
           "oracle_text": "Changeling (This card is every creature type.)"}
    # Andrew: changelings stay allowed under a creature-type ban (Kaalia workaround)
    assert not _viol(rec)


def test_dfc_canonical_faces_without_face_type_line_are_banned():
    rec = {"type": "Creature", "types": ["Creature"], "super_types": ["Legendary"], "sub_types": ["Angel"],
           "card_faces": [{"name": "Front"}, {"name": "Back"}]}
    assert _viol(rec), "DFC canonical record with faces lacking type_line leaks Angel"


def test_dfc_back_face_demon_is_banned_via_face_type_line():
    rec = {"type": "Sorcery", "types": ["Sorcery"], "sub_types": ["Demon"],
           "card_faces": [{"name": "Spell", "type_line": "Sorcery"},
                          {"name": "Demon Side", "type_line": "Creature — Demon"}]}
    assert _viol(rec)


def test_type_line_present_is_banned():
    assert _viol({"type_line": "Legendary Creature — Angel", "types": ["Creature"], "sub_types": ["Angel"]})


def test_equipment_and_aura_not_banned():
    assert not _viol({"type": "Artifact", "types": ["Artifact"], "sub_types": ["Equipment"]})
    assert not _viol({"type": "Enchantment", "types": ["Enchantment"], "sub_types": ["Aura"]})


def test_kindred_tribal_angel_banned():
    assert _viol({"type": "Kindred", "types": ["Kindred", "Creature"], "sub_types": ["Angel"]})


@pytest.mark.parametrize("rec", [
    {"type": "Creature", "types": ["Creature"], "sub_types": ["Angel"]},
    {"type": "Creature", "types": ["Creature"], "sub_types": ["Angel"], "card_faces": [{"name": "A"}]},
])
def test_canonical_via_card_facts_redis_lookup(rec):
    r = FakeRedis()
    r.set("card_meta:Lyra", json.dumps(rec))
    f = br.card_facts(r, "Lyra")
    assert br.violates(ANG, f)
