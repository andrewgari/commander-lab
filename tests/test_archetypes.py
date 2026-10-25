"""Vocab PR3 archetype registry: CRUD + version counter, archetype_match, and per-deck selection."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

import app as app_module
from analytics import archetypes as AR


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.store:
            return None
        self.store[k] = v
        return True

    def incr(self, k):
        self.store[k] = str(int(self.store.get(k, 0)) + 1)
        return int(self.store[k])


ROLE = {"tag": "produces:sacrifice", "min_count": 2, "weight": 1.5}
STORED_ROLE = {"tag": "legacy:produces:sacrifice", "min_count": 2, "weight": 1.5}  # legacy alias resolves at write


class TestStore(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_starts_empty_no_seed_call(self):
        """No built-in seed data: an unread registry is simply absent, and reading it
        never materializes anything -- it stays an empty list with version 0."""
        self.assertEqual(AR.list_archetypes(self.r), [])
        self.assertEqual(AR.get_version(self.r), 0)
        self.assertIsNone(self.r.get(AR.ARCHETYPES_KEY))
        AR.list_archetypes(self.r)
        self.assertEqual(AR.get_version(self.r), 0)  # reading never bumps or seeds

    def test_crud_and_version_bump(self):
        v0 = AR.get_version(self.r)
        a, v1 = AR.create_archetype(self.r, {"name": "Sac Package", "description": "d", "required_roles": [ROLE]})
        self.assertEqual((a["id"], v1), ("sac-package", v0 + 1))
        self.assertEqual(AR.get_archetype(self.r, "sac-package")["required_roles"], [STORED_ROLE])
        a2, v2 = AR.update_archetype(self.r, "sac-package", {"description": "new"})
        self.assertEqual((a2["description"], a2["required_roles"], v2), ("new", [STORED_ROLE], v1 + 1))
        self.assertFalse(a2["draft"])
        v3 = AR.delete_archetype(self.r, "sac-package")
        self.assertEqual(v3, v2 + 1)
        with self.assertRaises(AR.ArchetypeNotFound):
            AR.get_archetype(self.r, "sac-package")

    def test_failed_write_does_not_bump(self):
        AR.create_archetype(self.r, {"name": "Storm"})
        v = AR.get_version(self.r)
        for body in ({"name": ""}, {"name": "X", "required_roles": [{"tag": "produces:nope"}]}, {"name": "Storm"}):
            with self.assertRaises(AR.ArchetypeError):
                AR.create_archetype(self.r, body)
        with self.assertRaises(AR.ArchetypeNotFound):
            AR.delete_archetype(self.r, "missing")
        self.assertEqual(AR.get_version(self.r), v)

    def test_name_collision(self):
        AR.create_archetype(self.r, {"name": "My Thing"})
        with self.assertRaises(AR.ArchetypeError):
            AR.create_archetype(self.r, {"name": "my thing"})


def _fake_source(mapping):
    def src(q):
        for key, names in mapping.items():
            if key in q:
                return {"names": list(names), "total": len(names), "error": None, "oversized": False}
        return {"names": [], "total": 0, "error": None, "oversized": False}
    return src


class TestMatch(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.src = _fake_source({"sacrifice-outlet": ["Outlet", "Fodder"], "death-trigger": ["Payoff"]})

    def test_whole_deck_coverage_not_single_tag(self):
        """A single copy of the most common tag should NOT be enough: coverage is scored
        against every required role across the whole deck, weighted."""
        archetypes = [{
            "id": "aristocrats", "name": "Aristocrats",
            "required_roles": [
                {"tag": "produces:sacrifice", "min_count": 3, "weight": 2},
                {"tag": "consumes:death", "min_count": 4, "weight": 2},
            ],
        }]
        # Only one copy of a sacrifice-outlet card, nothing for death.
        deck_shallow = {"cards": [{"name": "Fodder", "quantity": 1}]}
        shallow = AR.archetype_match(deck_shallow, archetypes, self.r, self.src)[0]
        self.assertLess(shallow["coverage"], 0.5)

        deck_deep = {"cards": [{"name": "Outlet", "quantity": 3}, {"name": "Payoff", "quantity": 4}]}
        deep = AR.archetype_match(deck_deep, archetypes, self.r, self.src)[0]
        self.assertEqual(deep["coverage"], 1.0)
        # The breakdown names the matched cards.
        hit = {row["tag"]: row for row in deep["roles_hit"]}
        self.assertEqual([c["name"] for c in hit["legacy:produces:sacrifice"]["cards"]], ["Outlet"])

    def test_best_match_ties_break_on_name(self):
        archetypes = [
            {"id": "b", "name": "Beta", "required_roles": []},
            {"id": "a", "name": "Alpha", "required_roles": []},
        ]
        ranked = AR.archetype_match({"cards": []}, archetypes, self.r, self.src)
        self.assertEqual([r["archetype"]["id"] for r in ranked], ["a", "b"])


class TestSelection(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_default_is_auto(self):
        self.assertEqual(AR.get_selection(self.r, "deck1"), AR.default_selection())

    def test_manual_requires_known_archetype(self):
        with self.assertRaises(AR.ArchetypeNotFound):
            AR.set_selection(self.r, "deck1", {"mode": "manual", "archetype_id": "nope"})

    def test_manual_then_reset(self):
        a, _ = AR.create_archetype(self.r, {"name": "Pick Me"})
        rec = AR.set_selection(self.r, "deck1", {"mode": "manual", "archetype_id": a["id"]})
        self.assertEqual(rec["archetype_id"], a["id"])
        self.assertEqual(AR.get_selection(self.r, "deck1")["archetype_id"], a["id"])
        AR.reset_selection(self.r, "deck1")
        self.assertEqual(AR.get_selection(self.r, "deck1")["mode"], "auto")

    def test_resolve_for_deck_prefers_manual_override(self):
        a, _ = AR.create_archetype(self.r, {"name": "Chosen"})
        AR.set_selection(self.r, "deck1", {"mode": "manual", "archetype_id": a["id"]})
        out = AR.resolve_for_deck(self.r, "deck1", [])
        self.assertEqual(out["archetype"]["id"], a["id"])

    def test_resolve_for_deck_falls_back_to_best_match(self):
        AR.create_archetype(self.r, {"name": "High Coverage", "required_roles": [ROLE]})
        AR.create_archetype(self.r, {"name": "No Roles"})
        src = _fake_source({"sacrifice-outlet": ["Outlet"]})
        out = AR.resolve_for_deck(self.r, "deck1", [{"name": "Outlet", "quantity": 2}], src)
        self.assertEqual(out["selection"]["mode"], "auto")
        self.assertEqual(out["archetype"]["id"], "high-coverage")


class TestAPI(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch

        self.fake = FakeRedis()
        p = patch.object(app_module, "r", self.fake)
        p.start()
        self.addCleanup(p.stop)
        self.client = TestClient(app_module.app)

    def test_archetypes_crud_endpoints(self):
        res = self.client.get("/api/archetypes")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["success"])
        created0 = self.client.post("/api/archetypes", json={"name": "Seed", "description": "d",
                                                              "required_roles": [ROLE]})
        self.assertEqual(created0.status_code, 201)
        res = self.client.get("/api/archetypes")
        body = res.json()
        self.assertTrue(len(body["archetypes"]) >= 1)
        self.assertTrue(len(body["vocabulary"]) >= 1)

        created = self.client.post("/api/archetypes", json={"name": "New One", "description": "d",
                                                             "required_roles": [ROLE]})
        self.assertEqual(created.status_code, 201)
        aid = created.json()["archetype"]["id"]

        got = self.client.get(f"/api/archetypes/{aid}")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.json()["archetype"]["name"], "New One")

        updated = self.client.put(f"/api/archetypes/{aid}", json={"description": "updated"})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["archetype"]["description"], "updated")

        deleted = self.client.delete(f"/api/archetypes/{aid}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get(f"/api/archetypes/{aid}").status_code, 404)

    def test_archetype_create_rejects_bad_role(self):
        res = self.client.post("/api/archetypes", json={"name": "Bad", "required_roles": [{"tag": "nope"}]})
        self.assertEqual(res.status_code, 400)


class TestDescriptionRendering(unittest.TestCase):
    def test_cap_raised_to_4000(self):
        self.assertEqual(AR.MAX_DESCRIPTION, 4000)
        ok = AR.clean_fields({"name": "X", "description": "x" * 4000})
        self.assertEqual(len(ok["description"]), 4000)
        with self.assertRaises(AR.ArchetypeError) as ctx:
            AR.clean_fields({"name": "X", "description": "x" * 4001})
        self.assertIn("4000", str(ctx.exception))

    def test_markdown_subset_rendered(self):
        html = AR.render_description_html("Grindy **midrange** deck.\n\n- `loops`\n- *card draw*")
        self.assertIn("<strong>midrange</strong>", html)
        self.assertIn("<ul>", html)
        self.assertIn("<code>loops</code>", html)
        self.assertIn("<em>card draw</em>", html)

    def test_nl2br_preserves_single_newlines(self):
        self.assertIn("<br>", AR.render_description_html("line one\nline two"))

    def test_list_directly_after_intro_line(self):
        """GitHub-style: a list may interrupt a paragraph (no blank line required)."""
        html = AR.render_description_html("Key engines:\n- `loops`\n- *card draw*")
        self.assertIn("<p>Key engines:</p>", html)
        self.assertIn("<ul>", html)
        self.assertIn("<li><code>loops</code></li>", html)
        self.assertNotIn("<br>\n- ", html)  # dashes must not render as literal text
        # Ordered list works the same way.
        ordered = AR.render_description_html("Steps:\n1. one\n2. two")
        self.assertIn("<ol>", ordered)
        # A plain-text line that merely starts with '-' (no space) is NOT a list.
        not_a_list = AR.render_description_html("notes:\n-5 degrees of tilt")
        self.assertNotIn("<ul>", not_a_list)
        # A lazy continuation line inside a list must not split it in two.
        one_list = AR.render_description_html("- a\n  continued\n- b")
        self.assertEqual(one_list.count("<ul>"), 1)

    def test_raw_html_and_scripts_stripped(self):
        html = AR.render_description_html("<script>alert(1)</script>**ok**")
        self.assertNotIn("script", html.lower())
        self.assertNotIn("alert", html)
        self.assertIn("<strong>ok</strong>", html)

    def test_empty_renders_empty(self):
        self.assertEqual(AR.render_description_html(""), "")
        self.assertEqual(AR.render_description_html(None), "")
        self.assertEqual(AR.description_excerpt_html(""), "")

    def test_excerpt_is_first_block_only(self):
        html = AR.render_description_html("First para.\n\nSecond para.")
        self.assertEqual(AR.description_excerpt_html(html), "<p>First para.</p>")
        listed = AR.render_description_html("- one\n- two\n\nTrailing paragraph.")
        self.assertEqual(AR.description_excerpt_html(listed), "<ul>\n<li>one</li>\n<li>two</li>\n</ul>")

    def test_legacy_plain_text_round_trips_unchanged(self):
        """Source is stored verbatim; a pre-change plain-text entry still validates and renders."""
        legacy = "Midrange value pile.\nWins late."
        fields = AR.clean_fields({"name": "Legacy", "description": legacy})
        self.assertEqual(fields["description"], legacy)
        html = AR.render_description_html(fields["description"])
        self.assertIn("Midrange value pile.", html)
        self.assertIn("Wins late.", html)
        self.assertIn("<br>", html)


class TestDescriptionAPI(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch

        self.fake = FakeRedis()
        p = patch.object(app_module, "r", self.fake)
        p.start()
        self.addCleanup(p.stop)
        self.client = TestClient(app_module.app)

    def test_render_preview_endpoint(self):
        res = self.client.post("/api/archetypes/render-preview",
                               json={"description": "Grindy **deck**.\n\n- one\n<script>alert(1)</script>"})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["success"])
        self.assertIn("<strong>deck</strong>", body["html"])
        self.assertIn("<li>one</li>", body["html"])
        self.assertNotIn("script", body["html"].lower())

    def test_render_preview_rejects_non_string(self):
        res = self.client.post("/api/archetypes/render-preview", json={"description": 5})
        self.assertEqual(res.status_code, 400)

    def test_list_includes_rendered_description_fields(self):
        created = self.client.post("/api/archetypes", json={
            "name": "Rendered", "description": "First **para**.\n\nSecond para.", "required_roles": [ROLE]})
        self.assertEqual(created.status_code, 201)
        body = self.client.get("/api/archetypes").json()
        item = next(a for a in body["archetypes"] if a["name"] == "Rendered")
        self.assertEqual(item["description"], "First **para**.\n\nSecond para.")  # source preserved
        self.assertEqual(item["description_excerpt_html"], "<p>First <strong>para</strong>.</p>")
        self.assertIn("Second para.", item["description_html"])

    def test_create_rejects_over_4000_chars(self):
        res = self.client.post("/api/archetypes", json={"name": "Too Long", "description": "x" * 4001})
        self.assertEqual(res.status_code, 400)
        self.assertIn("4000", res.json()["error"])


if __name__ == "__main__":
    unittest.main()
