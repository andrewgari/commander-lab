"""RV-4: Deep Review tab on the deck page (docs/REVIEW_SPEC.md).

- analytics.review_view: section -> partial routing, fallback for unknown/odd data, markdown, meta;
- one render test per section type (real RV-2 metrics through the RV-3 doc builder);
- unknown-type fallback (markdown string, kv, json) and wrong-shape fallback;
- /deck/{name}/review: fresh, stale (badge + reason), empty ("Build review"), storage error;
- the 7th deck tab, 7-key hotkey, and the page never rebuilds on load.
"""
import copy
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from jinja2 import Environment, FileSystemLoader

import app as app_mod
from analytics import review_doc as rd
from analytics import review_metrics as rm
from analytics import review_view as rv
from analytics import theory_view
from tests.test_deck_stats_ui import DECK, _DeckPage
from tests.test_review_doc import FakeRedis
from tests.test_review_metrics import make_inputs

ROOT = Path(__file__).resolve().parent.parent
RID = DECK["registry_id"]
METRICS = rm.compute_review(make_inputs())


def _doc(prose=True, status="ok", **over):
    p = {k: f"{k} prose." for k in rd.SECTION_ORDER} if prose else None
    d = rd.build_doc(RID, copy.deepcopy(METRICS), p, oracle_status=status, oracle_note="down" if status != "ok" else "")
    d.update(over)
    return d


def _section(type_):
    return next(s for s in _doc()["sections"] if s["type"] == type_)


def render(section):
    env = Environment(loader=FileSystemLoader(str(ROOT / "templates")), autoescape=True)
    return env.get_template(rv.section_template(section)).render(section=section, rv=rv, tv=theory_view)


class TestRouting(unittest.TestCase):
    def test_every_known_type_has_a_partial_file(self):
        for t in rv.RENDERERS:
            with self.subTest(t=t):
                self.assertTrue((ROOT / "templates" / "partials" / "review" / f"{t}.html").exists())
        self.assertEqual(set(rv.RENDERERS), set(rd.SECTION_ORDER))

    def test_real_doc_sections_use_their_own_partial(self):
        for s in _doc()["sections"]:
            with self.subTest(t=s["type"]):
                self.assertEqual(rv.section_template(s), f"partials/review/{s['type']}.html")

    def test_unknown_type_and_wrong_shape_fall_back(self):
        self.assertEqual(rv.section_template({"type": "forge_sim", "data": {"a": 1}}), rv.FALLBACK)
        self.assertEqual(rv.section_template({"type": "focus", "data": "oops"}), rv.FALLBACK)
        self.assertEqual(rv.section_template({"type": "stragglers", "data": {"cards": "x"}}), rv.FALLBACK)
        self.assertEqual(rv.section_template({"type": "genre", "data": None}), rv.FALLBACK)
        self.assertEqual(rv.section_template(None), rv.FALLBACK)

    def test_sections_skip_junk_and_keep_order(self):
        doc = _doc()
        doc["sections"].insert(1, "junk")
        got = rv.sections(doc)
        self.assertEqual([i["section"]["type"] for i in got], list(rd.SECTION_ORDER))
        self.assertEqual(got[0]["prose"], "genre prose.")

    def test_markdown_blocks_are_plain_data(self):
        b = rv.md_blocks("## Head\n\nline one\nline two\n\n- a\n- b\n1. c\n<script>x</script>")
        self.assertEqual([x["kind"] for x in b], ["h", "p", "ul", "p"])
        self.assertEqual(b[1]["text"], "line one line two")
        self.assertEqual(b[2]["items"], ["a", "b", "c"])
        self.assertEqual(rv.md_blocks(None), [])


class TestSectionRenderers(unittest.TestCase):
    def test_genre(self):
        h = render(_section("genre"))
        self.assertIn('data-kind="primary"', h)
        self.assertIn("Aristocrats", h)
        self.assertIn("100% coverage", h)
        self.assertIn("produces sacrifice 1/1", h)
        self.assertIn('data-kind="secondary"', h)
        self.assertIn("Tokens", h)

    def test_genre_none_picked_and_missing_roles(self):
        sec = {"type": "genre", "data": {"primary": None, "secondary": [], "ranking": []}}
        self.assertIn("No genre reached", render(sec))
        sec = {"type": "genre", "data": {"primary": {"id": "x", "name": "Storm", "coverage": 0.4, "roles_hit": [],
                                                      "roles_missing": [{"tag": "produces:rituals"}]},
                                          "secondary": [], "ranking": []}}
        self.assertIn("produces rituals missing", render(sec))

    def test_composition(self):
        h = render(_section("composition"))
        self.assertIn("vs Command Zone 2025", h)
        self.assertIn('data-row="lands"', h)
        self.assertIn("−6", h)
        self.assertIn("hud-chip--danger", h)  # status short

    def test_curve_tempo(self):
        h = render(_section("curve_tempo"))
        self.assertIn("Commander on curve", h)
        self.assertIn("99.9%", h)
        self.assertIn("Unspent mana", h)
        self.assertIn("1.8", h)
        self.assertIn("Active share", h)
        self.assertIn("Durdle tax", h)
        self.assertIn("<td class=\"is-primary\">T6</td>", h)
        self.assertIn("Durdle cards", h)
        self.assertIn("setup only", h)

    def test_focus_win_lines_and_journal_diff(self):
        sec = copy.deepcopy(_section("focus"))
        sec["data"]["journal"] = {"claimed": ["mill"], "present": True}
        sec["data"]["claimed_unsupported"] = ["mill"]
        h = render(sec)
        self.assertIn('data-line="burn"', h)
        self.assertIn('data-line="combo"', h)
        self.assertIn("Gnaw Pet, Laboratory Maniac", h)
        self.assertRegex(h, r'data-diff="claimed_unsupported">\s*<span class="hud-chip hud-chip--danger">Mill')
        self.assertRegex(h, r'data-diff="supported_unclaimed">.*Alternate win')
        self.assertIn("win cons claimed: mill", h)

    def test_focus_no_journal(self):
        h = render(_section("focus"))
        self.assertIn("no Journal yet", h)
        self.assertRegex(h, r'data-diff="claimed_unsupported">\s*<span class="hud-freshness">none')

    def test_strong_cards(self):
        h = render(_section("strong_cards"))
        self.assertIn('data-card="Blood Artist"', h)
        self.assertIn("win line", h)
        self.assertIn("engine", h)
        self.assertIn("1.00", h)
        self.assertIn("3 strong", h)

    def test_stragglers_show_durdle_and_weak_reasons(self):
        h = render(_section("stragglers"))
        self.assertIn('data-card="Pestilence"', h)
        self.assertIn("weak: 0 edges", h)
        self.assertIn("durdle: setup only", h)
        self.assertIn('hud-chip--signal">durdle<', h)
        self.assertIn("1.15", h)

    def test_pet_cards_are_protected_not_stragglers(self):
        sec = copy.deepcopy(_section("stragglers"))
        sec["data"]["protected_pets"] = [{"name": "Pet Rock", "protected": True, "would_be_straggler": True}]
        sec["data"]["cards"] = [c for c in sec["data"]["cards"] if c["name"] != "Pet Rock"]
        h = render(sec)
        self.assertIn('id="protectedPets"', h)
        self.assertIn('data-protected="true"', h)
        self.assertIn("Would be a straggler, but it is a pet card.", h)
        # Pet Rock appears only inside the protected list.
        self.assertEqual(h.count('data-card="Pet Rock"'), 1)
        self.assertGreater(h.index('data-card="Pet Rock"'), h.index('id="protectedPets"'))

    def test_stragglers_empty(self):
        sec = {"type": "stragglers", "data": {"cards": [], "protected_pets": []}}
        self.assertIn("No stragglers", render(sec))

    def test_combos_engines(self):
        h = render(_section("combos_engines"))
        self.assertIn('data-combo="1-2"', h)
        self.assertIn("Gnaw Pet + Laboratory Maniac", h)
        self.assertIn("missing Foo (owned)", h)
        self.assertIn("tokens → death → life_loss", h)

    def test_combos_error_note(self):
        sec = {"type": "combos_engines", "data": {"combos_error": "timeout", "combos_in_deck": [],
                                                  "combos_one_away": [], "engines": []}}
        h = render(sec)
        self.assertIn("Combo lookup failed: timeout", h)
        self.assertIn("No complete combos", h)

    def test_oracle_summary_ok_markdown(self):
        sec = {"type": "oracle_summary", "data": {"status": "ok", "text": "## Verdict\n\nSolid.\n\n- one\n- two"}}
        h = render(sec)
        self.assertIn("<h3", h)
        self.assertIn("Solid.", h)
        self.assertIn("<li>two</li>", h)
        self.assertNotIn("hud-chip", h)

    def test_oracle_summary_offline_and_stripped(self):
        sec = {"type": "oracle_summary", "data": {"status": "offline", "text": rd.OFFLINE_NOTE, "note": "conn refused",
                                                  "stripped_sentences": 2}}
        h = render(sec)
        self.assertIn("Explanation offline", h)
        self.assertIn("press Rebuild to retry", h)
        self.assertIn("conn refused", h)
        self.assertIn("2 sentences removed", h)

    def test_prose_is_escaped(self):
        sec = {"type": "oracle_summary", "data": {"status": "ok", "text": "<script>alert(1)</script>"}}
        self.assertNotIn("<script>", render(sec))


class TestFallback(unittest.TestCase):
    def test_unknown_type_markdown_string(self):
        h = render({"type": "forge_sim", "title": "Forge", "data": "## Dead draws\n\n- Foo\n- Bar\n\nplain"})
        self.assertIn('data-fallback="markdown"', h)
        self.assertIn("Dead draws", h)
        self.assertIn("<li>Bar</li>", h)
        self.assertIn("plain", h)

    def test_unknown_type_kv_json_list_empty(self):
        self.assertIn('data-fallback="kv"', render({"type": "x", "data": {"Dead draw rate": 0.12}}))
        self.assertIn("Dead draw rate", render({"type": "x", "data": {"Dead draw rate": 0.12}}))
        self.assertIn('data-fallback="json"', render({"type": "x", "data": {"a": {"b": [1]}}}))
        self.assertIn('data-fallback="list"', render({"type": "x", "data": ["a", "b"]}))
        self.assertIn("No content", render({"type": "x", "data": None}))

    def test_known_type_with_broken_data_does_not_raise(self):
        h = render({"type": "focus", "data": {"win_lines": "not a list"}})
        self.assertIn("hud-doc-fallback", h)


class TestMeta(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(rv.doc_meta(None), {"exists": False})

    def test_fresh(self):
        m = rv.doc_meta(_doc())
        self.assertTrue(m["exists"])
        self.assertFalse(m["stale"])
        self.assertEqual(m["oracle_label"], "Prose by oracle")

    def test_stale_reason_default(self):
        m = rv.doc_meta(_doc(stale=True, stale_reason=None))
        self.assertTrue(m["stale"])
        self.assertIn("changed", m["stale_reason"])

    def test_offline_status(self):
        m = rv.doc_meta(_doc(prose=False, status="offline"))
        self.assertEqual((m["oracle_status"], m["oracle_label"]), ("offline", "Explanation offline"))


# --- the page --------------------------------------------------------------------------------


class _ReviewPage(_DeckPage):
    PATH = "/deck/Holy%20Hellfire/review"
    ACTIVE = "Review"
    DOC = None
    PATCH_STALE = None

    @classmethod
    def setUpClass(cls):
        cls.fake = FakeRedis()
        if cls.DOC is not None:
            rd.save(cls.fake, copy.deepcopy(cls.DOC))
        # refresh_stale recomputes against the deck: pin it so the stored flag decides.
        with patch.object(app_mod, "r", cls.fake), \
                patch.object(rd, "stale_reason", lambda r, doc, deck: doc.get("stale_reason") if doc.get("stale") else None):
            super().setUpClass()

    def test_tabs_have_keys_and_mark_the_active_tab(self):
        nav = self.body.split('class="hud-tabs hud-deck-tabs"', 1)[1].split("</nav>", 1)[0]
        tabs = re.findall(r'<a class="hud-tab[^"]*" href="([^"]+)"[^>]*data-key="(\d)"', nav)
        self.assertEqual([k for _, k in tabs], list("1234567"))
        self.assertEqual(tabs[6][0], "/deck/Holy%20Hellfire/review")
        active = re.findall(r'<a class="hud-tab active"[^>]*aria-current="page"[^>]*>([^<]+)', nav)
        self.assertEqual([a.strip() for a in active], ["Review"])

    def test_tab_order_matches_other_pages_hrefs(self):
        # the shared stats-page test lists 7 hrefs; keep this page consistent with it.
        self.assertIn('href="/deck/Holy%20Hellfire/review"', self.html)

    def test_never_builds_on_load(self):
        self.assertNotIn("review:job:", "".join(self.fake.kv))


class TestFreshPage(_ReviewPage, unittest.TestCase):
    DOC = _doc()

    def test_renders_all_sections_in_order(self):
        types = re.findall(r'<section class="hud-doc-section" data-type="([a-z_]+)"', self.html)
        self.assertEqual(types, list(rd.SECTION_ORDER))

    def test_fresh_state(self):
        self.assertIn('data-state="fresh"', self.html)
        self.assertIn("Up to date", self.html)
        self.assertNotIn("staleNote", self.html)
        self.assertNotIn('id="buildBtn"', self.html)
        self.assertIn(">Rebuild review<", self.html)
        self.assertIn('id="createdAt"', self.html)
        self.assertIn(f'datetime="{self.DOC["created_at"]}"', self.html)

    def test_prose_shown_next_to_the_numbers(self):
        self.assertIn('data-prose="focus"', self.html)
        self.assertIn("focus prose.", self.html)

    def test_doc_index_and_rebuild_wiring(self):
        self.assertIn("data-doc-index", self.html)
        self.assertIn('id="rebuildBtn"', self.html)
        self.assertIn("/rebuild", self.html)
        self.assertIn('id="job"', self.html)


class TestStalePage(_ReviewPage, unittest.TestCase):
    DOC = _doc(stale=True, stale_reason="decklist changed since this review was built")

    def test_stale_badge_and_reason(self):
        self.assertIn('data-state="stale"', self.html)
        self.assertRegex(self.html, r'<span class="hud-chip hud-chip--signal" title="decklist changed since this review was built">'
                                    r'<span class="hud-chip-dot" aria-hidden="true"></span>Stale</span>')
        self.assertNotIn("Up to date", self.html)
        self.assertIn('id="staleNote"', self.html)
        self.assertIn("decklist changed since this review was built", self.html.split('id="staleNote"', 1)[1].split("</div>", 1)[0])

    def test_stale_review_still_renders_sections(self):
        self.assertIn('data-type="focus"', self.html)
        self.assertIn(">Rebuild review<", self.html)


class TestOfflinePage(_ReviewPage, unittest.TestCase):
    DOC = _doc(prose=False, status="offline")

    def test_offline_note_and_math_still_render(self):
        self.assertIn("Explanation offline", self.html)
        self.assertIn("press Rebuild to retry", self.html)
        self.assertIn('data-type="stragglers"', self.html)


class TestUnknownSectionOnPage(_ReviewPage, unittest.TestCase):
    DOC = (lambda d: (d["sections"].insert(2, {"type": "forge_sim", "title": "Forge sims", "data": "## Dead draws\n\n- Foo"}), d)[1])(_doc())

    def test_unknown_section_renders_via_fallback(self):
        self.assertIn('data-type="forge_sim"', self.html)
        self.assertIn("Forge sims", self.html)
        self.assertIn('data-fallback="markdown"', self.html)
        self.assertIn("Dead draws", self.html)


class TestEmptyPage(_ReviewPage, unittest.TestCase):
    def test_empty_state_offers_build(self):
        self.assertIn('data-state="empty"', self.html)
        self.assertIn("No review yet", self.html)
        self.assertIn('id="buildBtn"', self.html)
        self.assertIn(">Build review<", self.html)
        doc = self.html.split('id="doc"', 1)[1].split("</article>", 1)[0]
        self.assertNotIn("hud-doc-head", doc)
        self.assertNotIn("data-type=", doc)


class TestStorageDown(unittest.TestCase):
    def test_redis_error_gives_quiet_state_not_a_500(self):
        class Boom(FakeRedis):
            def get(self, k):
                if str(k).startswith("review:"):
                    raise ConnectionError("down")
                return super().get(k)

        with patch.object(app_mod, "r", Boom()), \
                patch("app.registry.list_decks", return_value=[DECK]), \
                patch("app.inventory_view.build_inventory", return_value=[]):
            from fastapi.testclient import TestClient
            res = TestClient(app_mod.app).get("/deck/Holy%20Hellfire/review")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Couldn&#39;t load the review", res.text)

    def test_unknown_deck_404(self):
        from fastapi.testclient import TestClient
        with patch("app.registry.list_decks", return_value=[]):
            self.assertEqual(TestClient(app_mod.app).get("/deck/Nope/review").status_code, 404)


class TestHotkeys(unittest.TestCase):
    def test_seven_key_switches_tab(self):
        js = (ROOT / "static" / "js" / "deck_analytics.js").read_text()
        self.assertIn("/^[1-7]$/", js)


if __name__ == "__main__":
    unittest.main()
