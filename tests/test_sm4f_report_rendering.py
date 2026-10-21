"""SM-4F F1/F3/F6/F7: report head, stat strip and tempo section (node-evaluated deck_sim.js)."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from analytics import sim_diff as sd

SIM_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "deck_sim.js"

SCRIPT = r"""
const fs = require('fs');
global.document = {readyState:'complete', addEventListener(){}, getElementById(){return null}, querySelectorAll(){return []}, querySelector(){return null}};
global.window = {localStorage:{getItem(){return null},setItem(){},removeItem(){}}, fetch: async()=>({ok:true,status:200,text:async()=>'{}'}), DeckAnalytics:{age:()=> '2h ago'}};
const esc = s => String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
global.window.HUD = {esc, cardName:n=>n, initSortable(){},
  stat:(l,v,o)=>{o=o||{};return '<div class="hud-stat"><b>'+esc(l)+'</b><i class="v">'+esc(v)+'</i>'+(o.unit?'<u>'+esc(o.unit)+'</u>':'')+'<s class="foot">'+esc(o.foot||'')+'</s></div>';}};
eval(fs.readFileSync(process.argv[1],'utf8'));
const T = window.DeckSim._test;
const tc = {land_drops:[], static:{t5_starvation_probability:0.41, mana_utilization_t2_t6:0.61, commander_cmc:4, lands:36, library_size:99}, sim:{t5_mana_starvation_rate:0.15, avg_first_commander_turn:4, avg_mana_utilization_t2_t6:0.5},
  reads:{t5_mana_starvation:{read:'better', read_label:'Better than math'}, mana_utilization:{read:'worse', read_label:'Below math'}}};
const rep = (over) => Object.assign({params:{opponent_mode:'standard'}, comparison:{tempo_comparison:tc, pod_breakdown:{games_played:20,user_wins:5,win_rate:0.25,win_rate_ci95:[0.1119,0.4687],avg_total_turns:8.6}}}, over||{});
const out = {};
const s2 = T.s2(rep());
out.f3Better = /ok[^>]*>[^<]*▲ Better than math/.test(s2) || s2.includes('Better than math');
out.f3NoBelowForStarve = !/signal[^>]*>▼ Below math<\/span><\/td>\s*<\/tr>\s*<tr>.*Commander/.test(s2);
out.f3Worse = s2.includes('▼ Below math');
const r2 = rep({params:{opponent_mode:'random', verbose_log:false}});
const s2r = T.s2(r2);
out.f1Panel = s2r.includes('sm-unrec') && s2r.includes('Not recorded for random pods');
out.f1VerboseFalse = T.s2(rep({params:{opponent_mode:'standard', verbose_log:false}})).includes('sm-unrec');
out.f1Flag = T.s2(Object.assign(rep(), {comparison:{tempo_comparison:Object.assign({metrics_available:false}, tc), pod_breakdown:{}}})).includes('sm-unrec');
out.f1NoPanelNormal = !s2.includes('sm-unrec');
const strip = T.statStrip(rep());
out.f6Ci = strip.includes('95% CI 11–47%');
out.f6Round = strip.includes('<i class="v">9</i>');
out.f6NoCi = !T.statStrip(rep({comparison:{tempo_comparison:tc,pod_breakdown:{win_rate:0.25,avg_total_turns:8}}})).includes('CI');
const h1 = T.reportHead(rep(), {created_at:'2026-10-21T14:15:00Z', deck_name:'D'});
out.f7Built = h1.includes('<time') && h1.includes('Built') === true || h1.includes('2h ago');
const h2 = T.reportHead(rep(), {deck_name:'D'});
const h3 = T.reportHead(rep(), {created_at:'garbage', deck_name:'D'});
out.f7Absent = !h2.includes('<time') && !h3.includes('<time');
console.log(JSON.stringify(out));
"""


class SM4F(unittest.TestCase):
    def test_node(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        res = subprocess.run([node, "-e", SCRIPT, str(SIM_JS)], capture_output=True, text=True, timeout=20)
        self.assertEqual(res.returncode, 0, res.stderr)
        o = json.loads(res.stdout)
        for k, v in o.items():
            self.assertTrue(v, k)

    def test_wilson_ci(self):
        lo, hi = sd.wilson_ci95(5, 20)
        self.assertAlmostEqual(lo, 0.1119, places=3)
        self.assertAlmostEqual(hi, 0.4687, places=3)
        self.assertIsNone(sd.wilson_ci95(0, 0))

    def test_read_polarity(self):
        self.assertEqual(sd._read("t5_mana_starvation", 0.15, 0.41)["read"], "better")
        self.assertEqual(sd._read("t5_mana_starvation", 0.6, 0.41)["read"], "worse")
        self.assertEqual(sd._read("mana_utilization", 0.5, 0.61)["read"], "worse")
        self.assertEqual(sd._read("mana_utilization", 0.7, 0.61)["read"], "better")
        self.assertEqual(sd._read("commander_turn", 6, 4, 1.0)["read_label"], "Later than math")
        self.assertIsNone(sd._read("mana_utilization", None, 0.5)["read"])

    def test_metrics_available(self):
        self.assertFalse(sd._metrics_available({"params": {"opponent_mode": "random"}}))
        self.assertFalse(sd._metrics_available({"params": {"verbose_log": False}}))
        self.assertTrue(sd._metrics_available({"params": {"opponent_mode": "standard"}}))


if __name__ == "__main__":
    unittest.main()
