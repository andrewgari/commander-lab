"""TH-3: Ideas tab + theory doc view (docs/THEORY_SPEC.md).

- analytics.theory_view: the generic fallback renderer + section → partial routing;
- every partial (and the fallback) renders through Jinja for good and bad data;
- /deck/{name}/ideas lists docs (theme, date, commander kept/swapped, buy total, open + delete)
  and has the New theory form (theme + Keep/Open seg) with job polling;
- /deck/{name}/ideas/{doc_id} renders one partial per known section, the fallback for
  unknown types (oracle's `notes`, future types), and 404s cleanly;
- the 6th deck tab (Ideas, key 6) with a doc-count badge;
- oracle offline → "oracle offline, try again later", never a stack trace.
Fixture doc: tests/fixtures/theory_doc_holy_hellfire.json (the real TH-2 prod doc, read-only copy).
"""
import copy
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
import src.server.domain.decks.card_refs as card_refs
import src.server.infra.providers.tcgplayer as tcgplayer
from jinja2 import Environment, FileSystemLoader

import src.server.app.main as app_mod
from src.server.domain.analytics import theory, theory_view
from src.server.domain.analytics.oracle_client import OracleClient
from tests.test_deck_stats_ui import DECK, HUD_CSS, INVENTORY, _classes, _DeckPage, _page_style
from tests.test_theory import FakeRedis

ROOT = Path(__file__).resolve().parent.parent
DOC = json.loads((ROOT / "tests" / "fixtures" / "theory_doc_holy_hellfire.json").read_text())
RID = DECK["registry_id"]


def _doc(**over):
    d = copy.deepcopy(DOC)
    d.update(over)
    return d


def _env():
    env = Environment(loader=FileSystemLoader(str(ROOT / "templates")), autoescape=True)
    env.globals["card_preview_attrs"] = card_refs.preview_attrs
    env.globals["tcgplayer_cart"] = tcgplayer.cart
    return env


def render_section(section):
    """Render one section through the same routing the page uses."""
    tpl = _env().get_template(theory_view.section_template(section))
    return tpl.render(section=section, tv=theory_view)


class _Fake(FakeRedis):
    def zcard(self, key):
        return len(self.zsets.get(key, {}))


# --- fallback renderer -----------------------------------------------------------------


class TestFallbackView(unittest.TestCase):
    def test_flat_dict_is_key_value(self):
        notes = DOC["sections"][4]
        self.assertEqual(notes["type"], "notes")
        fv = theory_view.fallback_view(notes["data"])
        self.assertEqual(fv["kind"], "kv")
        self.assertEqual(fv["pairs"][0], ("Dragon count", "13"))

    def test_string_is_paragraphs(self):
        fv = theory_view.fallback_view("one\n\ntwo\n")
        self.assertEqual(fv, {"kind": "text", "paragraphs": ["one", "two"]})

    def test_scalar_list(self):
        self.assertEqual(theory_view.fallback_view(["a", 2, None])["items"], ["a", "2", "—"])

    def test_nested_is_pretty_json(self):
        fv = theory_view.fallback_view({"a": {"b": [1, 2]}})
        self.assertEqual(fv["kind"], "json")
        self.assertEqual(json.loads(fv["text"]), {"a": {"b": [1, 2]}})

    def test_empty(self):
        for v in (None, "", {}, []):
            self.assertEqual(theory_view.fallback_view(v)["kind"], "empty")

    def test_bool_and_number(self):
        self.assertEqual(theory_view.fallback_view({"x": True, "y": 1.5})["pairs"], [("X", "yes"), ("Y", "1.5")])
        self.assertEqual(theory_view.fallback_view(7)["paragraphs"], ["7"])


class TestSectionRouting(unittest.TestCase):
    def test_known_types_get_their_partial(self):
        for s in DOC["sections"][:4]:
            with self.subTest(type=s["type"]):
                self.assertEqual(theory_view.section_template(s), f"partials/theory/{s['type']}.html")
                self.assertTrue((ROOT / "templates" / theory_view.section_template(s)).is_file())

    def test_every_renderer_has_a_partial(self):
        for t in theory_view.RENDERERS:
            self.assertTrue((ROOT / "templates" / "partials" / "theory" / f"{t}.html").is_file(), t)
        self.assertEqual(set(theory_view.RENDERERS), set(theory.KNOWN_TYPES))

    def test_unknown_type_falls_back(self):
        self.assertEqual(theory_view.section_template({"type": "notes", "data": {}}), theory_view.FALLBACK)
        self.assertEqual(theory_view.section_template({"type": "mana_curve_v2", "data": [1, 2]}), theory_view.FALLBACK)

    def test_known_type_with_bad_shape_falls_back(self):
        for s in ({"type": "decklist", "data": "oops"}, {"type": "decklist", "data": {"groups": "x"}},
                  {"type": "buy_total", "data": {"total_usd": "lots"}}, {"type": "thesis_pillars", "data": None},
                  {"type": "cuts", "data": {"cards": 3}}):
            with self.subTest(s=s):
                self.assertEqual(theory_view.section_template(s), theory_view.FALLBACK)

    def test_sections_skip_junk(self):
        out = theory_view.sections({"sections": [DOC["sections"][0], "junk", None]})
        self.assertEqual([s["section"]["type"] for s in out], ["thesis_pillars"])


class TestFallbackPartial(unittest.TestCase):
    def test_unknown_type_renders_title_data_not_raises(self):
        html = render_section({"type": "future_thing", "title": "Future", "data": {"deep": {"x": [1, "<b>"]}}})
        self.assertIn('data-fallback="json"', html)
        self.assertIn("<pre", html)
        self.assertIn("&lt;b&gt;", html)   # escaped
        self.assertNotIn("<b>", html)

    def test_notes_render_as_kv(self):
        html = render_section(DOC["sections"][4])
        self.assertIn('data-fallback="kv"', html)
        self.assertIn("<dt>Dragon count</dt><dd>13</dd>", html)

    def test_text_and_list_and_empty(self):
        self.assertIn("<p class=\"hud-doc-prose\">hello</p>", render_section({"type": "x", "data": "hello"}))
        self.assertIn("<li>a</li>", render_section({"type": "x", "data": ["a"]}))
        self.assertIn("No content", render_section({"type": "x", "data": None}))

    def test_bad_known_section_renders_via_fallback(self):
        html = render_section({"type": "decklist", "title": "Decklist", "data": "not a list"})
        self.assertIn('data-fallback="text"', html)


class TestKnownPartials(unittest.TestCase):
    def test_decklist_groups_badges_prices(self):
        html = render_section(DOC["sections"][1])
        roles = re.findall(r'<header class="hud-tgroup-head"><h3>([^<]+)</h3><span class="hud-freshness">(\d+)</span>', html)
        self.assertEqual(roles[0], ("Commander", "1"))
        self.assertEqual(sum(int(n) for _, n in roles), 100)
        self.assertEqual(html.count('class="hud-tcard is-buy"'), 8)
        rows = [c for g in DOC["sections"][1]["data"]["groups"] for c in g["cards"]]
        self.assertEqual(html.count('class="hud-tcard is-kept"'), sum(c["source"] == "kept" for c in rows))
        self.assertEqual(sum(int(c.get("qty") or 1) for c in rows if c["source"] == "kept"), 92)
        self.assertIn("$29.98", html)              # Terror of the Peaks, Scryfall
        self.assertIn("Salt · medium", html)
        self.assertIn("92 kept", html)

    def test_cuts_strike_and_reason(self):
        html = render_section(DOC["sections"][2])
        self.assertRegex(html, r'<s class="hud-card-name hud-cp-name"[^>]*data-card-preview[^>]*>Angelic Arbiter</s>')
        self.assertIn("symmetric lock", html)
        html = render_section({"type": "cuts", "data": {"cards": [{"name": "Kaalia of the Vast", "commander": True},
                                                                  {"name": "Pet", "pet": True}]}})
        self.assertIn("commander", html)
        self.assertIn("♥ pet", html)

    def test_buy_total(self):
        html = render_section(DOC["sections"][3])
        self.assertIn("$46.08", html)
        self.assertIn("Per-card cap", html)          # TH-3R F9: not "Caps / card"
        self.assertIn("hud-stat--quiet", html)
        self.assertEqual(html.count('class="hud-tcard is-buy"'), 8)

    def test_th3r_salt_and_zero_counts(self):
        html = render_section({"type": "decklist", "data": {
            "counts": {"kept": 99, "owned": 0, "buy": 1, "total": 100},
            "groups": [{"role": "Tutors", "cards": [
                {"name": "Demonic Tutor", "source": "kept", "price": 60.0,
                 "salt_flag": {"level": "high", "reason": "game_changer: tutor; salt: unverified [scryfall oracle]"}}]}]}})
        # Salt boilerplate goes to the tooltip; the visible note keeps the reason.
        self.assertIn("<b>Salt · high · unverified</b> game_changer: tutor</span>", html)
        # A non-string reason (free-form extra data) must not 500 the page.
        odd = render_section({"type": "decklist", "data": {"groups": [{"role": "X", "cards": [
            {"name": "A", "source": "kept", "price": 1.0, "salt_flag": {"level": "low", "reason": 7}},
            {"name": "B", "source": "kept", "price": 1.0, "salt_flag": {"level": "low", "reason": None}}]}]}})
        self.assertIn("<b>Salt · low</b> 7</span>", odd)
        self.assertIn('title="game_changer: tutor; salt: unverified [scryfall oracle]"', html)
        # A zero count is neutral, not green.
        self.assertIn('hud-chip--neutral">0 owned', html)
        self.assertIn('hud-chip--signal">1 buy', html)

    def test_th3f_salt_level_class_and_tone(self):
        cards = [{"name": n, "source": "kept", "price": 1.0,
                  "salt_flag": {"level": lv, "reason": "stax; salt: unverified [scryfall oracle]"}}
                 for n, lv in (("A", "high"), ("B", "medium"), ("C", "low"), ("D", "<bogus>"))]
        html = render_section({"type": "decklist", "data": {"groups": [{"role": "X", "cards": cards}]}})
        notes = re.findall(r'<span class="(hud-tcard-note is-salt[^"]*)"[^>]*><b>([^<]+)</b>', html)
        self.assertEqual([c for c, _ in notes], ["hud-tcard-note is-salt is-salt-high", "hud-tcard-note is-salt is-salt-medium",
                                                 "hud-tcard-note is-salt is-salt-low", "hud-tcard-note is-salt"])
        # "unverified" stays visible at every level (a11y).
        self.assertTrue(all(lbl.endswith("· unverified") for _, lbl in notes), notes)
        css = HUD_CSS.read_text()
        # Base label is --danger (so high keeps it); medium + low are overridden to --text-3.
        self.assertRegex(css, r"\.hud-tcard-note\.is-salt b \{ color: var\(--danger\)")
        self.assertRegex(css, r"\.hud-tcard-note\.is-salt-medium b,\s*\.hud-tcard-note\.is-salt-low b \{ color: var\(--text-3\); \}")
        self.assertNotRegex(css, r"\.is-salt-high[^{]*\{[^}]*--text-3")

    def test_th3r_hidden_buttons_stay_hidden(self):
        css = (ROOT / "static" / "css" / "hud.css").read_text()
        self.assertIn(".hud-btn[hidden] { display: none; }", css)

    def test_commander_change_callout(self):
        html = render_section({"type": "commander_change", "title": "Commander change", "data": {
            "old_commanders": ["Kaalia of the Vast"], "new_commanders": ["Lathliss, Dragon Queen"],
            "reason": "Dragons want a red dragon lord", "reuse_count": 71, "pets_lost": ["Lyra Dawnbringer"]}})
        self.assertIn("hud-callout", html)
        self.assertIn("<s>Kaalia of the Vast</s> → Lathliss, Dragon Queen", html)
        self.assertIn("<b>71</b>", html)
        self.assertIn("Lyra Dawnbringer", html)

    def test_thesis_pillars(self):
        html = render_section(DOC["sections"][0])
        self.assertIn("<blockquote>Kaalia drops a Dragon air force", html)
        self.assertEqual(html.count("<li><span class=\"hud-label\">Pillar"), 4)

    def test_every_hud_class_in_partials_is_defined(self):
        css = HUD_CSS.read_text()
        defined = set(re.findall(r"\.(hud-[a-z0-9-]+)", css))
        for f in (ROOT / "templates" / "partials" / "theory").glob("*.html"):
            used = set(re.findall(r"(?<![-\w])(hud-[a-z0-9-]+)", f.read_text()))
            with self.subTest(f=f.name):
                self.assertEqual(sorted(used - defined), [])
            self.assertNotIn('style="', f.read_text(), f.name)


class TestHelpers(unittest.TestCase):
    def test_money_and_when(self):
        self.assertEqual(theory_view.money(46.08), "$46.08")
        self.assertEqual(theory_view.money(None), "—")
        self.assertEqual(theory_view.when("2026-10-12T22:10:49+00:00"), "2026-10-12 22:10 UTC")
        self.assertEqual(theory_view.when("garbage"), "garbage")

    def test_section_title_and_doc_index(self):
        items = theory_view.sections({"sections": [{"type": "decklist", "title": " Decklist "}, "junk",
                                                   {"type": "buy_total"}, {"type": "x", "title": 7}]})
        self.assertEqual([i["title"] for i in items], ["Decklist", "Buy total", "X"])
        self.assertEqual(theory_view.doc_index(items), [
            {"href": "#sec1", "code": "01", "label": "Decklist"}, {"href": "#sec2", "code": "02", "label": "Buy total"},
            {"href": "#sec3", "code": "03", "label": "X"}])

    def test_commander_line(self):
        self.assertEqual(theory_view.commander_line({"commander_changed": False}), "Kept")
        self.assertEqual(theory_view.commander_line({"commander_changed": True, "commanders": ["A", "B"]}), "Swapped → A + B")


# --- pages -----------------------------------------------------------------------------


class _IdeasPage(_DeckPage):
    PATH = "/deck/Holy%20Hellfire/ideas"
    ACTIVE = "Ideas"
    DOCS = (DOC,)

    @classmethod
    def setUpClass(cls):
        cls.fake = _Fake()
        for d in cls.DOCS:
            theory.store(cls.fake, copy.deepcopy(d))
        with patch.object(app_mod, "r", cls.fake):
            super().setUpClass()

    def test_tabs_have_keys_and_mark_the_active_tab(self):
        nav = self.body.split('class="hud-tabs hud-deck-tabs"', 1)[1].split("</nav>", 1)[0]
        tabs = re.findall(r'<a class="hud-tab[^"]*" href="([^"]+)"[^>]*data-key="(\d)"', nav)
        self.assertEqual([k for _, k in tabs], ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"])
        self.assertEqual(tabs[7][0], "/deck/Holy%20Hellfire/ideas")
        active = re.findall(r'<a class="hud-tab active"[^>]*aria-current="page"[^>]*><span class="hud-kbd"[^>]*>[^<]*</span>([^<]+)', nav)
        self.assertEqual([a.strip() for a in active], [self.ACTIVE])


class TestIdeasPage(_IdeasPage, unittest.TestCase):
    DOCS = (DOC, _doc(doc_id="b2", theme="angels", created_at="2026-10-13T01:00:00+00:00",
                      verification={**DOC["verification"], "commander_changed": True, "commanders": ["Lyra Dawnbringer"],
                                    "buy_total_usd": 12.5}))

    def test_badge_is_doc_count(self):
        nav = self.body.split('class="hud-tabs hud-deck-tabs"', 1)[1].split("</nav>", 1)[0]
        self.assertRegex(nav, r'/ideas"[^>]*><span class="hud-kbd"[^>]*>8</span>Ideas <span class="hud-tab-count">2</span>')

    def test_list_newest_first_with_facts_and_actions(self):
        rows = re.findall(r'<li class="hud-idea" data-doc="([^"]+)">', self.body)
        self.assertEqual(rows, ["b2", "a49befd418ad"])
        first = self.body.split('data-doc="b2"', 1)[1].split("</li>", 1)[0]
        self.assertIn(">angels</a>", first)
        self.assertIn("Swapped → Lyra Dawnbringer", first)
        self.assertIn("$12.50", first)
        self.assertIn('datetime="2026-10-13T01:00:00+00:00"', first)
        self.assertIn('href="/deck/Holy%20Hellfire/ideas/b2"', first)
        self.assertIn("▶ Open", first)
        self.assertIn('data-delete="b2"', first)
        second = self.body.split('data-doc="a49befd418ad"', 1)[1].split("</li>", 1)[0]
        self.assertIn("Commander kept", second)
        self.assertIn("$46.08", second)
        self.assertIn("92 kept · 0 owned · 8 buy", second)

    def test_new_theory_form(self):
        self.assertRegex(self.body, r'<input id="theme" name="theme" maxlength="200"')
        radios = re.findall(r'<input type="radio" name="commander_mode" value="(\w+)" id="cm-\w+"( checked)?>', self.body)
        self.assertEqual([v for v, _ in radios], ["keep", "open"])
        self.assertEqual([v for v, c in radios if c], ["keep"])
        self.assertIn(">Open to swap</label>", self.body)

    def test_job_ui_polls_resumes_and_handles_offline(self):
        self.assertIn('id="job"', self.body)
        self.assertIn("/jobs/", self.body)
        self.assertIn("localStorage", self.body)                         # leave the page, come back
        self.assertIn("oracle offline, try again later", self.body)
        self.assertIn("STALE_MS", self.body)                             # stale 'running' = failed
        self.assertIn("You can leave this page", self.body)
        self.assertNotIn("Traceback", self.body)

    def test_kit_classes(self):
        for c in ["hud-ideas", "hud-idea", "hud-idea-list", "hud-idea-buy", "hud-job", "hud-seg", "hud-field", "hud-panel"]:
            self.assertIn(c, _classes(self.html), c)

    def _script(self):
        return self.body.rsplit("<script>", 1)[1].split("</script>", 1)[0]

    def test_th3f_form_locks_and_echoes_the_inflight_job(self):
        # The lock note ships hidden; lock(j) shows it, echoes theme/mode, makes inputs read-only.
        self.assertRegex(self.body, r'<p class="hud-form-lock" id="formLock" hidden>Locked while')
        js = self._script()
        lock = js.split("function lock(j) {", 1)[1].split("\n    }\n", 1)[0]
        self.assertIn('$("theme").readOnly = on', lock)
        self.assertIn('$("theme").value = j.theme', lock)
        self.assertIn('r.checked = r.value === (j.mode === "open" ? "open" : "keep")', lock)
        self.assertIn("r.disabled = on", lock)
        self.assertIn('$("formLock").hidden = !on', lock)
        # Every job render locks while queued/running and frees on done/failed; resume goes through showJob.
        show = js.split("function showJob(state, j, msg) {", 1)[1].split("\n    }\n", 1)[0]
        self.assertIn('lock(state === "queued" || state === "running" ? j : null)', show)
        self.assertIn('if (pending && pending.job_id) { busy(true); showJob("running", pending); poll(pending); }', js)
        for c in ["hud-form-lock", "is-locked"]:
            self.assertIn("." + c, self.css, c)

    def test_th3f_new_theory_button_scrolls_then_focuses(self):
        self.assertIn('href="#theme" id="newBtn">New theory</a>', self.body)
        js = self._script()
        handler = js.split('$("newBtn").addEventListener("click", function (e) {', 1)[1].split("\n    });", 1)[0]
        scroll = handler.index('$("newTheory").scrollIntoView({ block: "center" })')
        focus = handler.index('$("theme").focus({ preventScroll: true })')
        self.assertLess(scroll, focus)
        self.assertIn('id="newTheory"', self.body)

    def test_th3f_relative_time_with_absolute_title(self):
        first = self.body.split('data-doc="b2"', 1)[1].split("</li>", 1)[0]
        self.assertIn('<time class="hud-freshness" datetime="2026-10-13T01:00:00+00:00" data-rel title="2026-10-13 01:00 UTC">', first)
        js = self._script()
        self.assertIn("A.age(t.getAttribute(\"datetime\"))", js)
        self.assertIn("t.title = abs", js)
        self.assertIn("age: age", (ROOT / "static" / "js" / "deck_analytics.js").read_text())


class TestIdeasEmpty(_IdeasPage, unittest.TestCase):
    DOCS = ()

    def test_empty_state_and_no_badge(self):
        self.assertIn("No theories yet", self.body)
        nav = self.body.split('class="hud-tabs hud-deck-tabs"', 1)[1].split("</nav>", 1)[0]
        self.assertNotRegex(nav, r'/ideas"[^>]*>Ideas <span')


class TestDocPage(_IdeasPage, unittest.TestCase):
    PATH = "/deck/Holy%20Hellfire/ideas/a49befd418ad"
    DOCS = (_doc(deck_id=RID),)

    def test_sections_in_doc_order_with_fallback_for_notes(self):
        types = re.findall(r'<section class="hud-doc-section" data-type="([^"]+)"', self.body)
        self.assertEqual(types, ["thesis_pillars", "decklist", "cuts", "buy_total", "notes"])
        notes = self.body.split('data-type="notes"', 1)[1]
        self.assertIn(">Notes</h2>", notes)
        self.assertIn('data-fallback="kv"', notes)

    def test_header_facts_and_delete(self):
        self.assertIn("<h2>dragons</h2>", self.body)
        self.assertIn("Commander kept", self.body)
        self.assertIn("$46.08", self.body)
        self.assertIn('id="deleteBtn"', self.body)
        self.assertIn("5 corrections to oracle", self.body)
        self.assertIn('href="/deck/Holy%20Hellfire/ideas"', self.body)

    def test_page_css_is_layout_only_and_classes_defined(self):
        self.assertNotRegex(_page_style(self.html), r"#[0-9a-fA-F]{3,8}\b")

    def test_th3f_sticky_doc_index_links_every_section(self):
        nav = self.body.split('<nav class="hud-doc-index"', 1)[1].split("</nav>", 1)[0]
        links = re.findall(r'<a href="(#sec\d+)"><span class="hud-section-id">(\d\d)</span>([^<]+)</a>', nav)
        self.assertEqual(links, [("#sec1", "01", "Thesis"), ("#sec2", "02", "Decklist"), ("#sec3", "03", "Cuts"),
                                 ("#sec4", "04", "Buy total"), ("#sec5", "05", "Notes")])
        for href, _, label in links:
            self.assertIn(f'<h2 id="{href[1:]}">{label}</h2>', self.body)
        # Between the doc head and the first section.
        self.assertLess(self.body.index("hud-doc-index"), self.body.index('<section class="hud-doc-section"'))
        self.assertIn("data-doc-index", nav)
        self.assertRegex(self.css, r"\.hud-doc-index \{\s*position: sticky;")
        self.assertIn("initDocIndex", (ROOT / "static" / "js" / "hud.js").read_text())
        # Copilot (PR #86): a short final section must still become current at the page end.
        self.assertIn("cur = heads.length - 1", (ROOT / "static" / "js" / "hud.js").read_text())


class TestDocPageSwapped(_IdeasPage, unittest.TestCase):
    PATH = "/deck/Holy%20Hellfire/ideas/cc1"
    DOCS = (_doc(deck_id=RID, doc_id="cc1", commander_mode="open",
                 verification={**DOC["verification"], "commander_changed": True, "commanders": ["Lathliss, Dragon Queen"]},
                 sections=DOC["sections"][:4] + [
                     {"type": "commander_change", "title": "Commander change", "data": {
                         "old_commanders": ["Kaalia of the Vast"], "new_commanders": ["Lathliss, Dragon Queen"],
                         "reason": "r", "reuse_count": 70, "pets_lost": []}},
                     {"type": "brand_new_type", "title": "Brand new", "data": {"x": {"y": 1}}}]),)

    def test_callout_and_unknown_type(self):
        self.assertIn("Commander swapped → Lathliss, Dragon Queen", self.body)
        self.assertIn("hud-callout", self.body)
        unk = self.body.split('data-type="brand_new_type"', 1)[1]
        self.assertIn(">Brand new</h2>", unk)
        self.assertIn('data-fallback="json"', unk)


class TestPageEdges(unittest.TestCase):
    def setUp(self):
        self.fake = _Fake()
        self.client = TestClient(app_mod.app)
        for p in (patch.object(app_mod, "r", self.fake), patch("src.server.app.main.registry.list_decks", return_value=[DECK]),
                  patch("src.server.app.main.registry.find_deck", lambda r, i: DECK if i in (RID, str(DECK["id"])) else None),
                  patch("src.server.app.main.inventory_view.build_inventory", return_value=INVENTORY)):
            p.start()
            self.addCleanup(p.stop)

    def test_unknown_doc_404(self):
        res = self.client.get("/deck/Holy%20Hellfire/ideas/nope")
        self.assertEqual(res.status_code, 404)
        self.assertIn("Theory Not Found", res.text)
        self.assertIn("/deck/Holy%20Hellfire/ideas", res.text)

    def test_unknown_deck_404(self):
        self.assertEqual(self.client.get("/deck/Nope/ideas").status_code, 404)
        self.assertEqual(self.client.get("/deck/Nope/ideas/x").status_code, 404)

    def test_doc_with_junk_sections_still_renders(self):
        theory.store(self.fake, _doc(deck_id=RID, doc_id="junk", sections=[
            {"type": "decklist", "title": "Decklist", "data": "broken"}, "junk", {"type": "z"}], verification=None))
        res = self.client.get("/deck/Holy%20Hellfire/ideas/junk")
        self.assertEqual(res.status_code, 200, res.text[:500])
        self.assertIn('data-fallback="text"', res.text)

    def test_redis_down_ideas_page_still_renders(self):
        import redis as redis_lib

        class Down:
            def __getattr__(self, name):
                def boom(*a, **k):
                    raise redis_lib.exceptions.ConnectionError("redis down")
                return boom
        with patch.object(app_mod, "r", Down()):
            res = self.client.get("/deck/Holy%20Hellfire/ideas")
        self.assertEqual(res.status_code, 200)
        self.assertIn("load theories. Try again later", res.text)
        self.assertNotIn("Traceback", res.text)

    def test_doc_page_storage_down_is_503_not_404(self):
        theory.store(self.fake, _doc(deck_id=RID))
        with patch.object(theory, "load", side_effect=ConnectionError("redis down")):
            res = self.client.get("/deck/Holy%20Hellfire/ideas/a49befd418ad")
        self.assertEqual(res.status_code, 503)
        self.assertIn("Try again", res.text)
        with patch("src.server.app.main.registry.list_decks", side_effect=ConnectionError("redis down")):
            self.assertEqual(self.client.get("/deck/Holy%20Hellfire/ideas/a49befd418ad").status_code, 503)

    def test_delete_updates_tab_badge_and_409_syncs_job_fields(self):
        theory.store(self.fake, _doc(deck_id=RID))
        body = self.client.get("/deck/Holy%20Hellfire/ideas").text
        self.assertIn('[data-key="8"]', body)
        self.assertIn("hud-tab-count", body)
        self.assertIn("job.commander_mode", body)

    def test_api_delete_then_list(self):
        theory.store(self.fake, _doc(deck_id=RID))
        self.assertEqual(len(self.client.get(f"/api/decks/{RID}/theory").json()["docs"]), 1)
        self.assertEqual(self.client.delete(f"/api/decks/{RID}/theory/a49befd418ad").status_code, 200)
        self.assertIn("No theories yet", self.client.get("/deck/Holy%20Hellfire/ideas").text)

    def test_generate_oracle_offline_is_the_friendly_503(self):
        def refused(request):
            raise httpx.ConnectError("refused")
        down = OracleClient("http://oracle.test", "k", transport=httpx.MockTransport(refused))
        with patch.object(app_mod, "_oracle_client", lambda: down):
            res = self.client.post(f"/api/decks/{RID}/theory", json={"theme": "dragons", "commander_mode": "keep"})
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["error"], "oracle offline, try again later")
        self.assertEqual(res.json()["error_kind"], "oracle_offline")
        self.assertNotIn("Traceback", res.text)


class TestTabHotkey(unittest.TestCase):
    def test_key_6(self):
        js = (ROOT / "static" / "js" / "deck_analytics.js").read_text()
        self.assertIn("/^[0-9]$/", js)


if __name__ == "__main__":
    unittest.main()
