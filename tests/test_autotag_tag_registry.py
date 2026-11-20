"""Tests for the Auto Tag vocabulary registry (Vocab cleanup PR4)."""

import unittest

from src.server.domain.tags import tag_registry as tr
from src.server.domain.tags import engine


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, k):
        v = self.store.get(k)
        return v if isinstance(v, (str, type(None))) else None

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.store:
            return None
        self.store[k] = v
        return True

    def exists(self, k):
        return 1 if k in self.store else 0


class TestSeeding(unittest.TestCase):
    def test_seeds_once_from_builtin_lists(self):
        r = FakeRedis()
        tr.ensure_seeded(r)
        self.assertEqual(set(tr.get_function_ids(r)), set(tr.BUILTIN_FUNCTION_TAGS))
        self.assertEqual(set(tr.get_theme_ids(r)), set(tr.BUILTIN_THEME_TAGS))

    def test_deleted_tag_is_not_reseeded(self):
        r = FakeRedis()
        tr.ensure_seeded(r)
        tr.delete_tag(r, "voltron")
        tr.ensure_seeded(r)  # calling again must be a no-op
        self.assertNotIn("voltron", tr.get_all_ids(r))


class TestCrud(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        tr.ensure_seeded(self.r)

    def test_create_adds_new_tag(self):
        tag = tr.create_tag(self.r, "Combo Piece", "function")
        self.assertEqual(tag["category"], "function")
        self.assertIn(tag["id"], tr.get_function_ids(self.r))

    def test_create_rejects_duplicate_name(self):
        with self.assertRaises(tr.TagRegistryError):
            tr.create_tag(self.r, "ramp", "function")

    def test_create_rejects_bad_category(self):
        with self.assertRaises(tr.TagRegistryError):
            tr.create_tag(self.r, "whatever", "nonsense")

    def test_rename_changes_name_not_id(self):
        before = [t for t in tr.list_tags(self.r) if t["id"] == "ramp"][0]
        renamed = tr.rename_tag(self.r, "ramp", "Mana Ramp")
        self.assertEqual(renamed["id"], before["id"])
        self.assertEqual(renamed["name"], "Mana Ramp")

    def test_rename_missing_tag_raises(self):
        with self.assertRaises(tr.TagNotFound):
            tr.rename_tag(self.r, "does_not_exist", "x")

    def test_delete_removes_tag(self):
        tr.delete_tag(self.r, "blink")
        self.assertNotIn("blink", tr.get_all_ids(self.r))

    def test_delete_missing_tag_raises(self):
        with self.assertRaises(tr.TagNotFound):
            tr.delete_tag(self.r, "does_not_exist")


class TestEngineIntegration(unittest.TestCase):
    """load_tag_vocab reassigns engine.py's module globals in place, and the
    engine filters its output against the live registry."""

    def setUp(self):
        self.r = FakeRedis()
        tr.ensure_seeded(self.r)

    def tearDown(self):
        # Don't leak a mutated vocabulary into other test modules that import
        # engine's default (builtin) FUNCTION_TAGS/THEME_TAGS.
        engine.FUNCTION_TAGS = list(tr.BUILTIN_FUNCTION_TAGS)
        engine.THEME_TAGS = list(tr.BUILTIN_THEME_TAGS)
        engine.ALL_TAGS = set(engine.FUNCTION_TAGS) | set(engine.THEME_TAGS)
        engine.PRIORITY_ORDER = engine.FUNCTION_TAGS + ["kindred:*"] + engine.THEME_TAGS

    def test_load_tag_vocab_reflects_deletions(self):
        tr.delete_tag(self.r, "voltron")
        engine.load_tag_vocab(self.r)
        self.assertNotIn("voltron", engine.THEME_TAGS)
        self.assertNotIn("voltron", engine.ALL_TAGS)
        self.assertNotIn("voltron", engine.PRIORITY_ORDER)

    def test_run_auto_tag_drops_deleted_tag_from_output(self):
        tr.delete_tag(self.r, "sacrifice")
        engine.load_tag_vocab(self.r)
        deck = {
            "name": "Test",
            "commanders": [{"name": "Test Commander", "type_line": "Legendary Creature — Human",
                             "oracle_text": "", "cmc": 2}],
            "color_identity": ["B"],
            "color_identity_size": 1,
            "cards": [
                {"name": "Sac Outlet", "type_line": "Creature — Human",
                 "oracle_text": "Sacrifice a creature: Add {B}.", "cmc": 1},
            ],
        }
        result = engine.run_auto_tag(deck)
        all_tags = {t["tag"] for c in result["cards"] for t in c["tags"]}
        all_tags |= {t["tag"] for t in result["deck_tags"]}
        self.assertNotIn("sacrifice", all_tags)


if __name__ == "__main__":
    unittest.main()
