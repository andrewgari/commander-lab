"""Regression test for the Simulate tab P0: deck_sim.js must not read
window.HUD.esc synchronously at script-evaluation time, since hud.js is
loaded with `defer` and may not have attached window.HUD yet when
deck_sim.js (not deferred) is evaluated. See docs/design/sim/UX_SPEC.md.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIM_JS = ROOT / "static" / "js" / "deck_sim.js"


class TestDeckSimEvaluationOrder(unittest.TestCase):
    def test_deck_sim_js_evaluates_without_hud_attached(self):
        """eval()-ing deck_sim.js before window.HUD exists must not throw
        (the original bug: `var H = window.HUD; var esc = H.esc;` at the
        top of the IIFE threw TypeError: Cannot read properties of
        undefined when HUD wasn't attached yet)."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        script = r"""
const fs = require('fs');
global.document = {
    readyState: 'loading',
    addEventListener: () => {},
    getElementById: () => null,
    querySelectorAll: () => [],
    querySelector: () => null,
};
global.window = {
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    fetch: async () => ({ ok: true, status: 200, text: async () => '{}' }),
};
delete global.window.HUD;

try {
    eval(fs.readFileSync(process.argv[1], 'utf8'));
    process.stdout.write(JSON.stringify({
        error: null,
        deckSimAttached: typeof global.window.DeckSim === 'object' && typeof global.window.DeckSim.init === 'function',
    }));
} catch (err) {
    process.stdout.write(JSON.stringify({ error: { name: err.name, message: err.message }, deckSimAttached: false }));
}
"""
        res = subprocess.run([node, "-e", script, str(SIM_JS)], capture_output=True, text=True, timeout=10)
        self.assertEqual(res.returncode, 0, f"node execution failed: {res.stderr}")
        data = json.loads(res.stdout)
        self.assertIsNone(data["error"], f"deck_sim.js threw while HUD was unattached: {data['error']}")
        self.assertTrue(data["deckSimAttached"], "window.DeckSim.init was not attached")

    def test_deck_sim_render_uses_hud_after_it_attaches(self):
        """After deck_sim.js is evaluated (HUD absent) and window.HUD is
        attached afterward (mirroring defer timing), calling DeckSim.init
        and letting it render must actually use the real window.HUD (not
        a stale captured reference) and must not throw."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        script = r"""
const fs = require('fs');
const calls = [];
const rootEl = {
    id: 'simRoot',
    innerHTML: '',
    dataset: { deckId: 'archidekt:1' },
    querySelectorAll: () => [],
    querySelector: () => null,
    addEventListener: () => {},
};
global.document = {
    readyState: 'complete',
    addEventListener: () => {},
    getElementById: (id) => (id === 'simRoot' ? rootEl : null),
    querySelectorAll: () => [],
    querySelector: () => null,
};
global.window = {
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    fetch: async () => ({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ opponent_pool: [], sim: null, running_job: null }),
    }),
};
delete global.window.HUD;

// 1. Evaluate deck_sim.js before HUD exists (the bug scenario)
eval(fs.readFileSync(process.argv[1], 'utf8'));

// 2. Attach HUD afterward, as hud.js (defer) does
global.window.HUD = {
    esc: function (s) { calls.push('esc'); return String(s == null ? '' : s); },
    cardName: function (n) { return n; },
    stat: function () { return ''; },
    initSortable: function () { calls.push('initSortable'); },
};

try {
    global.window.DeckSim.init('archidekt:1');
    setTimeout(() => {
        process.stdout.write(JSON.stringify({ error: null, usedRealHud: calls.length > 0 }));
    }, 50);
} catch (err) {
    process.stdout.write(JSON.stringify({ error: { name: err.name, message: err.message }, usedRealHud: false }));
}
"""
        res = subprocess.run([node, "-e", script, str(SIM_JS)], capture_output=True, text=True, timeout=10)
        self.assertEqual(res.returncode, 0, f"node execution failed: {res.stderr}")
        data = json.loads(res.stdout)
        self.assertIsNone(data["error"], f"DeckSim.init threw: {data['error']}")


if __name__ == "__main__":
    unittest.main()
