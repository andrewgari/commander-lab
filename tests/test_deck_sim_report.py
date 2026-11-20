"""deck_sim_report.js: WoWLogs-style diagnostics built from persisted telemetry (node-evaluated)."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

JS = Path(__file__).resolve().parent.parent / "src/web/static" / "js" / "deck_sim_report.js"

SCRIPT = r"""
const R = require(process.argv[1]);
const T = 'user_1';
function game(i, nopp, won, full) {
  const pod = [{seat:1, deck_id:T, tested:true, owned:true}];
  for (let k=0;k<nopp;k++) pod.push({seat:k+2, deck_id:'opp'+k, tested:false, owned:false});
  const g = {game:i, pod, winner: won?'me':'opp0', user_won:won, total_turns:9, win_reason:'dmg', curves:{}, card_metrics:{}};
  if (full) {
    g.curves[T] = [1,2,3,4].map(t => ({turn:t, land_played: t!==3 || i%2, mana_spent:t, spells_cast:1}));
    g.card_metrics[T] = {'Sol Ring': {drawn:true, cast_turn:1, stuck:false}};
  }
  return g;
}
function report(n, nopp, full) {
  const games = []; for (let i=1;i<=n;i++) games.push(game(i, nopp, i%2===0, full));
  const cards = full ? {[T]: {'Sol Ring': {games:n, drawn:{n:n,count:n}, cast:{n:n,count:n-3}, stuck_in_hand:{n:n,count:3}, first_cast_turn:{n:n-3,median:1}},
                              'Zed': {games:n, drawn:{n:0,count:0}, cast:{n:0,count:0}, stuck_in_hand:{n:0,count:0}, first_cast_turn:{n:0,median:null}}}} : {};
  return {games_log: games.map(g=>({game:g.game})), telemetry:{deck_id:T, params:{opponent_mode:'standard'}, games, aggregate:{cards}}};
}
const out = {};
for (const nopp of [1,2,3,4,5]) {
  const html = R.render(report(25, nopp, true));
  out['full'+nopp] = ['Curve execution','Scorecard','Per-game log','Results by pod','sm-cardtbl','Commander cast turn'].every(s => html.includes(s)) && !html.includes('did not emit per-turn') && !html.includes('No per-card');
}
const sparse = R.render(report(25, 3, false));
out.sparseCurveEmpty = sparse.includes('did not emit per-turn');
out.sparseNoZeroPct = !sparse.includes('0%');
out.low = R.render(report(8, 2, true)).includes('low sample');
out.notLow = !R.render(report(25, 2, true)).includes('low sample · n=25');
out.legacy = R.render({}).includes('predates diagnostics');
const m = R.build(report(25, 2, true));
out.n0 = m.decks[0].rows.find(r=>r.name==='Zed').drawn === null;
out.scoreLow = R.build(report(8, 2, true)).score.working.length === 0;
out.sortNullLast = [R.sortCmp('', '3', true, 1), R.sortCmp('', '3', true, -1), R.sortCmp('2','3',true,-1)];
out.xss = !R.render({telemetry:{deck_id:T, games:[{game:1,pod:[{tested:false,deck_id:'<script>'}],user_won:false}], aggregate:{cards:{}}}}).includes('<script>');
console.log(JSON.stringify(out));
"""


class T(unittest.TestCase):
    def test_report(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        res = subprocess.run([node, "-e", SCRIPT, str(JS)], capture_output=True, text=True, timeout=20)
        self.assertEqual(res.returncode, 0, res.stderr)
        o = json.loads(res.stdout)
        for k in ["full1", "full2", "full3", "full4", "full5", "sparseCurveEmpty", "sparseNoZeroPct", "low", "notLow", "legacy", "n0", "scoreLow", "xss"]:
            self.assertTrue(o[k], k)
        self.assertEqual(o["sortNullLast"], [1, 1, 1])


if __name__ == "__main__":
    unittest.main()
