"""Slice 1: profile snapshot metrics + plain-language summary
(domain/profile/snapshot.py)."""
from __future__ import annotations

from src.server.domain.profile import snapshot as snap


def _deck():
    return {"id": 7, "name": "Gruul Smash", "commanders": ["Test Commander"],
            "color_identity": ["R", "G"]}


def _deck_cards():
    return [
        {"oracle_id": "o1", "name": "Cmd", "board": "commander", "qty": 1,
         "category": None, "type_line": "Legendary Creature", "cmc": 4.0, "colors": ["R", "G"]},
        {"oracle_id": "o2", "name": "Ramp A", "board": "main", "qty": 3,
         "category": "Ramp", "type_line": "Sorcery", "cmc": 2.0, "colors": ["G"]},
        {"oracle_id": "o3", "name": "Big Beat", "board": "main", "qty": 2,
         "category": "Finisher", "type_line": "Creature", "cmc": 7.0, "colors": ["R"]},
        {"oracle_id": "o4", "name": "Forest", "board": "main", "qty": 30,
         "category": None, "type_line": "Basic Land — Forest", "cmc": None, "colors": []},
        {"oracle_id": "o5", "name": "Mystery", "board": "main", "qty": 1,
         "category": None, "type_line": "Artifact", "cmc": None, "colors": []},
        {"oracle_id": "o6", "name": "Side", "board": "maybe", "qty": 1,
         "category": "Ramp", "type_line": "Sorcery", "cmc": 1.0, "colors": ["G"]},
    ]


def _have_buy():
    return {"cards": [], "totals": {"required": 37, "have": 30, "pending": 2, "buy": 7,
                                    "have_in_deck": 20, "have_from_collection": 10}}


def test_curve_buckets_lands_excluded_nulls_unavailable():
    metrics = snap.compute_metrics(_deck(), _deck_cards(), _have_buy())
    curve = metrics["curve"]
    assert curve["land_count"] == 30
    assert curve["spell_count"] == 7  # 1 cmd + 3 + 2 + 1 artifact; maybe-board excluded
    assert curve["buckets"]["2"] == 3
    assert curve["buckets"]["4"] == 1
    assert curve["buckets"]["7+"] == 2
    # NULL cmc is reported as unavailable, never counted as 0 (AGENT_RULES §4)
    assert curve["unavailable_cmc_count"] == 1
    assert curve["buckets"]["0"] == 0
    assert curve["avg_cmc"] == round((4.0 + 2.0 * 3 + 7.0 * 2) / 6, 2)


def test_archetype_mix_uses_non_structural_categories():
    metrics = snap.compute_metrics(_deck(), _deck_cards(), _have_buy())
    mix = {m["category"]: m["count"] for m in metrics["archetype_mix"]["mix"]}
    assert mix["Ramp"] == 3          # maybe-board Ramp excluded
    assert mix["Finisher"] == 2
    assert mix["Uncategorized"] == 32  # 30 Forests + commander + Mystery
    assert metrics["archetype_mix"]["total_cards"] == 37


def test_summary_text_plain_language():
    metrics = snap.compute_metrics(_deck(), _deck_cards(), _have_buy())
    text = snap.render_summary(_deck(), metrics)
    assert "Gruul Smash" in text
    assert "Test Commander" in text
    assert "30 lands" in text
    assert "Ramp (3)" in text
    assert "30 of 37" in text and "7 to buy" in text
    assert "Mana cost unavailable for 1 card" in text


def test_inputs_provenance_no_card_data():
    inputs = snap.build_inputs(_deck(), "import")
    assert inputs == {"trigger": "import", "deck_id": 7, "deck_name": "Gruul Smash",
                      "commanders": ["Test Commander"]}
