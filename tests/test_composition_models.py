"""Tests for composition models, store, and auto-selection (composition-v1)."""
from __future__ import annotations

import datetime
import json
import pytest
from tests.test_interactions import FakeRedis

from src.server.domain.analytics.composition_models import (
    # Constants
    SPEC_VERSION, DECK_SIZE, MAX_USER_PRESETS, MAX_SLOTS, MAX_OVERRIDES,
    MIN_PLAN_ROOM, MAX_NAME, MAX_DESCRIPTION, MAX_WHY,
    KIND_USER, CONFIDENCES, MODE_AUTO, MODE_PINNED,
    CORE_IDS, GROUP_IDS, TYPE_IDS, FACET_IDS, FLAG_IDS, FLOORS, SUPERSETS,
    # Refs
    normalize_ref, check_ref,
    # Helpers
    collapse_ws, spells_for, slot_bounds, band_of, plan_room,
    # Legacy
    LEGACY_ROW_REFS,
    # Validation
    issue, errors_of, validate_preset, validate_auto_select, validate_deck_composition,
    normalize_preset, user_preset_id,
    # Exceptions
    CompositionValidationError,
    # Auto-selection
    resolve_preset, default_deck_composition,
    legacy_override_to_record,
)
from src.server.domain.analytics.composition_store import (
    PRESET_KEY, DECK_KEY,
    list_presets, get_preset, create_preset, update_preset, delete_preset,
    get_deck_composition, set_deck_composition,
    migrate_legacy_composition, migrate_all_legacy_compositions,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slot(ref, target=None, min=None, max=None, why=""):  # noqa: A002
    return {"ref": ref, "target": target, "min": min, "max": max, "why": why, "pillar_id": None}


def _preset(pid, name, lands, slots, **kw):
    return {
        "id": pid, "schema": SPEC_VERSION, "kind": KIND_USER, "name": name,
        "description": kw.get("description", ""), "source": "test", "confidence": "heuristic",
        "based_on": None,
        "structure": {"deck_size": DECK_SIZE, "commanders": 1, "lands": lands},
        "slots": slots, "auto_select": kw.get("auto_select", {"archetypes": [], "themes": [],
                                                              "commander_mv_min": None, "priority": 0}),
        "version": 1, "created_at": None, "updated_at": None,
    }


# Literal fixture presets (not production data): used only to give the
# presetgen-style auto-selection tests concrete things to match against.
TOKENS_PRESET = _preset("fx-go-wide", "Go Wide (fixture)", 36,
                        [_slot("core:ramp", 10), _slot("core:card_advantage", 10),
                         _slot("group:targeted_interaction", 10), _slot("core:board_wipe", max=1)],
                        auto_select={"archetypes": ["tokens", "aristocrats", "tribal"],
                                     "themes": ["tokens", "sacrifice", "kindred:*"],
                                     "commander_mv_min": None, "priority": 0})
CONTROL_PRESET = _preset("fx-control", "Control (fixture)", 38,
                         [_slot("core:ramp", 10), _slot("core:card_advantage", 12),
                          _slot("group:targeted_interaction", 15), _slot("core:board_wipe", 5)],
                         auto_select={"archetypes": ["control", "stax", "pillowfort"], "themes": [],
                                      "commander_mv_min": None, "priority": 0})
GRAVEYARD_PRESET = _preset("fx-graveyard", "Graveyard (fixture)", 37,
                           [_slot("core:ramp", 10), _slot("core:card_advantage", 8),
                            _slot("core:recursion", 8), _slot("group:targeted_interaction", 10),
                            _slot("core:board_wipe", 2), _slot("theme:graveyard", min=10)],
                           auto_select={"archetypes": ["graveyard", "reanimator"], "themes": ["graveyard"],
                                        "commander_mv_min": None, "priority": 0})
DEFAULT_PRESET = _preset("fx-default", "Default (fixture)", 38,
                         [_slot("core:ramp", 10), _slot("core:card_advantage", 12),
                          _slot("group:targeted_interaction", 12), _slot("core:board_wipe", 6)])
ALL_FIXTURES = [TOKENS_PRESET, CONTROL_PRESET, GRAVEYARD_PRESET, DEFAULT_PRESET]


# ---------------------------------------------------------------------------
# Refs & helpers
# ---------------------------------------------------------------------------

class TestRefs:
    def test_normalize_lands(self):
        assert normalize_ref("core:lands") == "structure:lands"

    def test_normalize_passthrough(self):
        assert normalize_ref("core:ramp") == "core:ramp"

    def test_check_ref_valid_core(self):
        for cid in CORE_IDS:
            assert check_ref(f"core:{cid}") is None

    def test_check_ref_valid_group(self):
        for gid in GROUP_IDS:
            assert check_ref(f"group:{gid}") is None

    def test_check_ref_valid_theme(self):
        assert check_ref("theme:tokens") is None

    def test_check_ref_kindred_theme(self):
        assert check_ref("theme:kindred:Elf") is None  # type: ignore[unreachable]

    def test_check_ref_bad_kind(self):
        bad = check_ref("zone:graveyard")
        assert bad["code"] == "E3"

    def test_check_ref_structure_rejected(self):
        bad = check_ref("structure:lands")
        assert bad["code"] == "E4"

    def test_check_ref_flag_rejected(self):
        bad = check_ref("flag:game_changer")
        assert bad["code"] == "E3"

    def test_check_ref_derived_plan_rejected(self):
        bad = check_ref("derived:plan")
        assert bad["code"] == "E3"

    def test_collapsed_whitespace(self):
        assert collapse_ws("  hello   world  ") == "hello world"

    def test_spells_for(self):
        assert spells_for({"commanders": 1, "lands": 38}) == 61

    def test_slot_bounds_with_target_only(self):
        assert slot_bounds({"target": 10}) == (10, 10)

    def test_slot_bounds_with_min_max(self):
        assert slot_bounds({"min": 8, "max": 12}) == (8, 12)

    def test_band_of(self):
        assert band_of({"target": 10}) == (9, 11)
        assert band_of({"min": 8, "max": 12}) == (8, 12)

    def test_user_preset_id(self):
        uid = user_preset_id("My Cool Deck", "abc123")
        assert uid.startswith("p-") and uid.endswith("abc123")


# ---------------------------------------------------------------------------
# Preset validation
# ---------------------------------------------------------------------------

class TestPresetValidation:
    def _valid(self, **kw):
        return _preset("test-id", "Test", 38, [_slot("core:ramp", 10)], **kw)

    def test_valid_preset(self):
        p = self._valid()
        issues = validate_preset(p)
        assert not errors_of(issues)

    def test_empty_name(self):
        p = self._valid()
        p["name"] = ""
        issues = validate_preset(p)
        assert any(i["code"] == "E1" for i in issues)

    def test_name_too_long(self):
        p = self._valid()
        p["name"] = "x" * 41
        issues = validate_preset(p)
        assert any(i["code"] == "E1" for i in issues)

    def test_duplicate_name(self):
        p1 = self._valid()
        p1["id"] = "dup-a"
        p2 = self._valid()
        p2["id"] = "dup-b"
        issues = validate_preset(p2, [p1])
        assert any(i["code"] == "E1" for i in issues)

    def test_no_reserved_ids(self):
        """Every preset is a plain user record now; no id is reserved."""
        p = self._valid()
        p["id"] = "anything-goes"
        issues = validate_preset(p)
        assert not any(i["code"] == "E1" and "reserved" in i["message"] for i in issues)

    def test_bad_confidence(self):
        p = self._valid()
        p["confidence"] = "magic"
        issues = validate_preset(p)
        assert any(i["code"] == "E1" for i in issues)

    def test_bad_deck_size(self):
        p = self._valid()
        p["structure"]["deck_size"] = 99
        issues = validate_preset(p)
        assert any(i["code"] == "E2" for i in issues)

    def test_bad_commanders(self):
        p = self._valid()
        p["structure"]["commanders"] = 3
        issues = validate_preset(p)
        assert any(i["code"] == "E2" for i in issues)

    def test_lands_warning(self):
        p = self._valid()
        p["structure"]["lands"] = 30
        issues = validate_preset(p)
        assert any(i["code"] == "W1" for i in issues)

    def test_bad_ref(self):
        p = self._valid()
        p["slots"][0]["ref"] = "bogus:thing"
        issues = validate_preset(p)
        assert any(i["code"] == "E3" for i in issues)

    def test_duplicate_ref(self):
        p = self._valid()
        p["slots"].append(_slot("core:ramp", 5))
        issues = validate_preset(p)
        assert any(i["code"] == "E4" for i in issues)

    def test_slot_numbers_out_of_range(self):
        p = self._valid()
        p["slots"][0]["target"] = 100
        issues = validate_preset(p)
        assert any(i["code"] == "E5" for i in issues)

    def test_slot_no_values(self):
        p = self._valid()
        p["slots"][0] = {"ref": "core:ramp", "why": ""}
        issues = validate_preset(p)
        assert any(i["code"] == "E5" for i in issues)

    def test_slot_min_gt_max(self):
        p = self._valid()
        p["slots"][0] = _slot("core:ramp", min=12, max=10)
        issues = validate_preset(p)
        assert any(i["code"] == "E5" for i in issues)

    def test_lower_exceeds_spells(self):
        p = self._valid()
        p["structure"]["lands"] = 40
        p["slots"].append(_slot("core:card_advantage", min=65))
        issues = validate_preset(p)
        assert any(i["code"] == "E6" for i in issues)

    def test_subset_superset_conflict(self):
        p = self._valid()
        p["slots"] = [_slot("core:removal", min=15), _slot("group:targeted_interaction", max=10)]
        issues = validate_preset(p)
        assert any(i["code"] == "E7" for i in issues)

    def test_too_many_slots(self):
        p = self._valid()
        p["slots"] = [_slot(f"core:{cid}", i) for i, cid in enumerate(CORE_IDS)]
        while len(p["slots"]) < MAX_SLOTS + 1:
            p["slots"].append(_slot("core:ramp", 0))
        issues = validate_preset(p)
        assert any(i["code"] == "E8" for i in issues)

    def test_floor_warning(self):
        p = self._valid()
        p["slots"][0] = _slot("core:ramp", max=5)
        issues = validate_preset(p)
        assert any(i["code"] == "W2" for i in issues)

    def test_plan_room_warning(self):
        p = self._valid()
        p["slots"] = [_slot("core:ramp", 10), _slot("core:card_advantage", 10),
                      _slot("group:targeted_interaction", 10), _slot("core:board_wipe", 2),
                      _slot("core:protection", 10), _slot("core:finisher", 10),
                      _slot("type:creature", 10)]
        issues = validate_preset(p)
        assert any(i["code"] == "W3" for i in issues)

    def test_plan_room_ok(self):
        p = self._valid()
        p["slots"] = [_slot("core:ramp", 10), _slot("core:card_advantage", 12),
                      _slot("group:targeted_interaction", 12), _slot("core:board_wipe", 6)]
        issues = validate_preset(p)
        assert not any(i["code"] in ("W3", "A3") for i in issues)

    def test_normalize_preset(self):
        raw = {"name": "  Test  ", "slots": [{"ref": "core:lands", "target": 38}]}
        norm = normalize_preset(raw)
        assert norm["name"] == "Test"
        assert norm["slots"][0]["ref"] == "structure:lands"
        assert norm["kind"] == KIND_USER


# ---------------------------------------------------------------------------
# Auto-selection
# ---------------------------------------------------------------------------

class TestAutoSelection:
    def test_pinned(self):
        rec = {"mode": MODE_PINNED, "preset_id": "fx-default"}
        result = resolve_preset(rec, ALL_FIXTURES)
        assert result["preset_id"] == "fx-default"
        assert result["mode"] == MODE_PINNED
        assert result["reason"]["step"] == 1

    def test_pinned_missing_no_fallback(self):
        """A pinned preset that no longer exists -> no preset selected, not a hardcoded default."""
        rec = {"mode": MODE_PINNED, "preset_id": "nonexistent"}
        result = resolve_preset(rec, [])
        assert result["preset_id"] is None
        assert any(i["code"] == "I3" for i in result["issues"])

    def test_primary_archetype(self):
        journal = {"archetype_preferences": {"primary_archetype": "tokens"}}
        result = resolve_preset(None, ALL_FIXTURES, journal=journal)
        assert result["preset_id"] == "fx-go-wide"
        assert result["reason"]["step"] == 2

    def test_secondary_archetype(self):
        journal = {"archetype_preferences": {"secondary_archetypes": ["control"]}}
        result = resolve_preset(None, ALL_FIXTURES, journal=journal)
        assert result["preset_id"] == "fx-control"
        assert result["reason"]["step"] == 3

    def test_deck_tag_match(self):
        result = resolve_preset(None, ALL_FIXTURES, deck_tags=["graveyard"])
        assert result["preset_id"] == "fx-graveyard"
        assert result["reason"]["step"] == 4

    def test_no_preset_selected_when_nothing_matches(self):
        """No signal matches and there's no hardcoded default: preset_id is None."""
        result = resolve_preset(None, [], journal={}, deck_tags=[])
        assert result["preset_id"] is None
        assert result["reason"]["step"] == 6

    def test_no_preset_selected_even_with_user_presets_present(self):
        result = resolve_preset(None, ALL_FIXTURES, journal={}, deck_tags=[])
        assert result["preset_id"] is None

    def test_user_preset_priority(self):
        up = _preset("custom-tokens", "Custom Tokens", 36, [_slot("core:ramp", 10)],
                     auto_select={"archetypes": ["tokens"], "themes": [],
                                  "commander_mv_min": None, "priority": 10})
        result = resolve_preset(None, [up] + ALL_FIXTURES,
                                journal={"archetype_preferences": {"primary_archetype": "tokens"}})
        assert result["preset_id"] == "custom-tokens"

    def test_legacy_phantom_midrange(self):
        journal = {"archetype_preferences": {"primary_archetype": "midrange"}}
        result = resolve_preset(None, [], journal=journal)
        assert result["reason"]["step"] == 6  # falls through, no match


# ---------------------------------------------------------------------------
# Deck composition record validation
# ---------------------------------------------------------------------------

class TestDeckCompositionValidation:
    def _by_id(self):
        return {p["id"]: p for p in ALL_FIXTURES}

    def test_auto_mode_valid(self):
        rec = default_deck_composition()
        issues = validate_deck_composition(rec, self._by_id())
        assert not errors_of(issues)

    def test_auto_mode_rejects_data(self):
        rec = {"mode": MODE_AUTO, "preset_id": "fx-default"}
        issues = validate_deck_composition(rec, self._by_id())
        assert errors_of(issues)

    def test_pinned_valid(self):
        rec = {"mode": MODE_PINNED, "preset_id": "fx-default", "lands": None, "overrides": []}
        issues = validate_deck_composition(rec, self._by_id())
        assert not errors_of(issues)

    def test_pinned_bad_preset(self):
        rec = {"mode": MODE_PINNED, "preset_id": "nope", "lands": None, "overrides": []}
        issues = validate_deck_composition(rec, self._by_id())
        assert errors_of(issues)

    def test_too_many_overrides(self):
        rec = {"mode": MODE_PINNED, "preset_id": "fx-default", "lands": None,
               "overrides": [{"ref": "core:ramp", "target": 10}] * (MAX_OVERRIDES + 1)}
        issues = validate_deck_composition(rec, self._by_id())
        assert any(i["code"] == "E8" for i in issues)


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------

class TestLegacyMigration:
    def test_override_to_record_has_no_preset(self):
        """There's no built-in default preset to pin the migrated record to."""
        legacy = {"ramp": "10", "card_advantage": "12", "targeted_disruption": "12",
                  "mass_disruption": "6", "plan": "21"}
        record, dropped = legacy_override_to_record(legacy, "2026-01-01T00:00:00Z")
        assert record["mode"] == MODE_PINNED
        assert record["preset_id"] is None
        assert dropped == 21
        refs = [o["ref"] for o in record["overrides"]]
        assert "core:ramp" in refs

    def test_override_with_lands(self):
        legacy = {"lands": "36", "ramp": "12"}
        record, dropped = legacy_override_to_record(legacy, "2026-01-01T00:00:00Z")
        assert record["lands"] == 36
        assert dropped is None

    def test_empty_legacy(self):
        record, dropped = legacy_override_to_record({}, "2026-01-01T00:00:00Z")
        assert record["mode"] == MODE_PINNED
        assert record["overrides"] == []

    def test_migrated_record_resolves_without_crashing(self):
        """Reading back a migrated legacy record must not crash or re-seed a default."""
        legacy = {"ramp": "10"}
        record, _dropped = legacy_override_to_record(legacy, "2026-01-01T00:00:00Z")
        result = resolve_preset(record, [])
        assert result["preset_id"] is None


# ---------------------------------------------------------------------------
# Store CRUD (FakeRedis) -- every preset is a plain user record
# ---------------------------------------------------------------------------

class TestStore:
    def _r(self):
        return FakeRedis()

    def test_list_presets_starts_empty(self):
        r = self._r()
        assert list_presets(r) == []

    def test_get_missing_preset(self):
        r = self._r()
        assert get_preset(r, "nonexistent") is None

    def test_create_preset(self):
        r = self._r()
        p = create_preset(r, {"name": "Test", "slots": [_slot("core:ramp", 10)], "structure": {"lands": 38}})
        assert p["id"].startswith("p-")
        assert p["kind"] == KIND_USER
        found = get_preset(r, p["id"])
        assert found["name"] == "Test"

    def test_create_preset_validation_error(self):
        r = self._r()
        with pytest.raises(CompositionValidationError):
            create_preset(r, {"name": "", "slots": [], "structure": {"lands": 38}})

    def test_update_preset(self):
        r = self._r()
        p = create_preset(r, {"name": "Test", "slots": [_slot("core:ramp", 10)], "structure": {"lands": 38}})
        updated = update_preset(r, p["id"], {"name": "Updated", "slots": [_slot("core:ramp", 12)], "structure": {"lands": 38}})
        assert updated["name"] == "Updated"
        assert updated["version"] == 2

    def test_update_missing_raises_value_error(self):
        r = self._r()
        with pytest.raises(ValueError):
            update_preset(r, "nonexistent", {"name": "Changed"})

    def test_delete_preset(self):
        r = self._r()
        p = create_preset(r, {"name": "Test", "slots": [_slot("core:ramp", 10)], "structure": {"lands": 38}})
        assert delete_preset(r, p["id"]) is True
        assert get_preset(r, p["id"]) is None

    def test_delete_missing(self):
        r = self._r()
        assert delete_preset(r, "nonexistent") is False

    def test_deleted_preset_stays_deleted(self):
        """No read path re-seeds a deleted preset."""
        r = self._r()
        p = create_preset(r, {"name": "Test", "slots": [_slot("core:ramp", 10)], "structure": {"lands": 38}})
        delete_preset(r, p["id"])
        assert get_preset(r, p["id"]) is None
        assert list_presets(r) == []

    def test_get_deck_composition_default(self):
        r = self._r()
        rec = get_deck_composition(r, "archidekt:123")
        assert rec["mode"] == MODE_AUTO

    def test_set_deck_composition(self):
        r = self._r()
        p = create_preset(r, {"name": "Test", "slots": [_slot("core:ramp", 10)], "structure": {"lands": 38}})
        rec = set_deck_composition(r, "archidekt:123",
                                    {"mode": MODE_PINNED, "preset_id": p["id"], "lands": None, "overrides": []})
        assert rec["mode"] == MODE_PINNED
        found = get_deck_composition(r, "archidekt:123")
        assert found["preset_id"] == p["id"]


# ---------------------------------------------------------------------------
# Legacy row refs sanity (unrelated to built-ins)
# ---------------------------------------------------------------------------

class TestLegacyRowRefs:
    def test_legacy_row_refs(self):
        for row, ref in LEGACY_ROW_REFS.items():
            if ref is not None:
                assert check_ref(ref) is None
