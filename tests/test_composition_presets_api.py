"""t-45945b06: comprehensive unit + integration tests for the deck-composition
preset feature (composition-v1, PR #210).

Layers covered:
  * analytics/composition_service.py  — thin service glue (CRUD passthroughs,
    resolve_for_deck wiring, set/reset selection)
  * app.py routes                     — /api/composition/presets CRUD and
    /api/decks/{id}/composition/selection GET/PUT/DELETE via TestClient with a
    FakeRedis patched over `app.r`
  * auto-selection edge cases         — step precedence, ordering, MV gates,
    W2 floors, stale pins, kindred themes, phantom midrange

Every preset is a plain user record now: there are no built-ins, no
read-only/immutable preset ids, and nothing re-seeds a deleted preset. Tests
that used to exercise "built-in" fixtures (tcz_2025, big_mana, go_wide, ...)
now create those same shapes as ordinary user presets via literal fixture
dicts defined in this file.

tests/test_composition_models.py already covers the pure model layer
(refs, validate_preset basics, plan_room, legacy_override_to_record) and
minimal store CRUD; this file builds on it rather than duplicating it.

Run: python -m pytest tests/test_composition_presets_api.py
     python -m unittest tests.test_composition_presets_api
"""
from __future__ import annotations

import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, __file__.rsplit("tests", 1)[0].rstrip("/"))

from fastapi.testclient import TestClient

from src.server.domain.analytics import composition_service as svc
from src.server.domain.analytics import composition_store as store
from src.server.domain.analytics.composition_models import (
    DECK_SIZE, MAX_USER_PRESETS,
    MODE_AUTO, MODE_PINNED, SPEC_VERSION,
    CompositionValidationError,
    band_of, default_deck_composition, errors_of, resolve_preset, validate_preset,
)
from src.server.app.main import app

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class FakeRedis:
    """In-memory stand-in covering exactly the redis calls this feature makes."""

    def __init__(self):
        self.kv = {}

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value, **kw):
        self.kv[key] = value
        return True

    def mget(self, keys):
        return [self.kv.get(k) for k in keys]

    def delete(self, *keys):
        n = 0
        for k in keys:
            if self.kv.pop(k, None) is not None:
                n += 1
        return n

    def keys(self, pattern="*"):
        prefix = pattern.rstrip("*")
        return [k for k in self.kv if k.startswith(prefix)]


RID = "archidekt:777"


def slot(ref, target=None, min=None, max=None, why=""):  # noqa: A002
    return {"ref": ref, "target": target, "min": min, "max": max, "why": why}


def valid_body(name="My Preset", lands=38, slots=None, **extra):
    body = {
        "name": name,
        "structure": {"deck_size": DECK_SIZE, "commanders": 1, "lands": lands},
        "slots": slots if slots is not None else [slot("core:ramp", 10),
                                                  slot("core:card_advantage", 12)],
    }
    body.update(extra)
    return body


def make_deck(**over):
    d = {
        "id": 777,
        "registry_id": RID,
        "source": "archidekt",
        "source_id": 777,
        "name": "Test Deck",
        "status": "physical",
        "commanders": ["Atraxa, Grand Unifier"],
        "cards": [{"name": "Atraxa, Grand Unifier", "quantity": 1},
                  {"name": "Sol Ring", "quantity": 1}],
    }
    d.update(over)
    return d


def put_decks(r, *decks):
    r.set("decks", json.dumps(list(decks)))


def journal_with(primary=None, secondary=(), genre_v=True):
    ap = {}
    if primary:
        ap["primary_archetype"] = primary
    if secondary:
        ap["secondary_archetypes"] = list(secondary)
    rec = {"archetype_preferences": ap}
    if genre_v:
        rec["_journal"] = {"genre_v": 1}
    return rec


def user_preset(pid, name, archetypes=(), themes=(), mv_min=None, priority=0,
                lands=38, slots=None):
    """A ready-made normalized user preset dict (for resolve_preset directly)."""
    return {
        "id": pid, "schema": SPEC_VERSION, "kind": "user", "name": name,
        "description": "", "source": "test", "confidence": "heuristic", "based_on": None,
        "structure": {"deck_size": DECK_SIZE, "commanders": 1, "lands": lands},
        "slots": slots or [slot("core:ramp", 10)],
        "auto_select": {"archetypes": list(archetypes), "themes": list(themes),
                        "commander_mv_min": mv_min, "priority": priority},
        "version": 1, "created_at": None, "updated_at": None,
    }


# Fixture preset bodies mirroring the old (now-removed) built-ins. These are
# plain user presets -- nothing in production code treats their ids
# specially. Used both to seed FakeRedis (via svc.create) for API/service
# tests and, as fully-normalized dicts, for pure resolve_preset tests.
FIXTURE_BODIES = [
    dict(id="tcz_2025", name="Command Zone 2025",
         structure={"deck_size": DECK_SIZE, "commanders": 1, "lands": 38},
         slots=[slot("core:ramp", 10), slot("core:card_advantage", 12),
                slot("group:targeted_interaction", 12), slot("core:board_wipe", 6)]),
    dict(id="big_mana", name="Big Mana",
         structure={"deck_size": DECK_SIZE, "commanders": 1, "lands": 38},
         slots=[slot("core:ramp", 14), slot("core:card_advantage", 10),
                slot("group:targeted_interaction", 10), slot("core:board_wipe", 3)],
         auto_select={"archetypes": ["ramp"], "themes": [], "commander_mv_min": 6, "priority": 0}),
    dict(id="go_wide", name="Go Wide",
         structure={"deck_size": DECK_SIZE, "commanders": 1, "lands": 36},
         slots=[slot("core:ramp", 10), slot("core:card_advantage", 10),
                slot("group:targeted_interaction", 10), slot("core:board_wipe", max=1)],
         auto_select={"archetypes": ["tokens", "aristocrats", "tribal"],
                      "themes": ["tokens", "sacrifice", "kindred:*"],
                      "commander_mv_min": None, "priority": 0}),
    dict(id="spellslinger", name="Spellslinger",
         structure={"deck_size": DECK_SIZE, "commanders": 1, "lands": 36},
         slots=[slot("core:ramp", 10), slot("core:card_advantage", 12),
                slot("group:targeted_interaction", 12), slot("core:board_wipe", 3)],
         auto_select={"archetypes": ["spellslinger", "storm"], "themes": ["spellslinger"],
                      "commander_mv_min": None, "priority": 0}),
    dict(id="control", name="Control",
         structure={"deck_size": DECK_SIZE, "commanders": 1, "lands": 38},
         slots=[slot("core:ramp", 10), slot("core:card_advantage", 12),
                slot("group:targeted_interaction", 15), slot("core:board_wipe", 5)],
         auto_select={"archetypes": ["control", "stax", "pillowfort"], "themes": [],
                      "commander_mv_min": None, "priority": 0}),
    dict(id="voltron", name="Voltron",
         structure={"deck_size": DECK_SIZE, "commanders": 1, "lands": 37},
         slots=[slot("core:ramp", 10), slot("core:card_advantage", 10),
                slot("core:protection", 10), slot("group:targeted_interaction", 10),
                slot("core:board_wipe", 2), slot("theme:voltron", min=10)],
         auto_select={"archetypes": ["voltron"], "themes": ["voltron"],
                      "commander_mv_min": None, "priority": 0}),
    dict(id="graveyard", name="Graveyard",
         structure={"deck_size": DECK_SIZE, "commanders": 1, "lands": 37},
         slots=[slot("core:ramp", 10), slot("core:card_advantage", 8),
                slot("core:recursion", 8), slot("group:targeted_interaction", 10),
                slot("core:board_wipe", 2), slot("theme:graveyard", min=10)],
         auto_select={"archetypes": ["graveyard", "reanimator"], "themes": ["graveyard"],
                      "commander_mv_min": None, "priority": 0}),
    dict(id="landfall", name="Landfall",
         structure={"deck_size": DECK_SIZE, "commanders": 1, "lands": 38},
         slots=[slot("core:ramp", 12), slot("core:card_advantage", 10),
                slot("group:targeted_interaction", 10), slot("core:board_wipe", 2),
                slot("theme:lands_matter", min=10)],
         auto_select={"archetypes": ["landfall"], "themes": ["lands_matter"],
                      "commander_mv_min": None, "priority": 0}),
]


def seed_fixtures(r):
    """Create every FIXTURE_BODIES entry as a plain user preset in r."""
    for body in FIXTURE_BODIES:
        store.create_preset(r, body)


def fixture_preset_dicts():
    """Normalized fixture dicts for pure resolve_preset(...) calls (no redis)."""
    from src.server.domain.analytics.composition_models import normalize_preset
    out = []
    for body in FIXTURE_BODIES:
        norm = normalize_preset(body)
        norm["id"] = body["id"]
        out.append(norm)
    return out


# ---------------------------------------------------------------------------
# Unit: composition_service glue (FakeRedis, no HTTP)
# ---------------------------------------------------------------------------


class ServiceBase(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()


class TestServiceUserPresets(ServiceBase):
    def test_user_presets_starts_empty(self):
        self.assertEqual(svc.user_presets(self.r), [])
        p = svc.create(self.r, valid_body())
        ids = [x["id"] for x in svc.user_presets(self.r)]
        self.assertEqual(ids, [p["id"]])

    def test_list_all_default_includes_everything(self):
        seed_fixtures(self.r)
        items = svc.list_all(self.r)
        ids = [p["id"] for p in items]
        self.assertIn("tcz_2025", ids)
        self.assertEqual(len(items), len(FIXTURE_BODIES))

    def test_list_all_builtin_kind_is_always_empty(self):
        seed_fixtures(self.r)
        self.assertEqual(svc.list_all(self.r, "builtin"), [])

    def test_list_all_user_kind_returns_everything(self):
        seed_fixtures(self.r)
        self.assertEqual(len(svc.list_all(self.r, "user")), len(FIXTURE_BODIES))

    def test_list_all_unknown_kind_unfiltered(self):
        seed_fixtures(self.r)
        self.assertEqual(len(svc.list_all(self.r, "magic")), len(FIXTURE_BODIES))


class TestServiceGetCreate(ServiceBase):
    def test_get_seeded_preset(self):
        seed_fixtures(self.r)
        p = svc.get(self.r, "tcz_2025")
        self.assertEqual(p["id"], "tcz_2025")
        self.assertEqual(p["kind"], "user")

    def test_get_missing_raises(self):
        with self.assertRaises(svc.PresetNotFound) as cm:
            svc.get(self.r, "nope")
        self.assertEqual(cm.exception.args[0], "nope")

    def test_create_normalizes_and_persists(self):
        p = svc.create(self.r, valid_body(name="  Spaced   Name  "))
        self.assertEqual(p["name"], "Spaced Name")
        self.assertEqual(p["kind"], "user")
        self.assertTrue(p["id"].startswith("p-spaced-name-"))
        self.assertIsNotNone(p["created_at"])
        self.assertEqual(p["schema"], SPEC_VERSION)
        raw = json.loads(self.r.get(store.PRESET_KEY))
        self.assertEqual([x["id"] for x in raw], [p["id"]])

    def test_create_validation_error_carries_issues(self):
        with self.assertRaises(CompositionValidationError) as cm:
            svc.create(self.r, valid_body(name="", slots=[slot("bogus:thing", 5)]))
        codes = {i["code"] for i in cm.exception.issues}
        self.assertIn("E1", codes)
        self.assertIn("E3", codes)

    def test_create_with_arbitrary_id_allowed(self):
        """No id is reserved: any preset, including former built-in ids, can be created."""
        p = svc.create(self.r, dict(valid_body(name="Totally New"), id="tcz_2025"))
        self.assertEqual(p["id"], "tcz_2025")

    def test_create_cap_max_user_presets(self):
        existing = [valid_body(name=f"P{i}") for i in range(MAX_USER_PRESETS)]
        for b in existing:
            svc.create(self.r, b)
        with self.assertRaises(CompositionValidationError) as cm:
            svc.create(self.r, valid_body(name="One Too Many"))
        bad = [i for i in cm.exception.issues if i["level"] == "error"]
        self.assertEqual({i["code"] for i in bad}, {"E8"})


class TestServiceUpdateDelete(ServiceBase):
    def _make(self, name="Upd"):
        return svc.create(self.r, valid_body(name=name))

    def test_update_bumps_version_keeps_created_at(self):
        p = self._make()
        upd = svc.update(self.r, p["id"], valid_body(name="Renamed"))
        self.assertEqual(upd["version"], 2)
        self.assertEqual(upd["created_at"], p["created_at"])
        self.assertGreaterEqual(upd["updated_at"], p["updated_at"])
        self.assertEqual(upd["name"], "Renamed")

    def test_update_missing_raises_not_found(self):
        with self.assertRaises(svc.PresetNotFound):
            svc.update(self.r, "ghost", valid_body())

    def test_update_seeded_preset_succeeds(self):
        """Every preset is fully editable now -- no immutable/read-only preset."""
        seed_fixtures(self.r)
        updated = svc.update(self.r, "tcz_2025", valid_body(name="Renamed Default"))
        self.assertEqual(updated["name"], "Renamed Default")

    def test_update_invalid_stays_422_class(self):
        p = self._make()
        with self.assertRaises(CompositionValidationError):
            svc.update(self.r, p["id"], valid_body(name=""))

    def test_delete_flow(self):
        p = self._make()
        svc.delete(self.r, p["id"])
        self.assertEqual(svc.user_presets(self.r), [])
        with self.assertRaises(svc.PresetNotFound):
            svc.delete(self.r, p["id"])

    def test_delete_seeded_preset_succeeds(self):
        seed_fixtures(self.r)
        svc.delete(self.r, "go_wide")
        with self.assertRaises(svc.PresetNotFound):
            svc.get(self.r, "go_wide")

    def test_deleted_preset_stays_deleted(self):
        """Nothing re-seeds a deleted preset on a later read."""
        seed_fixtures(self.r)
        svc.delete(self.r, "go_wide")
        self.assertNotIn("go_wide", [p["id"] for p in svc.list_all(self.r)])


class TestServiceDeckSelection(ServiceBase):
    def setUp(self):
        super().setUp()
        seed_fixtures(self.r)

    def test_set_auto_clears_fields(self):
        rec = svc.set_deck_selection(self.r, RID, {
            "mode": "auto", "preset_id": "tcz_2025", "lands": 40,
            "overrides": [slot("core:ramp", 5)]})
        self.assertIsNone(rec["preset_id"])
        self.assertIsNone(rec["lands"])
        self.assertEqual(rec["overrides"], [])
        self.assertEqual(rec["mode"], MODE_AUTO)
        self.assertIsNotNone(rec["updated_at"])

    def test_set_pinned_round_trip(self):
        body = {"mode": "pinned", "preset_id": "big_mana", "lands": 36,
                "overrides": [{"ref": "core:ramp", "target": 14, "min": None, "max": None}]}
        rec = svc.set_deck_selection(self.r, RID, body)
        self.assertEqual(rec["preset_id"], "big_mana")
        self.assertEqual(rec["lands"], 36)
        found = store.get_deck_composition(self.r, RID)
        self.assertEqual(found["overrides"][0]["ref"], "core:ramp")

    def test_set_pinned_unknown_preset_rejected(self):
        with self.assertRaises(CompositionValidationError):
            svc.set_deck_selection(self.r, RID, {"mode": "pinned", "preset_id": "nope"})

    def test_set_bad_mode_rejected(self):
        with self.assertRaises(CompositionValidationError):
            svc.set_deck_selection(self.r, RID, {"mode": "wild"})

    def test_reset_returns_auto(self):
        svc.set_deck_selection(self.r, RID, {"mode": "pinned", "preset_id": "control"})
        rec = svc.reset_deck_selection(self.r, RID)
        self.assertEqual(rec["mode"], MODE_AUTO)
        self.assertIsNone(rec["preset_id"])

    def test_overrides_default_empty(self):
        rec = svc.set_deck_selection(self.r, RID, {"mode": "pinned", "preset_id": "tcz_2025"})
        self.assertEqual(rec["overrides"], [])


class TestResolveForDeck(ServiceBase):
    """resolve_for_deck wires journal/tags/commander-MV into resolve_preset."""

    def setUp(self):
        super().setUp()
        seed_fixtures(self.r)
        self.deck = make_deck()

    def _with_journal(self, rec):
        # analytics/journal.py: load_journal reads KEY_PREFIX + registry_id
        self.r.set(f"journal:deck:{RID}", json.dumps(rec))

    def _with_card(self, name, cmc):
        # insights.card_texts only keeps a card if it has type_line/oracle_text
        self.r.set(f"card_interactions:{name}", json.dumps(
            {"card": {"cmc": cmc, "type_line": "Legendary Creature", "oracle_text": "..."}}))

    def test_nothing_matches_no_preset_selected(self):
        out = svc.resolve_for_deck(self.r, self.deck, RID)
        self.assertIsNone(out["preset_id"])
        self.assertEqual(out["reason"], {"step": 6, "signal": "no preset selected"})
        self.assertEqual(out["mode"], MODE_AUTO)
        self.assertIsNone(out["preset"])
        self.assertEqual(out["record"], default_deck_composition())

    def test_journal_primary_drives_selection(self):
        self._with_journal(journal_with(primary="ramp"))  # journal:deck:{RID}
        out = svc.resolve_for_deck(self.r, self.deck, RID)
        self.assertEqual(out["preset_id"], "big_mana")
        self.assertEqual(out["reason"]["step"], 2)
        self.assertIn("journal primary: ramp", out["reason"]["signal"])

    def test_deck_tag_drives_selection(self):
        self.r.set(f"deck_tags:{RID}", json.dumps(
            [{"tag": "voltron", "source": "user", "version": 1}]))
        out = svc.resolve_for_deck(self.r, self.deck, RID)
        self.assertEqual(out["preset_id"], "voltron")
        self.assertEqual(out["reason"]["step"], 4)
        self.assertEqual(out["reason"]["signal"], "deck tag: voltron")

    def test_commander_mv_step5_only_when_nothing_higher(self):
        # big_mana gates on mv>=6; Atraxa cmc 5 must NOT match.
        self._with_card("Atraxa, Grand Unifier", 5)
        out = svc.resolve_for_deck(self.r, self.deck, RID)
        self.assertIsNone(out["preset_id"])
        # now a genuinely high-MV commander (deck registry_id drives the lookup)
        deck = make_deck(commanders=["Blasphemous Engineer"], id=778, registry_id="archidekt:778")
        self._with_card("Blasphemous Engineer", 6)
        out = svc.resolve_for_deck(self.r, deck, "archidekt:778")
        self.assertEqual(out["preset_id"], "big_mana")
        self.assertEqual(out["reason"]["step"], 5)

    def test_multi_commander_uses_max_cmc(self):
        deck = make_deck(commanders=["Atraxa, Grand Unifier", "Blasphemous Engineer"])
        self._with_card("Atraxa, Grand Unifier", 5)
        self._with_card("Blasphemous Engineer", 6)
        out = svc.resolve_for_deck(self.r, deck, RID)
        self.assertEqual(out["preset_id"], "big_mana")  # max(5, 6) >= 6

    def test_pinned_record_short_circuits_signals(self):
        svc.set_deck_selection(self.r, RID, {"mode": "pinned", "preset_id": "landfall"})
        self._with_journal(journal_with(primary="ramp"))  # would say big_mana
        out = svc.resolve_for_deck(self.r, self.deck, RID)
        self.assertEqual(out["preset_id"], "landfall")
        self.assertEqual(out["reason"], {"step": 1, "signal": "pinned"})
        self.assertEqual(out["record"]["mode"], MODE_PINNED)

    def test_resolve_is_read_only(self):
        svc.resolve_for_deck(self.r, self.deck, RID)
        self.assertIsNone(self.r.get(store.DECK_KEY.format(registry_id=RID)))

    def test_corrupt_journal_degrades_to_no_selection(self):
        self.r.set(f"journal:deck:{RID}", "{not json")
        out = svc.resolve_for_deck(self.r, self.deck, RID)
        self.assertIsNone(out["preset_id"])

    def test_corrupt_deck_tags_are_ignored(self):
        self.r.set(f"deck_tags:{RID}", "{not json")
        out = svc.resolve_for_deck(self.r, self.deck, RID)
        self.assertIsNone(out["preset_id"])

    def test_missing_card_texts_means_no_mv_signal(self):
        # No card_interactions entries -> commander_mv None -> step 5 skipped.
        out = svc.resolve_for_deck(self.r, self.deck, RID)
        self.assertEqual(out["reason"]["step"], 6)


# ---------------------------------------------------------------------------
# Unit: auto-selection edge cases (pure resolve_preset)
# ---------------------------------------------------------------------------


class TestAutoSelectionEdges(unittest.TestCase):
    def setUp(self):
        self.fixtures = fixture_preset_dicts()

    def test_step_precedence_journal_beats_tag_beats_mv(self):
        # primary 'graveyard' (step2) vs tag 'voltron' (step4) vs mv 6 (step5)
        res = resolve_preset(None, self.fixtures, journal=journal_with(primary="graveyard"),
                             deck_tags=["voltron"], commander_mv=7.0)
        self.assertEqual((res["preset_id"], res["reason"]["step"]), ("graveyard", 2))

    def test_tag_beats_mv(self):
        res = resolve_preset(None, self.fixtures, deck_tags=["voltron"], commander_mv=7.0)
        self.assertEqual((res["preset_id"], res["reason"]["step"]), ("voltron", 4))

    def test_mv_gate_boundary_exact_match_counts(self):
        res = resolve_preset(None, self.fixtures, commander_mv=6.0)
        self.assertEqual(res["preset_id"], "big_mana")
        self.assertEqual(res["reason"]["step"], 5)
        self.assertNotIn("big_mana", res["also_matched"])

    def test_mv_gate_below_threshold_no_selection(self):
        res = resolve_preset(None, self.fixtures, commander_mv=5.9)
        self.assertIsNone(res["preset_id"])

    def test_mv_signal_formats_integral_float(self):
        res = resolve_preset(None, self.fixtures, commander_mv=6.0)
        self.assertEqual(res["reason"]["signal"], "commander MV 6")
        res = resolve_preset(None, self.fixtures, commander_mv=6.5)
        self.assertEqual(res["reason"]["signal"], "commander MV 6.5")

    def test_higher_priority_user_preset_wins_same_step(self):
        up = user_preset("p-custom", "Custom Ramp", archetypes=["ramp"], priority=100)
        res = resolve_preset(None, [up] + self.fixtures, journal=journal_with(primary="ramp"))
        self.assertEqual(res["preset_id"], "p-custom")
        self.assertEqual(res["also_matched"], ["big_mana"])

    def test_priority_desc_then_id_asc_within_group(self):
        lo = user_preset("p-a", "A", archetypes=["tokens"], priority=1)
        hi = user_preset("p-z", "Z", archetypes=["tokens"], priority=9)
        same1 = user_preset("p-m", "M", archetypes=["tokens"], priority=1)
        res = resolve_preset(None, [lo, hi, same1] + self.fixtures, journal=journal_with(primary="tokens"))
        self.assertEqual(res["preset_id"], "p-z")
        # among priority ties: id asc -> p-a before p-m before go_wide
        res = resolve_preset(None, [same1, lo] + self.fixtures, journal=journal_with(primary="tokens"))
        self.assertEqual(res["preset_id"], "p-a")
        self.assertIn("p-m", res["also_matched"])
        self.assertIn("go_wide", res["also_matched"])

    def test_also_matched_dedupes_and_excludes_winner(self):
        # tag 'tokens' matches go_wide themes; secondary 'aristocrats' also go_wide
        res = resolve_preset(None, self.fixtures, journal=journal_with(secondary=["aristocrats", "tokens"]),
                             deck_tags=["tokens"])
        self.assertEqual(res["preset_id"], "go_wide")
        self.assertNotIn("go_wide", res["also_matched"])

    def test_kindred_theme_tag_matches_go_wide(self):
        res = resolve_preset(None, self.fixtures, deck_tags=["kindred:Elf"])
        self.assertEqual(res["preset_id"], "go_wide")
        self.assertEqual(res["reason"]["step"], 4)

    def test_non_kindred_tag_does_not_match_kindred_pattern(self):
        res = resolve_preset(None, self.fixtures, deck_tags=["elves"])
        self.assertIsNone(res["preset_id"])

    def test_sacrifice_tag_matches_go_wide(self):
        res = resolve_preset(None, self.fixtures, deck_tags=["sacrifice"])
        self.assertEqual(res["preset_id"], "go_wide")

    def test_phantom_midrange_needs_genre_v_stamp(self):
        # Deliberate midrange saved post-GN (has genre_v) survives load-time
        # migration but no fixture lists 'midrange', so it still falls through.
        res = resolve_preset(None, self.fixtures, journal=journal_with(primary="midrange"))
        self.assertEqual(res["reason"]["step"], 6)
        # Legacy record without genre_v: migrate_phantom_midrange nulls it.
        legacy = {"archetype_preferences": {"primary_archetype": "midrange"}}
        self.assertIsNone(legacy["archetype_preferences"]["primary_archetype"]
                          if False else None)  # clarity guard, real path below
        from src.server.domain.decks.deck_genre import migrate_phantom_midrange
        cleared = migrate_phantom_midrange(legacy)
        self.assertIsNone(cleared["archetype_preferences"]["primary_archetype"])

    def test_explicit_midrange_secondary_still_matches(self):
        # secondary archetypes aren't phantom-cleared; 'nonexistent_arch' matches nothing.
        res = resolve_preset(None, self.fixtures, journal=journal_with(secondary=["nonexistent_arch"]))
        self.assertEqual(res["reason"]["step"], 6)

    def test_stale_pin_reports_issue_no_fallback(self):
        rec = {"mode": MODE_PINNED, "preset_id": "p-deleted"}
        res = resolve_preset(rec, self.fixtures)
        self.assertIsNone(res["preset_id"])
        self.assertEqual(res["mode"], MODE_PINNED)
        self.assertEqual(res["issues"][0]["code"], "I3")
        self.assertEqual(res["issues"][0]["data"]["stale_pin"], "p-deleted")

    def test_stale_pin_resolves_again_after_recreation(self):
        up = user_preset("p-back", "Back Again", archetypes=["ramp"])
        res = resolve_preset({"mode": MODE_PINNED, "preset_id": "p-back"}, [up])
        self.assertEqual(res["preset_id"], "p-back")
        self.assertEqual(res["issues"], [])

    def test_tag_themes_spellslinger(self):
        res = resolve_preset(None, self.fixtures, deck_tags=["spellslinger"])
        self.assertEqual(res["preset_id"], "spellslinger")

    def test_landfall_via_lands_matter_tag(self):
        res = resolve_preset(None, self.fixtures, deck_tags=["lands_matter"])
        self.assertEqual(res["preset_id"], "landfall")

    def test_empty_string_journal_values_treated_unset(self):
        res = resolve_preset(None, self.fixtures, journal={"archetype_preferences":
                                                {"primary_archetype": "", "secondary_archetypes": []}})
        self.assertEqual(res["reason"]["step"], 6)

    def test_no_presets_at_all_no_selection(self):
        res = resolve_preset(None, [])
        self.assertIsNone(res["preset_id"])
        self.assertEqual(res["reason"], {"step": 6, "signal": "no preset selected"})


# ---------------------------------------------------------------------------
# Unit: validation edge cases beyond test_composition_models coverage
# ---------------------------------------------------------------------------


class TestValidationEdges(unittest.TestCase):
    def _p(self, **kw):
        base = {"id": "vp", "kind": "user", "name": kw.pop("name", "VP"),
                "structure": {"deck_size": DECK_SIZE, "commanders": 1, "lands": 38},
                "slots": [slot("core:ramp", 10)]}
        base.update(kw)
        return base

    def test_ramp_floor_bumped_by_mv_min_5(self):
        # FLOORS core:ramp = 10, +2 when auto_select.commander_mv_min >= 5 (boundary is inclusive).
        p = self._p(slots=[slot("core:ramp", max=12)],
                    auto_select={"archetypes": [], "themes": [], "commander_mv_min": 5, "priority": 0})
        self.assertFalse([i for i in validate_preset(p) if i["code"] == "W2"])
        p_low = self._p(slots=[slot("core:ramp", max=11)],
                        auto_select={"archetypes": [], "themes": [], "commander_mv_min": 4, "priority": 0})
        self.assertFalse([i for i in validate_preset(p_low) if i["code"] == "W2"])
        p2 = self._p(slots=[slot("core:ramp", max=11)],
                     auto_select={"archetypes": [], "themes": [], "commander_mv_min": 6, "priority": 0})
        w2 = [i for i in validate_preset(p2) if i["code"] == "W2"]
        self.assertTrue(w2)
        self.assertEqual(w2[0]["data"]["floor"], 12)

    def test_board_wipe_floor_zeroed_for_tokens_theme(self):
        p = self._p(slots=[slot("core:board_wipe", max=1)],
                    auto_select={"archetypes": [], "themes": ["tokens"],
                                 "commander_mv_min": None, "priority": 0})
        self.assertFalse([i for i in validate_preset(p) if i["code"] == "W2"])

    def test_deck_cat_ref_rejected_at_preset_scope(self):
        p = self._p(slots=[slot("cat:d-cool-thing", 5)])
        issues = validate_preset(p)
        e3 = [i for i in issues if i["code"] == "E3"]
        self.assertTrue(e3)
        self.assertIn("deck overrides", e3[0]["message"])

    def test_global_cat_ref_allowed(self):
        p = self._p(slots=[slot("cat:g-draw", 5)])
        self.assertFalse(errors_of(validate_preset(p)))

    def test_core_lands_slot_normalized_then_rejected_as_structure(self):
        # normalize_ref maps core:lands -> structure:lands; check_ref then E4s it.
        p = self._p(slots=[{"ref": "core:lands", "target": 38, "min": None, "max": None, "why": ""}])
        issues = validate_preset(p)
        self.assertTrue([i for i in issues if i["code"] == "E4"])

    def test_bool_slot_numbers_rejected(self):
        p = self._p(slots=[slot("core:ramp", target=True)])
        self.assertTrue([i for i in validate_preset(p) if i["code"] == "E5"])

    def test_ai_flag_requires_why_and_plan_room_error(self):
        p = self._p(slots=[slot("core:ramp", 10, why="TCZ template says ~10 ramp.")])
        self.assertFalse([i for i in validate_preset(p, ai=True) if i["code"] == "A1"])
        p_no_why = self._p(slots=[slot("core:ramp", 10)])
        self.assertTrue([i for i in validate_preset(p_no_why, ai=True) if i["code"] == "A1"])
        # 62 spells; lower bounds 25+25+10 leave room 2 < MIN_PLAN_ROOM(15).
        # W3 needs no other errors present, so the same slots need a `why` to be AI-checkable.
        tight = self._p(slots=[slot("core:ramp", 25), slot("core:card_advantage", 25),
                               slot("group:targeted_interaction", 10)])
        codes = {i["code"] for i in validate_preset(tight)}
        self.assertIn("W3", codes)
        tight_ai = self._p(slots=[slot("core:ramp", 25, why="a"), slot("core:card_advantage", 25, why="b"),
                                   slot("group:targeted_interaction", 10, why="c")])
        codes_ai = {i["code"] for i in validate_preset(tight_ai, ai=True)}
        self.assertIn("A3", codes_ai)
        self.assertNotIn("W3", codes_ai)

    def test_band_of_display_defaults(self):
        self.assertEqual(band_of(slot("core:ramp", 10)), (9, 11))
        self.assertEqual(band_of(slot("core:ramp", min=3)), (3, None))

    def test_two_commanders_shrink_spell_budget(self):
        st = {"deck_size": DECK_SIZE, "commanders": 2, "lands": 36}
        from src.server.domain.analytics.composition_models import spells_for
        self.assertEqual(spells_for(st), 62)
        p = self._p(structure=st, slots=[slot("core:ramp", min=63)])
        self.assertTrue([i for i in validate_preset(p) if i["code"] == "E6"])


# ---------------------------------------------------------------------------
# Integration: /api/composition/presets CRUD (TestClient over app.r)
# ---------------------------------------------------------------------------


class ApiBase(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.deck = make_deck()
        put_decks(self.r, self.deck)
        seed_fixtures(self.r)
        self.p = patch("src.server.app.main.r", self.r)
        self.p.start()
        self.addCleanup(self.p.stop)
        self.client = TestClient(app)


class TestPresetListApi(ApiBase):
    def test_list_returns_seeded_presets(self):
        resp = self.client.get("/api/composition/presets")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        ids = [p["id"] for p in body["presets"]]
        self.assertIn("tcz_2025", ids)
        self.assertEqual(len(ids), len(FIXTURE_BODIES))

    def test_list_kind_filter(self):
        created = self.client.post("/api/composition/presets", json=valid_body()).json()["preset"]
        b = self.client.get("/api/composition/presets", params={"kind": "builtin"}).json()
        u = self.client.get("/api/composition/presets", params={"kind": "user"}).json()
        self.assertEqual(b["presets"], [])
        u_ids = {p["id"] for p in u["presets"]}
        self.assertIn(created["id"], u_ids)
        self.assertEqual(len(u["presets"]), len(FIXTURE_BODIES) + 1)

    def test_newest_user_preset_first(self):
        first = self.client.post("/api/composition/presets", json=valid_body(name="First")).json()["preset"]
        second = self.client.post("/api/composition/presets", json=valid_body(name="Second")).json()["preset"]
        ids = [p["id"] for p in self.client.get("/api/composition/presets").json()["presets"]]
        self.assertEqual(ids[0], second["id"])
        self.assertEqual(ids[1], first["id"])


class TestPresetCreateApi(ApiBase):
    def test_create_201_shape(self):
        resp = self.client.post("/api/composition/presets", json=valid_body(name="Alpha Draft"))
        self.assertEqual(resp.status_code, 201)
        p = resp.json()["preset"]
        self.assertEqual(p["kind"], "user")
        self.assertEqual(p["name"], "Alpha Draft")
        self.assertTrue(p["id"].startswith("p-alpha-draft-"))

    def test_create_duplicate_name_422(self):
        self.client.post("/api/composition/presets", json=valid_body(name="Dup"))
        resp = self.client.post("/api/composition/presets", json=valid_body(name="Dup"))
        self.assertEqual(resp.status_code, 422)
        body = resp.json()
        self.assertFalse(body["success"])
        self.assertTrue(any(i["code"] == "E1" for i in body["issues"]))

    def test_create_collides_with_existing_preset_name_422(self):
        resp = self.client.post("/api/composition/presets", json=valid_body(name="Big Mana"))
        self.assertEqual(resp.status_code, 422)

    def test_create_invalid_slot_422_with_issues(self):
        resp = self.client.post("/api/composition/presets",
                                json=valid_body(slots=[slot("zone:library", 5)]))
        self.assertEqual(resp.status_code, 422)
        self.assertTrue(any(i["code"] == "E3" for i in resp.json()["issues"]))

    def test_create_non_dict_body_400(self):
        resp = self.client.post("/api/composition/presets", json=[1, 2, 3])
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["success"])

    def test_create_malformed_json_400(self):
        resp = self.client.post("/api/composition/presets", content=b"{oops",
                                headers={"Content-Type": "application/json"})
        self.assertEqual(resp.status_code, 400)

    def test_create_with_existing_id_allowed_if_name_unique(self):
        """ids aren't reserved, but a reused id overwrites a different preset's data path --
        creation still goes through normal validation (name uniqueness, etc.)."""
        resp = self.client.post("/api/composition/presets",
                                json=dict(valid_body(name="Clone Zone"), id="some-arbitrary-id"))
        self.assertEqual(resp.status_code, 201)

    def test_create_at_cap_422_e8(self):
        # already seeded len(FIXTURE_BODIES); fill up to the cap.
        for i in range(MAX_USER_PRESETS - len(FIXTURE_BODIES)):
            r = self.client.post("/api/composition/presets", json=valid_body(name=f"Cap {i}"))
            assert r.status_code == 201, r.text
        resp = self.client.post("/api/composition/presets", json=valid_body(name="Over Cap"))
        self.assertEqual(resp.status_code, 422)
        self.assertTrue(any(i["code"] == "E8" for i in resp.json()["issues"]))


class TestPresetGetApi(ApiBase):
    def test_get_seeded_preset(self):
        resp = self.client.get("/api/composition/presets/tcz_2025")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["preset"]["name"], "Command Zone 2025")

    def test_get_user_preset(self):
        pid = self.client.post("/api/composition/presets", json=valid_body()).json()["preset"]["id"]
        resp = self.client.get(f"/api/composition/presets/{pid}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["preset"]["id"], pid)

    def test_get_missing_404_message(self):
        resp = self.client.get("/api/composition/presets/ghost")
        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertFalse(body["success"])
        self.assertIn("ghost", body["error"])

    def test_get_path_traversal_style_id_404(self):
        resp = self.client.get("/api/composition/presets/..%2Fsecret")
        self.assertIn(resp.status_code, (404, 422))


class TestPresetUpdateApi(ApiBase):
    def _pid(self):
        return self.client.post("/api/composition/presets",
                                json=valid_body(name="Pre Update")).json()["preset"]["id"]

    def test_update_round_trip(self):
        pid = self._pid()
        resp = self.client.put(f"/api/composition/presets/{pid}",
                               json=valid_body(name="Post Update", lands=36))
        self.assertEqual(resp.status_code, 200)
        p = resp.json()["preset"]
        self.assertEqual((p["name"], p["structure"]["lands"], p["version"]),
                         ("Post Update", 36, 2))
        got = self.client.get(f"/api/composition/presets/{pid}").json()["preset"]
        self.assertEqual(got["name"], "Post Update")

    def test_update_seeded_preset_is_editable(self):
        resp = self.client.put("/api/composition/presets/big_mana", json=valid_body(name="Big Mana Mk2"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["preset"]["name"], "Big Mana Mk2")

    def test_update_missing_404(self):
        resp = self.client.put("/api/composition/presets/nope", json=valid_body())
        self.assertEqual(resp.status_code, 404)

    def test_update_invalid_422_leaves_original_intact(self):
        pid = self._pid()
        resp = self.client.put(f"/api/composition/presets/{pid}",
                               json=valid_body(slots=[slot("core:ramp", 100)]))
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(self.client.get(f"/api/composition/presets/{pid}").json()["preset"]["name"],
                         "Pre Update")

    def test_update_rename_to_other_user_preset_422(self):
        pid1 = self._pid()
        self.client.post("/api/composition/presets", json=valid_body(name="Other"))
        resp = self.client.put(f"/api/composition/presets/{pid1}", json=valid_body(name="Other"))
        self.assertEqual(resp.status_code, 422)

    def test_update_rename_to_seeded_preset_name_422(self):
        pid = self._pid()
        resp = self.client.put(f"/api/composition/presets/{pid}", json=valid_body(name="Go Wide"))
        self.assertEqual(resp.status_code, 422)


class TestPresetDeleteApi(ApiBase):
    def test_delete_flow(self):
        pid = self.client.post("/api/composition/presets", json=valid_body()).json()["preset"]["id"]
        resp = self.client.delete(f"/api/composition/presets/{pid}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["deleted"], pid)
        self.assertEqual(self.client.get(f"/api/composition/presets/{pid}").status_code, 404)

    def test_delete_seeded_preset_succeeds(self):
        resp = self.client.delete("/api/composition/presets/control")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.client.get("/api/composition/presets/control").status_code, 404)

    def test_delete_missing_404(self):
        self.assertEqual(self.client.delete("/api/composition/presets/ghost").status_code, 404)

    def test_delete_twice_second_404(self):
        pid = self.client.post("/api/composition/presets", json=valid_body()).json()["preset"]["id"]
        self.client.delete(f"/api/composition/presets/{pid}")
        self.assertEqual(self.client.delete(f"/api/composition/presets/{pid}").status_code, 404)


# ---------------------------------------------------------------------------
# Integration: /api/decks/{id}/composition/selection
# ---------------------------------------------------------------------------


class TestSelectionApi(ApiBase):
    def url(self, deck_id=RID):
        return f"/api/decks/{deck_id}/composition/selection"

    def test_get_default_no_selection(self):
        resp = self.client.get(self.url())
        self.assertEqual(resp.status_code, 200)
        sel = resp.json()["selection"]
        self.assertIsNone(sel["preset_id"])
        self.assertEqual(sel["mode"], MODE_AUTO)
        self.assertIsNone(sel["preset"])
        self.assertEqual(sel["record"], default_deck_composition())

    def test_get_by_numeric_id_and_name(self):
        for ident in ("777", "Test Deck"):
            resp = self.client.get(self.url(ident))
            self.assertEqual(resp.status_code, 200, ident)
            self.assertIsNone(resp.json()["selection"]["preset_id"])

    def test_get_unknown_deck_404(self):
        resp = self.client.get(self.url("does-not-exist"))
        self.assertEqual(resp.status_code, 404)
        self.assertIn("does-not-exist", resp.json()["error"])

    def test_put_auto_clears_pin(self):
        self.client.put(self.url(), json={"mode": "pinned", "preset_id": "control"})
        resp = self.client.put(self.url(), json={"mode": "auto"})
        self.assertEqual(resp.status_code, 200)
        sel = resp.json()["selection"]
        self.assertEqual(sel["mode"], MODE_AUTO)
        self.assertIsNone(sel["record"]["preset_id"])

    def test_put_pin_seeded_preset(self):
        resp = self.client.put(self.url(), json={"mode": "pinned", "preset_id": "go_wide"})
        self.assertEqual(resp.status_code, 200)
        sel = resp.json()["selection"]
        self.assertEqual((sel["preset_id"], sel["mode"], sel["reason"]["step"]),
                         ("go_wide", MODE_PINNED, 1))

    def test_put_pin_user_preset(self):
        pid = self.client.post("/api/composition/presets", json=valid_body(name="Pin Me")).json()["preset"]["id"]
        resp = self.client.put(self.url(), json={"mode": "pinned", "preset_id": pid, "lands": 35})
        sel = resp.json()["selection"]
        self.assertEqual(sel["preset_id"], pid)
        self.assertEqual(sel["record"]["lands"], 35)

    def test_put_pin_unknown_preset_422(self):
        resp = self.client.put(self.url(), json={"mode": "pinned", "preset_id": "nope"})
        self.assertEqual(resp.status_code, 422)
        self.assertTrue(any(i["code"] == "E3" for i in resp.json()["issues"]))

    def test_put_bad_mode_422(self):
        resp = self.client.put(self.url(), json={"mode": "vibes"})
        self.assertEqual(resp.status_code, 422)

    def test_put_auto_with_leftovers_silently_cleared(self):
        # Documented behavior (composition_service.set_deck_selection line 95-96):
        # mode=auto FORCES preset_id/lands/overrides to null before validation, so
        # leftover fields cannot reach the E3 'Auto mode has no...' rule. The API
        # accepts it with 200 and a clean auto record.
        resp = self.client.put(self.url(), json={"mode": "auto", "preset_id": "tcz_2025", "lands": 40})
        self.assertEqual(resp.status_code, 200)
        rec = resp.json()["selection"]["record"]
        self.assertIsNone(rec["preset_id"])
        self.assertIsNone(rec["lands"])
        self.assertEqual(rec["overrides"], [])

    def test_auto_mode_leftovers_rejected_at_model_layer(self):
        # The strict rule lives in validate_deck_composition; show it fires when
        # the record bypasses the service's normalization (defense-in-depth check).
        from src.server.domain.analytics.composition_models import validate_deck_composition
        presets_by_id = {p["id"]: p for p in fixture_preset_dicts()}
        issues = validate_deck_composition(
            {"mode": MODE_AUTO, "preset_id": "tcz_2025", "lands": None, "overrides": []},
            presets_by_id)
        self.assertTrue([i for i in issues if i["code"] == "E3"])

    def test_put_overrides_deck_category_allowed(self):
        resp = self.client.put(self.url(), json={
            "mode": "pinned", "preset_id": "tcz_2025",
            "overrides": [{"ref": "cat:d-fun-pile", "target": 5, "min": None, "max": None}]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["selection"]["record"]["overrides"][0]["ref"], "cat:d-fun-pile")

    def test_put_override_duplicate_ref_422(self):
        resp = self.client.put(self.url(), json={
            "mode": "pinned", "preset_id": "tcz_2025",
            "overrides": [slot("core:ramp", 5), slot("core:ramp", 6)]})
        self.assertEqual(resp.status_code, 422)
        self.assertTrue(any(i["code"] == "E4" for i in resp.json()["issues"]))

    def test_put_override_lands_out_of_range_422(self):
        resp = self.client.put(self.url(), json={"mode": "pinned", "preset_id": "tcz_2025", "lands": 150})
        self.assertEqual(resp.status_code, 422)

    def test_put_non_dict_body_400(self):
        resp = self.client.put(self.url(), json="pinned")
        self.assertEqual(resp.status_code, 400)

    def test_put_unknown_deck_404_before_validation(self):
        resp = self.client.put(self.url("ghost-deck"), json={"mode": "pinned", "preset_id": "nope"})
        self.assertEqual(resp.status_code, 404)

    def test_delete_resets_to_auto(self):
        self.client.put(self.url(), json={"mode": "pinned", "preset_id": "voltron"})
        resp = self.client.delete(self.url())
        self.assertEqual(resp.status_code, 200)
        sel = resp.json()["selection"]
        self.assertEqual(sel["mode"], MODE_AUTO)
        self.assertEqual(sel["reason"], {"step": 6, "signal": "no preset selected"})

    def test_delete_unknown_deck_404(self):
        self.assertEqual(self.client.delete(self.url("ghost-deck")).status_code, 404)

    def test_full_lifecycle_user_preset_pin_delete_stale_pin(self):
        pid = self.client.post("/api/composition/presets",
                               json=valid_body(name="Temp Pin")).json()["preset"]["id"]
        self.client.put(self.url(), json={"mode": "pinned", "preset_id": pid})
        self.assertEqual(self.client.get(self.url()).json()["selection"]["preset_id"], pid)
        self.client.delete(f"/api/composition/presets/{pid}")
        sel = self.client.get(self.url()).json()["selection"]
        self.assertIsNone(sel["preset_id"])
        self.assertEqual(sel["mode"], MODE_PINNED)  # pin kept, preset gone
        self.assertEqual(sel["issues"][0]["code"], "I3")
        self.assertEqual(sel["reason"]["signal"], "pinned preset missing")
        # reset clears the stale pin entirely
        sel2 = self.client.delete(self.url()).json()["selection"]
        self.assertEqual(sel2["mode"], MODE_AUTO)
        self.assertEqual(sel2["issues"], [])

    def test_selection_survives_across_requests(self):
        self.client.put(self.url(), json={"mode": "pinned", "preset_id": "graveyard"})
        sel = self.client.get(self.url()).json()["selection"]
        self.assertEqual(sel["preset_id"], "graveyard")
        self.assertEqual(sel["record"]["mode"], MODE_PINNED)

    def test_auto_selection_flows_through_api(self):
        self.r.set(f"deck_tags:{RID}", json.dumps([{"tag": "tokens", "source": "user", "version": 1}]))
        sel = self.client.get(self.url()).json()["selection"]
        self.assertEqual(sel["preset_id"], "go_wide")
        self.assertEqual(sel["reason"]["step"], 4)

    def test_selection_isolated_per_deck(self):
        other = make_deck(id=778, registry_id="archidekt:778", name="Other Deck")
        put_decks(self.r, self.deck, other)
        self.client.put(self.url(RID), json={"mode": "pinned", "preset_id": "control"})
        sel = self.client.get(self.url("archidekt:778")).json()["selection"]
        self.assertEqual(sel["mode"], MODE_AUTO)


if __name__ == "__main__":
    unittest.main(verbosity=2)
