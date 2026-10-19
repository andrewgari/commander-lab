"""GN: the Journal's Deck Genre section (archetype / themes / kindred).

Spec: docs/DECK_GENRE_TAXONOMY.md (data rules), docs/DECK_GENRE_UX_SPEC.md (UI).

- deck_genre.py constants are docs/deck_genre_taxonomy.yaml as-is (drift fails here);
- normalization: kindred aliases / plurals -> catalog spelling, theme labels -> seed ids, dedupe,
  primary never a secondary; validation: >max and >32 chars are 400s;
- the journal round-trips null primary, primary-only, 1+3, custom values, and clearing;
- the phantom "midrange" saved by every pre-GN journal reads as unset (and stays a
  deliberate choice once a record carries genre_v);
- the analytics engine skips archetype ids it has no card pattern for;
- the header strip (view mode) and the journal section render; the JS helpers run in node
  and produce the same strip markup as the Jinja macro.

No network: Redis is tests.test_interactions.FakeRedis.
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

import app as app_mod
import deck_genre as G
from analytics import journal
from tests.test_deck_stats_ui import DECK, INVENTORY
from tests.test_interactions import FakeRedis
from user_intent import ALLOWED_ARCHETYPES, DeckGenre, parse_user_intent, resolve_intent_defaults, validate_intent

ROOT = Path(__file__).resolve().parents[1]
YAML = ROOT / "docs" / "deck_genre_taxonomy.yaml"
TAXONOMY_JS = ROOT / "docs" / "design" / "genre" / "taxonomy.js"
TYPES_JS = ROOT / "docs" / "design" / "genre" / "creature_types.js"
GENRE_JS = ROOT / "static" / "js" / "deck_genre.js"
ORIGINAL_14 = {"aggro", "midrange", "control", "combo", "stax", "spellslinger", "aristocrats", "tokens", "graveyard",
               "voltron", "ramp", "tribal", "group_hug", "group_slug"}


def _taxonomy():
    """The yaml when PyYAML is installed (it isn't in requirements.txt), else the mockup's JSON copy of it."""
    try:
        import yaml
        return yaml.safe_load(YAML.read_text())
    except ImportError:
        return json.loads(re.search(r"=\s*(\{.*\})\s*;?\s*$", TAXONOMY_JS.read_text(), re.S).group(1))


def _legacy_record(**ap):
    """A journal saved before GN: the resolver's default primary, no genre_v stamp."""
    arch = {"primary_archetype": "midrange", "secondary_archetypes": [], "excluded_archetypes": [], "custom_themes": []}
    arch.update(ap)
    return {"deck_vision": {"intent": "tune_up", "description": "Old.", "target_turn_win": None},
            "archetype_preferences": arch, "_journal": {"created_at": "2026-01-01T00:00:00+00:00"}}


class TestTaxonomyConstants(unittest.TestCase):
    def test_counts_and_unique_ids(self):
        self.assertEqual(len(G.ARCHETYPE_IDS), 30)
        self.assertEqual(len(G.GENRE_IDS), 33)
        self.assertEqual(len(G.KINDRED_SUGGESTIONS), 35)
        self.assertEqual(len(set(G.ARCHETYPE_IDS)), 30)
        self.assertEqual(len(set(G.GENRE_IDS)), 33)
        self.assertEqual(len(G.CREATURE_TYPES_SNAPSHOT), 351)

    def test_constants_match_the_yaml(self):
        t = _taxonomy()
        strip = lambda rows, keys: [{k: r[k] for k in keys} for r in rows]  # noqa: E731
        self.assertEqual(strip(t["archetypes"], ("id", "group", "label", "desc")), list(G.ARCHETYPES))
        self.assertEqual(strip(t["genres"], ("id", "group", "label", "desc")), list(G.GENRES))
        self.assertEqual(t["archetype_groups"], list(G.ARCHETYPE_GROUPS))
        self.assertEqual(t["genre_groups"], list(G.GENRE_GROUPS))
        self.assertEqual(t["kindred"]["suggestions"], list(G.KINDRED_SUGGESTIONS))
        self.assertEqual(t["kindred"]["groups"], list(G.KINDRED_GROUPS))
        self.assertEqual(t["kindred"]["aliases"], G.KINDRED_ALIASES)
        self.assertEqual(t["limits"]["secondary_archetypes"]["max"], G.MAX_SECONDARY)
        self.assertEqual(t["limits"]["genres"]["max"], G.MAX_GENRES)
        self.assertEqual(t["limits"]["kindred"]["max"], G.MAX_KINDRED)
        self.assertEqual(t["limits"]["genres"]["custom_max_len"], G.CUSTOM_MAX_LEN)

    def test_original_ids_kept_and_allowed(self):
        self.assertTrue(ORIGINAL_14 <= set(G.ARCHETYPE_IDS))
        self.assertEqual(ALLOWED_ARCHETYPES, set(G.ARCHETYPE_IDS))
        self.assertEqual(G.ARCHETYPE_LABELS["tribal"], "Kindred (type matters)")

    def test_kindred_seed_values_are_catalog_types(self):
        cat = set(G.CREATURE_TYPES_SNAPSHOT)
        self.assertTrue(set(G.KINDRED_SUGGESTIONS) <= cat)
        for g in G.KINDRED_GROUPS:
            self.assertTrue(set(g["members"]) <= cat, g)
        self.assertTrue(set(G.KINDRED_ALIASES.values()) <= cat)

    def test_snapshot_matches_the_design_fixture(self):
        js = json.loads(re.search(r"=\s*(\[.*\])", TYPES_JS.read_text(), re.S).group(1))
        self.assertEqual(js, list(G.CREATURE_TYPES_SNAPSHOT))


class TestNormalization(unittest.TestCase):
    def test_kindred_aliases_plurals_and_case(self):
        for raw, want in [("Elves", "Elf"), ("elves", "Elf"), ("mice", "Mouse"), ("foxes", "Fox"), ("Dinos", "Dinosaur"),
                          ("  zombie ", "Zombie"), ("Goblins", "Goblin"), ("assembly-worker", "Assembly-Worker")]:
            with self.subTest(raw=raw):
                self.assertEqual(G.canon_kindred(raw), want)
        self.assertEqual(G.canon_kindred("Changeling  (any   type)"), "Changeling (any type)")  # custom, collapsed
        self.assertTrue(G.is_custom_kindred("Changeling (any type)"))
        self.assertFalse(G.is_custom_kindred("elf"))

    def test_kindred_uses_the_given_catalog(self):
        self.assertEqual(G.canon_kindred("wugs", types=["Wug"]), "Wug")
        self.assertEqual(G.canon_kindred("Elves", types=["Wug"]), "Elf")  # aliases still apply

    def test_theme_labels_and_ids_canonicalize(self):
        for raw, want in [("Horror / Gothic", "horror"), ("horror", "horror"), ("PET DECK", "pet_deck"),
                          ("precon_upgrade", "precon_upgrade"), ("Space   western ", "Space western")]:
            with self.subTest(raw=raw):
                self.assertEqual(G.canon_genre(raw), want)
        self.assertTrue(G.is_custom_genre("Space western"))
        self.assertFalse(G.is_custom_genre("horror"))

    def test_normalize_deck_genre_dedupes_after_canonicalizing(self):
        out = G.normalize_deck_genre({"genres": ["Horror", "horror / gothic", " ", "Noir"], "kindred": ["Elves", "elf", "Elf"]})
        self.assertEqual(out, {"genres": ["horror", "Noir"], "kindred": ["Elf"]})
        self.assertEqual(G.normalize_deck_genre("junk"), "junk")  # left for validation to explain

    def test_normalize_archetypes(self):
        n = G.normalize_archetypes
        self.assertEqual(n({"primary_archetype": "tokens", "secondary_archetypes": ["tokens", "aristocrats", "aristocrats"]}),
                         {"primary_archetype": "tokens", "secondary_archetypes": ["aristocrats"]})
        self.assertEqual(n({"primary_archetype": "", "secondary_archetypes": []}),
                         {"primary_archetype": None, "secondary_archetypes": []})
        # No primary but secondaries: the first one is promoted (the UI's "remove primary" rule).
        self.assertEqual(n({"primary_archetype": None, "secondary_archetypes": ["blink", "ramp"]}),
                         {"primary_archetype": "blink", "secondary_archetypes": ["ramp"]})

    def test_validate_deck_genre(self):
        v = G.validate_deck_genre
        self.assertEqual(v(None), [])
        self.assertEqual(v({"genres": ["horror"], "kindred": []}), [])
        self.assertTrue(any("at most 5" in e for e in v({"genres": list("abcdef")})))
        self.assertTrue(any("at most 5" in e for e in v({"kindred": list("abcdef")})))
        self.assertTrue(any("longer than 32" in e for e in v({"genres": ["x" * 33]})))
        self.assertEqual(v({"genres": ["x" * 32]}), [])
        self.assertTrue(any("list of strings" in e for e in v({"kindred": "Elf"})))
        self.assertTrue(any("duplicates" in e for e in v({"kindred": ["Elf", "elf"]})))
        self.assertTrue(any("unknown keys" in e for e in v({"genres": [], "note": "x"})))
        self.assertTrue(any("must be an object" in e for e in v(["horror"])))


class TestUserIntent(unittest.TestCase):
    def test_defaults(self):
        res = resolve_intent_defaults({})
        self.assertIsNone(res["archetype_preferences"]["primary_archetype"])
        self.assertEqual(res["deck_genre"], {"genres": [], "kindred": []})
        res = resolve_intent_defaults({"deck_genre": {"genres": ["horror"]}})
        self.assertEqual(res["deck_genre"], {"genres": ["horror"], "kindred": []})
        self.assertNotIn("deck_genre", res["metadata"])  # a known key, not swept into metadata

    def test_parse_carries_deck_genre(self):
        intent = parse_user_intent({"archetype_preferences": {"primary_archetype": "landfall"},
                                    "deck_genre": {"genres": ["budget"], "kindred": ["Elf"]}})
        self.assertEqual(intent.archetype_preferences.primary_archetype, "landfall")
        self.assertEqual(intent.deck_genre, DeckGenre(genres=["budget"], kindred=["Elf"]))
        self.assertIsNone(parse_user_intent({}).archetype_preferences.primary_archetype)

    def test_cardinality_rules(self):
        ok, _ = validate_intent({"archetype_preferences": {"primary_archetype": "voltron",
                                                           "secondary_archetypes": ["extra_combats", "cheat", "landfall"]}})
        self.assertTrue(ok)
        for ap, frag in [({"primary_archetype": "voltron", "secondary_archetypes": ["cheat", "blink", "ramp", "combo"]}, "at most 3"),
                         ({"primary_archetype": "voltron", "secondary_archetypes": ["voltron"]}, "both primary and secondary"),
                         ({"primary_archetype": None, "secondary_archetypes": ["voltron"]}, "needs a primary"),
                         ({"primary_archetype": "pirates"}, "is invalid")]:
            with self.subTest(frag=frag):
                ok, errors = validate_intent({"archetype_preferences": ap})
                self.assertFalse(ok)
                self.assertTrue(any(frag in e for e in errors), errors)
        ok, errors = validate_intent({"deck_genre": {"genres": list("abcdef")}})
        self.assertFalse(ok)


class TestJournalRoundTrip(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def save(self, payload):
        return journal.save_journal(self.r, DECK, payload)

    def pub(self):
        return journal.public(journal.load_journal(self.r, DECK))

    def test_null_primary(self):
        self.save({"archetype_preferences": {"primary_archetype": None, "secondary_archetypes": []}})
        self.assertIsNone(self.pub()["archetype_preferences"]["primary_archetype"])

    def test_primary_only_and_one_plus_three(self):
        self.save({"archetype_preferences": {"primary_archetype": "aristocrats", "secondary_archetypes": []}})
        self.assertEqual(self.pub()["archetype_preferences"]["primary_archetype"], "aristocrats")
        self.save({"archetype_preferences": {"primary_archetype": "voltron",
                                             "secondary_archetypes": ["extra_combats", "cheat", "landfall"]}})
        ap = self.pub()["archetype_preferences"]
        self.assertEqual((ap["primary_archetype"], ap["secondary_archetypes"]), ("voltron", ["extra_combats", "cheat", "landfall"]))

    def test_custom_values_and_alias_normalization(self):
        rec = self.save({"deck_genre": {"genres": ["Horror / Gothic", "Space western, but gothic"],
                                        "kindred": ["Elves", "Mice", "Changeling (any type)"]}})
        self.assertEqual(rec["deck_genre"], {"genres": ["horror", "Space western, but gothic"],
                                             "kindred": ["Elf", "Mouse", "Changeling (any type)"]})
        self.assertEqual(self.pub()["deck_genre"], rec["deck_genre"])

    def test_over_max_is_rejected_and_nothing_is_stored(self):
        self.save({"deck_genre": {"genres": ["horror"], "kindred": []}})
        for payload in [{"deck_genre": {"genres": list("abcdef"), "kindred": []}},
                        {"deck_genre": {"genres": [], "kindred": ["x" * 33]}},
                        {"archetype_preferences": {"primary_archetype": "tokens", "secondary_archetypes": ["a", "b"]}},
                        {"archetype_preferences": {"primary_archetype": "tokens",
                                                   "secondary_archetypes": ["blink", "ramp", "combo", "control"]}}]:
            with self.subTest(payload=payload), self.assertRaises(journal.JournalError):
                self.save(payload)
        self.assertEqual(self.pub()["deck_genre"]["genres"], ["horror"])

    def test_clear(self):
        self.save({"archetype_preferences": {"primary_archetype": "tokens", "secondary_archetypes": ["aristocrats"]},
                   "deck_genre": {"genres": ["budget"], "kindred": ["Elf"]}})
        self.save({"archetype_preferences": {"primary_archetype": None, "secondary_archetypes": []},
                   "deck_genre": {"genres": [], "kindred": []}})
        p = self.pub()
        self.assertIsNone(p["archetype_preferences"]["primary_archetype"])
        self.assertEqual(p["deck_genre"], {"genres": [], "kindred": []})

    def test_hidden_archetype_fields_ride_along(self):
        self.save({"archetype_preferences": {"primary_archetype": "tokens", "secondary_archetypes": [],
                                             "excluded_archetypes": ["stax"], "custom_themes": ["proliferate"]}})
        ap = self.pub()["archetype_preferences"]
        self.assertEqual((ap["excluded_archetypes"], ap["custom_themes"]), (["stax"], ["proliferate"]))

    def test_other_sections_unaffected_and_stamped(self):
        rec = self.save({"pet_cards": ["Plains"], "deck_genre": {"genres": ["meme"], "kindred": []}})
        self.assertEqual(rec["pet_cards"], ["Plains"])
        self.assertEqual(rec["_journal"]["genre_v"], G.TAXONOMY_VERSION)
        rec = self.save({"novelties": "Once."})  # a later partial save keeps the genre section
        self.assertEqual(rec["deck_genre"]["genres"], ["meme"])

    # -- Phantom "midrange" (taxonomy §0) --------------------------------------------------------
    def test_legacy_phantom_midrange_reads_as_unset(self):
        self.r.set(journal.KEY_PREFIX + DECK["registry_id"], json.dumps(_legacy_record()))
        self.assertIsNone(self.pub()["archetype_preferences"]["primary_archetype"])
        self.assertEqual(self.pub()["deck_genre"], {"genres": [], "kindred": []})
        self.assertEqual(self.pub()["deck_vision"]["description"], "Old.")

    def test_legacy_midrange_with_other_archetype_data_is_kept(self):
        self.r.set(journal.KEY_PREFIX + DECK["registry_id"], json.dumps(_legacy_record(secondary_archetypes=["tokens"])))
        self.assertEqual(self.pub()["archetype_preferences"]["primary_archetype"], "midrange")

    def test_saving_any_section_persists_the_cleanup(self):
        self.r.set(journal.KEY_PREFIX + DECK["registry_id"], json.dumps(_legacy_record()))
        self.save({"novelties": "x"})
        raw = json.loads(self.r.get(journal.KEY_PREFIX + DECK["registry_id"]))
        self.assertIsNone(raw["archetype_preferences"]["primary_archetype"])
        self.assertEqual(raw["_journal"]["genre_v"], G.TAXONOMY_VERSION)
        self.assertEqual(raw["_journal"]["created_at"], "2026-01-01T00:00:00+00:00")

    def test_deliberate_midrange_after_gn_is_kept(self):
        self.save({"archetype_preferences": {"primary_archetype": "midrange", "secondary_archetypes": []}})
        self.assertEqual(self.pub()["archetype_preferences"]["primary_archetype"], "midrange")


class TestCreatureCatalog(unittest.TestCase):
    def test_snapshot_fallback_and_redis_override(self):
        r = FakeRedis()
        self.assertEqual(G.creature_types(r), list(G.CREATURE_TYPES_SNAPSHOT))
        self.assertEqual(G.creature_types(None), list(G.CREATURE_TYPES_SNAPSHOT))
        r.set(G.CATALOG_KEY, json.dumps(["Wug"] * 5))  # too short to trust
        self.assertEqual(len(G.creature_types(r)), 351)
        r.set(G.CATALOG_KEY, "{not json")
        self.assertEqual(len(G.creature_types(r)), 351)
        r.set(G.CATALOG_KEY, json.dumps(list(G.CREATURE_TYPES_SNAPSHOT) + ["Wug"]))
        self.assertIn("Wug", G.creature_types(r))

    def test_refresh_stores_the_catalog(self):
        r = FakeRedis()
        n = G.refresh_creature_types(r, fetch=lambda url: {"data": list(G.CREATURE_TYPES_SNAPSHOT) + ["Wug"]})
        self.assertEqual(n, 352)
        self.assertIn("Wug", G.creature_types(r))
        with self.assertRaises(ValueError):
            G.refresh_creature_types(r, fetch=lambda url: {"data": []})


class TestHeaderStrip(unittest.TestCase):
    def test_empty_and_phantom_give_no_strip(self):
        self.assertIsNone(G.header_strip(journal.public(None)))
        self.assertIsNone(G.header_strip(journal.public(G.migrate_phantom_midrange(_legacy_record()))))

    def test_order_ranks_and_overflow(self):
        j = {"archetype_preferences": {"primary_archetype": "aristocrats", "secondary_archetypes": ["tokens"]},
             "deck_genre": {"genres": ["horror", "budget"], "kindred": ["Zombie", "Vampire"]}}
        s = G.header_strip(j)
        self.assertEqual([i["label"] for i in s["items"]], ["Aristocrats", "Zombie", "Horror / Gothic", "Tokens / Go-Wide"])
        self.assertEqual([i["rank"] for i in s["items"]], [1, 2, 3, 4])
        self.assertEqual(s["more_desktop"], {"n": 2, "labels": "Vampire, Budget"})
        self.assertEqual(s["more_phone"]["n"], 4)
        self.assertEqual(s["items"][0]["title"], "Style: Aristocrats")

    def test_custom_flag(self):
        s = G.header_strip({"deck_genre": {"genres": ["Noir-ish"], "kindred": ["Blorbo"]}})
        self.assertEqual([(i["kind"], i["custom"]) for i in s["items"]], [("kindred", True), ("theme", True)])


class _Pages(unittest.TestCase):
    """Real routes over FakeRedis (registry + journal both read from it)."""
    LEGACY = dict(DECK, name="Legacy Deck", id=999, registry_id="archidekt:999", source_id="999")

    @classmethod
    def setUpClass(cls):
        cls.r = FakeRedis()
        cls.r.set("decks", json.dumps([DECK, cls.LEGACY]))
        cls.r.set(journal.KEY_PREFIX + "archidekt:999", json.dumps(_legacy_record()))
        cls.client = TestClient(app_mod.app)

    def get(self, path):
        with patch.object(app_mod, "r", self.r), patch("app.inventory_view.build_inventory", return_value=INVENTORY):
            return self.client.get(path)

    def put(self, path, body):
        # The first save snapshots live metrics (prices = network); stub them out.
        with patch.object(app_mod, "r", self.r), patch("app.journal_mod.collect_context", return_value={}):
            return self.client.put(path, json=body)

    @staticmethod
    def strip(body):
        m = re.search(r'<span id="deckGenre"[^>]*>.*?</span>(?=\s*</div>)', body, re.S)
        return m.group(0) if m else None


class TestPagesAndApi(_Pages):
    def test_journal_section_follows_vision(self):
        body = self.get("/deck/Holy%20Hellfire/journal").text
        ids = re.findall(r'<section class="hud-form-section" id="([^"]+)"', body)
        self.assertEqual(ids[:4], ["j-vision", "j-genre", "j-archetype", "j-win"])
        self.assertIn('<a href="#j-genre">Deck Tags</a>', body)
        for k in ("archetype", "theme", "kindred"):
            self.assertIn(f'id="f-{k}" data-genre-field="{k}"', body)
        self.assertIn("/static/js/deck_genre.js", body)
        self.assertIn('"aristocrats"', body)        # option lists rendered with | tojson
        self.assertIn('"Assembly-Worker"', body)    # the creature-type catalog too
        self.assertIn('"archetype_preferences", "deck_genre"', body)  # both keys dirty-tracked
        self.assertIn(" · deck tags", body)

    def test_strip_hidden_without_genre_and_for_phantom_midrange(self):
        for path in ("/deck/Holy%20Hellfire/journal", "/deck/Legacy%20Deck/journal", "/deck/Legacy%20Deck/stats"):
            with self.subTest(path=path):
                s = self.strip(self.get(path).text)
                self.assertIsNotNone(s)
                self.assertRegex(s, r'<span id="deckGenre"[^>]* hidden>')
                self.assertNotIn("hud-chip", s)

    def test_set_edit_clear_through_the_api(self):
        api = "/api/decks/archidekt:417707/journal"
        res = self.put(api, {"archetype_preferences": {"primary_archetype": "aristocrats", "secondary_archetypes": ["tokens"]},
                             "deck_genre": {"genres": ["horror", "Noir-ish"], "kindred": ["Blorbo", "Zombies"]}})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["journal"]["deck_genre"], {"genres": ["horror", "Noir-ish"], "kindred": ["Blorbo", "Zombie"]})
        # (GET /journal also computes live prices over the network, so read the store directly)
        got = journal.public(journal.load_journal(self.r, DECK))
        self.assertEqual(got["archetype_preferences"]["primary_archetype"], "aristocrats")
        for path in ("/deck/Holy%20Hellfire/journal", "/deck/Holy%20Hellfire/stats", "/deck/Holy%20Hellfire"):
            with self.subTest(path=path):
                s = self.strip(self.get(path).text)
                self.assertNotIn(" hidden>", s.split(">", 1)[0] + ">")
                self.assertIn('data-rank="1" title="Style: Aristocrats"', s)
                self.assertIn('hud-chip--ghost hud-chip--custom" data-rank="2" title="Kindred: Blorbo"', s)
                self.assertIn('href="/deck/Holy%20Hellfire/journal#j-genre"', s)
        # >max is a 400 with the reason, and nothing changes
        bad = self.put(api, {"deck_genre": {"genres": list("abcdef"), "kindred": []}})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("at most 5", bad.text)
        # clear
        res = self.put(api, {"archetype_preferences": {"primary_archetype": None, "secondary_archetypes": []},
                             "deck_genre": {"genres": [], "kindred": []}})
        self.assertEqual(res.status_code, 200)
        self.assertRegex(self.strip(self.get("/deck/Holy%20Hellfire/journal").text), r'<span id="deckGenre"[^>]* hidden>')

    def test_options_endpoint(self):
        body = self.get("/api/deck-genre/options").json()
        self.assertEqual(len(body["options"]["archetypes"]), 30)
        self.assertEqual(body["options"]["limits"], {"archetype": 4, "theme": 5, "kindred": 5, "custom_max_len": 32})
        self.assertEqual(len(body["creature_types"]), 351)

    def test_css_defines_the_kit_classes(self):
        css = (ROOT / "static" / "css" / "hud.css").read_text()
        for cls in ("hud-sr", "hud-tagfield", "hud-tagfield-count", "hud-tagchip", "hud-chip--custom", "hud-chip-star",
                    "hud-tagopt", "hud-picker-group", "hud-picker-note", "hud-genre-strip", "hud-chip-more-desktop",
                    "hud-chip-more-phone"):
            with self.subTest(cls=cls):
                self.assertIn("." + cls, css)


class TestEngineGuard(unittest.TestCase):
    def theme(self, ap):
        from analytics.engine import DeckAnalysisEngine
        from deck_intake import DeckIntakeService
        from tests.test_analysis_engine import SAMPLE_AGGRO_TOKENS_DECKLIST
        intake = DeckIntakeService().process(decklist=SAMPLE_AGGRO_TOKENS_DECKLIST, intent={"archetype_preferences": ap})
        return DeckAnalysisEngine().analyze(deck=intake).theme_analysis

    def test_unpatterned_ids_are_skipped_not_scored_zero(self):
        plain = self.theme({"primary_archetype": None})
        landfall = self.theme({"primary_archetype": "landfall", "secondary_archetypes": ["storm", "tokens"]})
        self.assertEqual(landfall.primary_archetype_score, plain.primary_archetype_score)  # scored like unset
        self.assertEqual([s.archetype for s in landfall.secondary_archetypes], ["tokens"])
        self.assertIn("Not scored (no card pattern yet): landfall, storm.", landfall.description)


@unittest.skipUnless(shutil.which("node"), "node not installed: deck_genre.js tests need it")
class TestDeckGenreJs(_Pages):
    HARNESS = r"""
const G = require(process.argv[1]).create(JSON.parse(process.argv[2]), JSON.parse(process.argv[3]));
const v = { archetype: ["aristocrats", "tokens"], theme: ["horror", "Noir-ish"], kindred: ["Zombie", "Blorbo", "Vampire"] };
process.stdout.write(JSON.stringify({
  types: ["Elves", "mice", "FOXES", "zombie", "Blorbo", ""].map(G.canonType),
  seed: ["Horror / Gothic", "pet deck", "nope"].map(q => G.canonSeed(Object.fromEntries(JSON.parse(process.argv[2]).genres.map(g => [g.id, g])), q)),
  payload: G.toPayload(v, { excluded_archetypes: ["stax"], custom_themes: [] }),
  emptyPayload: G.toPayload({ archetype: [], theme: [], kindred: [] }, {}),
  back: G.fromJournal({ archetype_preferences: { primary_archetype: "aristocrats", secondary_archetypes: ["tokens"] },
                        deck_genre: { genres: ["horror", "Noir-ish"], kindred: ["Zombie", "Blorbo", "Vampire"] } }),
  strip: G.stripHtml(v, "/deck/Holy%20Hellfire/journal#j-genre"),
  emptyStrip: G.stripHtml({ archetype: [], theme: [], kindred: [] }, "#"),
  read: G.readHtml({ primary: { name: "Aristocrats", coverage: 0.72 }, secondary: [{ name: "Tokens", coverage: 0.54 }] }, "/r", true),
  noRead: G.readHtml({ primary: null }, "/r"),
}));
"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        res = subprocess.run(["node", "-e", cls.HARNESS, str(GENRE_JS), json.dumps(G.page_options()),
                              json.dumps(list(G.CREATURE_TYPES_SNAPSHOT))], capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        cls.out = json.loads(res.stdout)

    def test_canonicalization_matches_python(self):
        self.assertEqual(self.out["types"], ["Elf", "Mouse", "Fox", "Zombie", None, None])
        self.assertEqual(self.out["seed"], ["horror", "pet_deck", None])

    def test_payload_shape(self):
        self.assertEqual(self.out["payload"], {
            "archetype_preferences": {"primary_archetype": "aristocrats", "secondary_archetypes": ["tokens"],
                                      "excluded_archetypes": ["stax"], "custom_themes": []},
            "deck_genre": {"genres": ["horror", "Noir-ish"], "kindred": ["Zombie", "Blorbo", "Vampire"]}})
        self.assertIsNone(self.out["emptyPayload"]["archetype_preferences"]["primary_archetype"])
        self.assertEqual(self.out["back"], {"archetype": ["aristocrats", "tokens"], "theme": ["horror", "Noir-ish"],
                                            "kindred": ["Zombie", "Blorbo", "Vampire"]})

    def test_js_strip_matches_the_server_strip(self):
        self.put("/api/decks/archidekt:417707/journal",
                 {"archetype_preferences": {"primary_archetype": "aristocrats", "secondary_archetypes": ["tokens"]},
                  "deck_genre": {"genres": ["horror", "Noir-ish"], "kindred": ["Zombie", "Blorbo", "Vampire"]}})
        server = self.strip(self.get("/deck/Holy%20Hellfire/stats").text)
        inner = re.sub(r"^<span[^>]*>|</span>$", "", server)
        self.assertEqual(html.unescape(inner), html.unescape(self.out["strip"]))
        self.assertEqual(self.out["emptyStrip"], "")

    def test_review_read(self):
        self.assertEqual(self.out["read"], "Deep Review's read (computed from the cards): <b>Aristocrats</b> 72% · "
                                           "<b>Tokens</b> 54% · stale · <a href=\"/r\">Open Review</a>")
        self.assertEqual(self.out["noRead"], "")


if __name__ == "__main__":
    unittest.main()
