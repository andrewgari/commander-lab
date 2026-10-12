"""AN-6: Tune tab UI + API (UX_SPEC.md §3.4, §4, §5).

Structure and behaviour only, same rules as the AN-4/AN-5 page tests:
- /deck/{name}/tune renders the shared deck header with all five tabs and
  Tune (key 5) active; every hud-* class is defined; page CSS is layout-only;
- filter bar (source seg + sleeved toggle + caps freshness), the three
  sections in spec order, the sticky change list (Copy as text only: there
  is no Archidekt write yet, so Apply is hidden, not disabled), the keys panel;
- the check-in's Finish goes to Tune (D4); Stats links into Tune (§3.1.6, §3.1.10);
- the API routes (tune, decisions, adds, pet, why) on a fake Redis;
- the Tune view-model helpers in deck_analytics.js run in node.
"""
import json
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_mod
from analytics import journal, tuner
from app import app
from tests.test_deck_stats_ui import DECK, DECK_JS, HUD_JS, _DeckPage, _classes
from tests.test_interactions import FakeRedis
from tests.test_tuner import COMP, WEAK, cand

TEMPLATE = app_mod.os.path.join(app_mod.os.path.dirname(app_mod.__file__), "templates", "deck_tune.html")


class _TunePage(_DeckPage):
    PATH = "/deck/Holy%20Hellfire/tune"
    ACTIVE = "Tune"

    @classmethod
    def setUpClass(cls):
        cls.fake = FakeRedis()
        cls.fake.set(tuner.COUNT_PREFIX + "archidekt:417707", "9")
        with patch.object(app_mod, "r", cls.fake):
            super().setUpClass()

    def test_tabs_have_keys_and_mark_the_active_tab(self):
        nav = self.body.split('class="hud-tabs hud-deck-tabs"', 1)[1].split("</nav>", 1)[0]
        tabs = re.findall(r'<a class="hud-tab[^"]*" href="([^"]+)"[^>]*data-key="(\d)"', nav)
        self.assertEqual([k for _, k in tabs], ["1", "2", "3", "4", "5"])
        self.assertEqual(tabs[4][0], "/deck/Holy%20Hellfire/tune")
        active = re.findall(r'<a class="hud-tab active"[^>]*aria-current="page"[^>]*>([^<]+)', nav)
        self.assertEqual([a.strip() for a in active], [self.ACTIVE])

    def test_tune_badge_is_the_open_count(self):
        nav = self.body.split('class="hud-tabs hud-deck-tabs"', 1)[1].split("</nav>", 1)[0]
        self.assertRegex(nav, r'/tune"[^>]*>Tune <span class="hud-tab-count">9</span>')


class TestTunePage(_TunePage, unittest.TestCase):
    def test_kit_structure(self):
        for cls_ in ["hud-filterbar", "hud-seg", "hud-toggle", "hud-tune", "hud-swaps", "hud-changes", "hud-caps",
                     "hud-kbd", "hud-table", "hud-panel-foot", "hud-section-head"]:
            with self.subTest(cls=cls_):
                self.assertIn(cls_, _classes(self.html))
        # Rendered by JS: the swap/fix/option classes must exist in the script.
        for cls_ in ["hud-swap", "hud-swap-out", "hud-swap-arrow", "hud-opts", "hud-opt", "is-pick", "hud-why",
                     "hud-gapline", "hud-justify", "hud-swap-act", "hud-fix", "hud-fix-gap", "is-accepted",
                     "is-dismissed", "is-current"]:
            with self.subTest(js_cls=cls_):
                self.assertIn(cls_, self.body)

    def test_source_seg_is_native_radios_owned_first_default(self):
        radios = re.findall(r'<input type="radio" name="src" id="src-(\w+)" value="(\w+)"( checked)?>', self.body)
        self.assertEqual([v for _, v, _ in radios], ["owned_first", "owned_only", "best"])
        self.assertEqual([v for _, v, c in radios if c], ["owned_first"])
        self.assertRegex(self.body, r'<input type="checkbox" id="sleeved" checked>')

    def test_sections_in_spec_order(self):
        marks = ['id="fixT"', 'id="swT"', 'id="wkT"', 'aria-label="Pending changes"', ">Keys</h3>"]
        idx = [self.body.find(m) for m in marks]
        self.assertTrue(all(i > 0 for i in idx), idx)
        self.assertEqual(idx, sorted(idx))
        self.assertIn('id="weak"', self.body)   # Stats links to tune#weak

    def test_weak_table_columns_and_foot(self):
        head = self.body.split('id="weakRows"', 1)[0].rsplit("<thead>", 1)[1]
        self.assertEqual(re.findall(r'class="hud-sort">([^<]+)<', head), ["Card", "Edges", "Pillar fit", "EDHREC", "Weak score"])
        self.assertIn("Weak = few edges + off-pillar + low EDHREC, equally weighted. No game logs.", self.body)
        self.assertIn("tn-col-opt", self.style)   # hidden at <= 640px

    def test_change_list_copy_only_apply_hidden(self):
        self.assertIn('id="copyText"', self.body)
        self.assertNotIn("Apply to Archidekt", self.body)   # §3.4: hide it, don't show it disabled
        self.assertIn("Physical moves appear in Manage", self.body)
        for k in ["j", "k", "a", "b", "x", "h"]:
            with self.subTest(key=k):
                self.assertIn('<span class="hud-kbd">' + k + "</span>", self.body)

    def test_swaps_are_live_and_keys_ignore_text_fields(self):
        self.assertRegex(self.body, r'<ul class="hud-swaps[^"]*" id="swaps" aria-live="polite"')
        self.assertIn("A.inTextField(e.target)", self.body)
        self.assertNotIn("innerHTML = s.cut.name", self.body)

    def test_hard_cap_is_danger_and_soft_is_signal(self):
        self.assertIn('H.chip("over hard cap " + A.money(T.caps.hard_cap), "danger")', self.body)
        self.assertIn('"over soft cap " + A.money(T.caps.soft_cap), "signal"', self.body)

    def test_cut_shows_its_own_score_so_the_gap_reads(self):
        # Fit is one of three inputs; without the cut's score a 0.56 add next to "Fit 0.61" looks like a downgrade.
        self.assertIn("s.cut.score.toFixed(2)", self.body)


class TestCheckinFinishLandsOnTune(unittest.TestCase):
    def test_finish_button_label(self):
        with open(TEMPLATE.replace("deck_tune.html", "deck_journal.html"), encoding="utf-8") as fh:
            self.assertIn("Finish → Tune", fh.read())

    def test_stats_links_into_tune(self):
        with open(TEMPLATE.replace("deck_tune.html", "deck_stats.html"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("/tune#weak\">See in Tune →</a>", src)
        self.assertIn('"/tune?add=" + encodeURIComponent(miss)', src)


def _fake_compute(r, deck, mode="owned_first", sleeved=True, **kw):
    cands = [cand("Owned Ok", 4, 0.75, 0.35, owned=True, available=1),
             cand("Soft Buy", 4, 0.9, 0.45, price=18.50),
             cand("Pricey Bomb", 4, 0.99, 0.5, price=95.0)]
    out = tuner.build_tune(deck, WEAK, COMP, cands, tuner.caps_for(r, deck), p75=4.0, has_pillars=True,
                           has_edhrec=True, mode=mode, sleeved=sleeved, state=tuner.load_state(r, deck))
    out["on_plan"] = {"pct": 70, "on": 70, "total": 100}
    return out


class TestTuneAPI(unittest.TestCase):
    def setUp(self):
        self.fake = FakeRedis()
        self.client = TestClient(app)
        deck = dict(DECK, cards=[{"name": n, "quantity": 1} for n in ("Kaalia of the Vast", "Weak Plan", "Weak Removal",
                                                                       "Pet Card", "Okay Plan")])
        self.patches = [patch.object(app_mod, "r", self.fake),
                        patch("app.registry.find_deck", side_effect=lambda r, d: deck if d in ("archidekt:417707", "417707") else None),
                        patch("app.registry.list_decks", return_value=[deck]),
                        patch("app.tuner_mod.compute", side_effect=_fake_compute)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_unknown_deck_404(self):
        with patch("app.registry.find_deck", return_value=None), patch("app.registry.list_decks", return_value=[]):
            self.assertEqual(self.client.get("/api/decks/nope/tune").status_code, 404)

    def test_tune_payload_and_caps(self):
        journal.save_journal(self.fake, DECK, {"budget_constraints": {"hard_cap": 40, "soft_cap": 15}})
        t = self.client.get("/api/decks/archidekt:417707/tune").json()["tune"]
        self.assertEqual((t["caps"]["hard_cap"], t["caps"]["hard_source"]), (40.0, "deck"))
        s = next(x for x in t["swaps"] if x["cut"]["name"] == "Weak Plan")
        self.assertEqual(s["buy"]["cap"], "soft")
        self.assertEqual(s["hidden_hard"], 1)
        self.assertNotIn("Pricey Bomb", json.dumps(t["swaps"]))
        self.assertEqual(self.client.get("/api/decks/archidekt:417707/tune?mode=nope").status_code, 400)

    def test_decisions_adds_and_pet(self):
        res = self.client.put("/api/decks/archidekt:417707/tune/decisions",
                              json={"cut": "Weak Plan", "decision": "own", "add": "Owned Ok"})
        self.assertEqual(res.status_code, 200, res.text)
        t = self.client.get("/api/decks/archidekt:417707/tune").json()["tune"]
        self.assertEqual(next(x for x in t["swaps"] if x["cut"]["name"] == "Weak Plan")["decision"], "own")
        self.assertEqual(self.client.put("/api/decks/archidekt:417707/tune/decisions",
                                         json={"cut": "Weak Plan", "decision": "maybe"}).status_code, 400)
        res = self.client.put("/api/decks/archidekt:417707/tune/adds", json={"adds": [{"name": "Plains", "qty": 3, "source": "basic"}]})
        self.assertEqual(res.json()["state"]["adds"][0]["qty"], 3)
        res = self.client.post("/api/decks/archidekt:417707/tune/pet", json={"name": "Weak Plan"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIn("Weak Plan", journal.load_journal(self.fake, DECK)["pet_cards"])
        self.assertEqual(self.client.post("/api/decks/archidekt:417707/tune/pet", json={"name": "Nope"}).status_code, 400)

    def test_why_without_llm_is_502_but_keeps_numbers(self):
        from analytics.interactions import llm as llm_mod
        with patch("analytics.interactions.llm.default_chain", return_value=llm_mod.LLMChain([])):
            res = self.client.post("/api/decks/archidekt:417707/tune/why", json={})
        self.assertEqual(res.status_code, 502)
        body = res.json()
        self.assertEqual(body["llm"]["status"], "no_llm")
        self.assertTrue(body["tune"]["swaps"])

    def test_finish_checkin_goes_to_tune(self):
        journal.save_journal(self.fake, DECK, {"target_power_level": {"scale": 7}})
        with patch("analytics.journal.collect_context", return_value={"power": 6.5}):
            self.client.post("/api/decks/archidekt:417707/checkin")
        res = self.client.post("/api/decks/archidekt:417707/checkin/finish", json={})
        self.assertEqual(res.json()["next"], "/deck/Holy%20Hellfire/tune")


@unittest.skipUnless(shutil.which("node"), "node not installed: deck_analytics.js tests need it")
class TestTuneHelpers(unittest.TestCase):
    HARNESS = r"""
const fs = require('fs');
global.document = { readyState: 'complete', addEventListener: () => {}, querySelectorAll: () => [],
                    getElementById: () => null, querySelector: () => null };
global.window = {};
eval(fs.readFileSync(process.argv[1], 'utf8'));
eval(fs.readFileSync(process.argv[2], 'utf8'));
const A = window.DeckAnalytics;
const own = { name: 'Combat Celebrant', source: 'deck', deck: 'Dawnbringer', score: 0.77, price: 0, on_plan: true };
const free = { name: 'Sword of Feast and Famine', source: 'free', score: 0.81, price: 0, on_plan: true };
const buy = { name: 'Moraug, Fury of Akoum', source: 'buy', price: 6.4, score: 0.79, on_plan: true };
const tune = { on_plan: { pct: 71, on: 71, total: 100 }, swaps: [
  { cut: { name: 'Ob Nixilis, Unshackled', on_plan: false }, own: own, buy: null, pick: 'own', decision: 'own', decision_add: 'Combat Celebrant' },
  { cut: { name: 'Angelic Arbiter', on_plan: false }, own: free, buy: buy, pick: 'buy', decision: 'buy' },
  { cut: { name: 'Phyrexian Arena', on_plan: true }, own: free, buy: null, pick: 'own', decision: null },
  { cut: { name: 'Steel Hellkite', on_plan: true }, own: free, buy: null, pick: 'own', decision: 'dismissed' },
  // accepted earlier; a recompute now ranks a different owned card first
  { cut: { name: 'Bloodthirster', on_plan: true }, own: free, buy: null, pick: 'own', decision: 'own', decision_add: 'Hellkite Charger' } ],
  adds: [{ name: 'Plains', qty: 2, source: 'basic' }] };
const L = A.changeList(tune);
const out = {
  L: L, text: A.changeText('Holy Hellfire', L),
  bars: A.whyBars({ edges: 1, fit: 0.21, edhrec: 0.31 }), barsNull: A.whyBars({ edges: 4, fit: null, edhrec: null }),
  gaps: [A.gapText(0.27), A.gapText(-0.05), A.gapText(null)],
  chips: [A.verdictChip({ own: free, buy: buy, pick: 'buy' }), A.verdictChip({ own: free, buy: buy, pick: 'own' }),
          A.verdictChip({ own: free, buy: null, verdict: 'keep_yours' }), A.verdictChip({ own: free, buy: null, verdict: 'owned' })],
  keep: A.keepYoursLine({ near_buy: { name: 'Neheb, the Eternal', price: 40, gap: 0.03 } }),
  src: [A.tuneSource(own), A.tuneSource(free), A.tuneSource(buy)],
};
process.stdout.write(JSON.stringify(out));
"""

    @classmethod
    def setUpClass(cls):
        res = subprocess.run(["node", "-e", cls.HARNESS, str(HUD_JS), str(DECK_JS)], capture_output=True, text=True,
                             timeout=30)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        cls.out = json.loads(res.stdout)

    def test_change_list_pairs_accepted_swaps_and_adds(self):
        items = [(i["op"], i["name"]) for i in self.out["L"]["items"]]
        self.assertEqual(items, [("out", "Ob Nixilis, Unshackled"), ("in", "Combat Celebrant"),
                                 ("out", "Angelic Arbiter"), ("in", "Moraug, Fury of Akoum"),
                                 ("out", "Bloodthirster"), ("in", "Hellkite Charger"), ("in", "Plains")])
        self.assertEqual(self.out["L"]["swaps"], 3)

    def test_accepted_card_wins_over_a_recomputed_pick(self):
        last = [i for i in self.out["L"]["items"] if i["name"] == "Hellkite Charger"][0]
        self.assertEqual(last["source"], "accepted")

    def test_spend_counts_buys_only(self):
        self.assertEqual(self.out["L"]["spend"], 6.4)

    def test_on_plan_projection(self):
        # 71/100 -> +2 swaps that bring an on-plan card for an off-plan cut, +2 basics (glue): 75/102.
        self.assertEqual(self.out["L"]["onPlan"], [71, 74])

    def test_physical_moves_from_other_decks(self):
        self.assertEqual(self.out["L"]["moves"], [{"name": "Combat Celebrant", "from": "Dawnbringer"}])

    def test_copy_text(self):
        t = self.out["text"].split("\n")
        self.assertEqual(t[0], "// Holy Hellfire · Tune")
        self.assertIn("1 Ob Nixilis, Unshackled", t)
        self.assertIn("2 Plains  // basic land", t)
        self.assertIn("1 Moraug, Fury of Akoum  // buy $6.40", t)
        self.assertEqual(t[-1], "// Spend $6.40")

    def test_why_bars_flag_low_values(self):
        b = {x["key"]: x for x in self.out["bars"]}
        self.assertEqual([x["key"] for x in self.out["bars"]], ["Edges", "Fit", "EDHREC"])
        self.assertTrue(b["Edges"]["low"] and b["Fit"]["low"])
        self.assertFalse(b["EDHREC"]["low"])
        self.assertEqual((b["Fit"]["text"], b["EDHREC"]["text"]), ("0.21", "31%"))
        n = {x["key"]: x for x in self.out["barsNull"]}
        self.assertEqual((n["Fit"]["text"], n["EDHREC"]["text"]), ("—", "0%"))

    def test_gap_and_verdicts(self):
        self.assertEqual(self.out["gaps"], ["+0.27", "−0.05", ""])
        self.assertEqual(self.out["chips"], [["worth buying", "signal"], ["owned is good enough", "ok"],
                                             ["keep yours", "ok"], None])
        self.assertEqual(self.out["keep"], "Keep yours: +0.03 isn't worth $40 (Neheb, the Eternal).")
        self.assertEqual(self.out["src"], ["from Dawnbringer", "owned · free", "buy $6.40"])


if __name__ == "__main__":
    unittest.main()
