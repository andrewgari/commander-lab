"""Focus chips: persisted catalog, seed-once, validation/snapshot, suggestions, run payload, legacy reports, UI wiring."""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.analytics import sim_focus as sf
from src.server.domain.analytics import sim_service as svc
from tests.test_sim_service_and_api import Base, FakeOracle, FakeSim, good_reply, run_sync
from tests.test_sim_store_and_diff import FakeRedis

ROOT = Path(__file__).resolve().parent.parent
ALL_TELEMETRY = set(sf.SUPPORTED_TELEMETRY) | {c["telemetry_key"] for c in sf.SEED if c["telemetry_key"]}


def stats(**kw):
    base = {"built": True, "mv": 3, "counts": {"lands": 37, "ramp": 12, "card_advantage": 9, "removal": 12, "finisher": 4},
            "floors": {"ramp": 10, "card_advantage": 7, "removal": 10, "finisher": 2},
            "targets": {"lands": 38}, "commander_roles": 0}
    base.update(kw)
    return base


def suggested(r, st, supported=ALL_TELEMETRY):
    return [x["slug"] for x in sf.suggest(sf.list_catalog(r), st, supported)]


class TestStore(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_seeds_eight_once(self):
        cat = sf.list_catalog(self.r)
        self.assertEqual([c["slug"] for c in cat], ["manabase", "ramp", "tempo", "consistency", "card-draw", "commander",
                                                    "interaction", "closing"])
        self.assertTrue(all("builtin" not in c and "built_in" not in c for c in cat))

    def test_delete_does_not_reseed(self):
        sf.list_catalog(self.r)
        sf.delete(self.r, "ramp")
        self.assertNotIn("ramp", [c["slug"] for c in sf.list_catalog(self.r)])
        sf.ensure_seeded(self.r)
        self.assertEqual(len(sf.list_catalog(self.r)), 7)

    def test_delete_all_stays_empty(self):
        for c in sf.list_catalog(self.r):
            sf.delete(self.r, c["slug"])
        self.assertEqual(sf.list_catalog(self.r), [])

    def test_update_reorder_create(self):
        sf.list_catalog(self.r)
        sf.update(self.r, "ramp", {"label": "Mana"})
        self.assertEqual([c for c in sf.list_catalog(self.r) if c["slug"] == "ramp"][0]["label"], "Mana")
        order = [c["slug"] for c in reversed(sf.list_catalog(self.r))]
        sf.reorder(self.r, order)
        self.assertEqual([c["slug"] for c in sf.list_catalog(self.r)], order)
        sf.create(self.r, {"slug": "mine", "label": "Mine", "hint": "Custom"})
        with self.assertRaises(sf.FocusError):
            sf.create(self.r, {"slug": "mine", "label": "Dup", "hint": "x"})
        with self.assertRaises(sf.FocusError):
            sf.create(self.r, {"slug": "Bad Slug", "label": "x", "hint": "x"})
        with self.assertRaises(sf.FocusError):
            sf.reorder(self.r, ["mine"])

    def test_custom_ruleless_chip_never_suggested(self):
        sf.create(self.r, {"slug": "mine", "label": "Mine", "hint": "Custom"})
        bad = stats(counts={"lands": 20, "ramp": 1, "card_advantage": 0, "removal": 0, "finisher": 0}, mv=7, commander_roles=4)
        self.assertNotIn("mine", suggested(self.r, bad))

    def test_unavailable_telemetry_hidden(self):
        slugs = [c["slug"] for c in sf.available_catalog(self.r)]
        self.assertEqual(sorted(slugs), ["closing", "commander", "consistency", "manabase", "tempo"])
        self.assertIn("ramp", [c["slug"] for c in sf.list_catalog(self.r)])
        sf.create(self.r, {"slug": "mine", "label": "Mine", "hint": "x"})
        self.assertIn("mine", [c["slug"] for c in sf.available_catalog(self.r)])


class TestSuggestions(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_each_rule(self):
        cases = {
            "manabase": stats(counts={**stats()["counts"], "lands": 33}),
            "ramp": stats(counts={**stats()["counts"], "ramp": 6}),
            "card-draw": stats(counts={**stats()["counts"], "card_advantage": 3}),
            "interaction": stats(counts={**stats()["counts"], "removal": 4}),
            "closing": stats(counts={**stats()["counts"], "finisher": 0}),
            "commander": stats(commander_roles=3),
        }
        for slug, st in cases.items():
            self.assertEqual(suggested(self.r, st), [slug], slug)
        self.assertEqual(suggested(self.r, stats(mv=7)), ["ramp"])  # high commander MV

    def test_reason_quotes_computed_values(self):
        out = sf.suggest(sf.list_catalog(self.r), stats(counts={**stats()["counts"], "ramp": 6}), ALL_TELEMETRY)
        self.assertEqual(out[0]["reason"], "6 ramp pieces (floor 10)")

    def test_cap_and_rank(self):
        st = stats(counts={"lands": 30, "ramp": 2, "card_advantage": 1, "removal": 2, "finisher": 0}, commander_roles=3)
        out = suggested(self.r, st)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0], "closing")  # 100% under its floor ranks first

    def test_nothing_triggers_none(self):
        self.assertEqual(suggested(self.r, stats()), [])

    def test_unbuilt_deck_none(self):
        self.assertEqual(suggested(self.r, stats(built=False, mv=9)), [])

    def test_telemetry_hidden_never_suggested(self):
        st = stats(counts={**stats()["counts"], "card_advantage": 0})
        self.assertEqual(suggested(self.r, st, supported=sf.SUPPORTED_TELEMETRY), [])  # mono draw-gap: card-draw is hidden
        self.assertEqual(suggested(self.r, st), ["card-draw"])

    def test_fixtures(self):
        three_colour = stats(mv=6, commander_roles=3, counts={**stats()["counts"], "ramp": 8})
        self.assertEqual(set(suggested(self.r, three_colour)), {"ramp", "commander"})
        self.assertEqual(suggested(self.r, stats()), [])  # no-gap deck

    def test_deleted_chip_not_suggested(self):
        sf.list_catalog(self.r)
        sf.delete(self.r, "closing")
        self.assertEqual(suggested(self.r, stats(counts={**stats()["counts"], "finisher": 0})), [])


class TestRunPersistence(Base):
    def do_run(self, params):
        sim = FakeSim()
        run_sync(self.r, self.deck, params, sim, FakeOracle(good_reply))
        return sim

    def test_validation_drops_unknown_hidden_dupes_and_caps(self):
        sf.ensure_seeded(self.r)
        cat = sf.available_catalog(self.r)
        p = svc.validate_params({"focus": ["nope", "ramp", "manabase", "manabase", "tempo", "commander", "closing"]}, cat)
        self.assertEqual(p["focus"], ["manabase", "tempo", "commander"])
        self.assertEqual(p["focus_snapshot"][0], {"slug": "manabase", "label": "Manabase"})
        self.assertEqual(svc.validate_params({}, cat)["focus"], [])
        with self.assertRaises(svc.SimParamError):
            svc.validate_params({"focus": "ramp"}, cat)

    def test_run_persists_focus_notes_and_survives_rename_delete(self):
        self.do_run({"focus": ["manabase", "closing"], "notes": "hi"})
        view = svc.saved_view(self.r, self.deck)
        params = view["sim"]["report"]["params"]
        self.assertEqual((params["focus"], params["notes"]), (["manabase", "closing"], "hi"))
        sf.update(self.r, "manabase", {"label": "Lands!"})
        sf.delete(self.r, "closing")
        params = svc.saved_view(self.r, self.deck)["sim"]["report"]["params"]
        self.assertEqual([f["label"] for f in params["focus_snapshot"]], ["Manabase", "Closing"])

    def test_oracle_prompt_gets_emphasis(self):
        oracle = FakeOracle(good_reply)
        job = svc.begin_run(self.r, self.deck, {"focus": ["commander"]}, FakeSim())
        from unittest.mock import patch
        from tests.test_sim_store_and_diff import make_review
        with patch.object(svc, "_static_review", return_value=make_review()):
            svc.run_job(self.r, job, self.deck, FakeSim(), lambda: oracle, poll_interval=0, sleep=lambda s: None)
        prompt = oracle.calls[0][0][-1]["content"]
        self.assertIn("emphasise: Commander", prompt)
        self.assertNotIn("emphasise", svc.request_prompt("x", {"focus_snapshot": []}, {}, {}))

    def test_saved_choice_wins_over_suggestions_and_empty_is_a_choice(self):
        self.do_run({"focus": ["tempo"]})
        self.assertEqual(svc.focus_view(self.r, self.deck)["saved"], ["tempo"])
        self.assertEqual(svc.focus_view(self.r, self.deck)["suggestions"], [])
        self.do_run({"focus": []})
        v = svc.focus_view(self.r, self.deck)
        self.assertEqual((v["saved"], v["suggestions"]), ([], []))

    def test_changed_decklist_reopens_suggestions(self):
        self.do_run({"focus": ["tempo"]})
        changed = dict(self.deck, cards=self.deck["cards"] + [{"name": "New Card", "quantity": 1}])
        self.assertIsNone(svc.focus_view(self.r, changed)["saved"])

    def test_legacy_report_defaults(self):
        from src.server.domain.analytics import sim_store
        from tests.test_sim_store_and_diff import make_report
        rep = make_report()
        rep.pop("params", None)
        rep["params"] = {"opponent_mode": "standard", "games": 20, "notes": "old"}
        sim_store.save_completed(self.r, self.deck, {"job_id": "j", "status": "done", "report": rep})
        params = svc.saved_view(self.r, self.deck)["sim"]["report"]["params"]
        self.assertEqual((params["focus"], params["focus_snapshot"]), ([], []))


class TestUi(unittest.TestCase):
    JS = (ROOT / "src/web/static" / "js" / "deck_sim.js").read_text()

    def test_no_test_notes_string(self):
        for p in ("src/web/static", "src/web/templates", "docs", "src/server/domain/analytics", "tests"):
            for f in (ROOT / p).rglob("*"):
                if f.is_file() and f.suffix in (".js", ".html", ".py", ".md", ".css") and f.name != "test_sim_focus.py":
                    self.assertNotRegex(f.read_text(errors="ignore"), r"(?i)test notes", str(f))

    def test_static_markup(self):
        self.assertIn("aria-pressed", self.JS)
        self.assertIn("What to watch for", self.JS)
        self.assertIn("focus: (STATE.focus", self.JS)

    def test_toggle_cap_clear_payload(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        script = r"""
const fs=require('fs');
const els={};
const mk=(id)=>els[id]||(els[id]={id,innerHTML:'',dataset:{},value:'',placeholder:'',style:{},hidden:false,
  addEventListener(){},querySelectorAll:()=>[],querySelector:()=>null,classList:{toggle(){},add(){},remove(){}},setAttribute(){},removeAttribute(){}});
let calls=[];
global.document={readyState:'complete',addEventListener(){},getElementById:(id)=>id==='simRoot'?mk(id):null,
  querySelectorAll:()=>[],querySelector:()=>null};
global.localStorage={getItem:()=>null,setItem(){},removeItem(){}};
const info={catalog:[1,2,3,4].map(i=>({slug:'c'+i,label:'Chip '+i,hint:'hint '+i})),saved:null,max:3,
  suggestions:[{slug:'c1',reason:'7 ramp pieces'},{slug:'c2',reason:'r2'}]};
global.window={localStorage:global.localStorage,fetch:async(url,o)=>{calls.push([url,o&&o.body]);
  return {ok:true,status:200,text:async()=>JSON.stringify({success:true,opponent_pool:[],sim:null,running_job:null,focus:info})};},
  HUD:{esc:(s)=>String(s==null?'':s),cardName:(n)=>n,stat:()=>'',initSortable(){}}};
global.localStorage=global.window.localStorage;
global.fetch=global.window.fetch;
eval(fs.readFileSync(process.argv[1],'utf8'));
window.DeckSim.init('d1');
setTimeout(()=>{
  const html=els.simRoot.innerHTML;
  const out={html_has_pressed:/aria-pressed="true"[^>]*>Chip 1/.test(html)||/data-slug="c1" aria-pressed="true"/.test(html),
    suggested:/Suggested/.test(html), reason:/7 ramp pieces/.test(html), hint:/We'll watch: hint 1/.test(html),
    ph:/Anything specific about chip 1, chip 2/.test(html), unselected_disabled:/data-slug="c3" aria-pressed="false"/.test(html)};
  process.stdout.write(JSON.stringify(out));
},50);
"""
        res = subprocess.run([node, "-e", script, str(ROOT / "src/web/static/js/deck_sim.js")], capture_output=True, text=True, timeout=15)
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        self.assertTrue(out["html_has_pressed"], out)
        self.assertTrue(out["suggested"] and out["reason"] and out["hint"] and out["ph"], out)


if __name__ == "__main__":
    unittest.main()
