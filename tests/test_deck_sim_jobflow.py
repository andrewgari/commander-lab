"""SM-4F job-flow regressions (F4, F8, F9, F10) for static/js/deck_sim.js.

Runs the real script under node with a minimal fake DOM and asserts on the
rendered HTML string.
"""
import datetime
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIM_JS = ROOT / "src/web/static" / "js" / "deck_sim.js"

HARNESS = r"""
const fs = require('fs');
const cfg = JSON.parse(process.argv[2]);
const store = Object.assign({}, cfg.storage || {});
const rootEl = { id: 'simRoot', innerHTML: '', querySelectorAll: () => [], querySelector: () => null, addEventListener: () => {} };
global.document = { readyState: 'complete', addEventListener() {}, getElementById: (id) => (id === 'simRoot' ? rootEl : null),
  querySelectorAll: () => [], querySelector: () => null };
global.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
global.window = { localStorage: global.localStorage };
global.fetch = async (url) => {
  const body = /\/jobs\//.test(url) ? cfg.job : cfg.sim;
  return { ok: true, status: 200, text: async () => JSON.stringify(body) };
};
global.window.fetch = global.fetch;
global.window.HUD = { esc: (s) => String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'),
  cardName: (n) => n, stat: () => '', initSortable: () => {} };
eval(fs.readFileSync(process.argv[1], 'utf8'));
window.DeckSim.init('archidekt:1');
setTimeout(() => { process.stdout.write(JSON.stringify({ html: rootEl.innerHTML, store })); process.exit(0); }, 300);
"""

POOL = [
    {"id": "precon:a", "name": "Alpha Precon", "source": "precon"},
    {"id": "deck:b", "name": "Bravo", "source": "synced"},
    {"id": "deck:c", "name": "Charlie", "source": "synced"},
]
REPORT_SIM = {"meta": {"deck_name": "X", "created_at": "2026-01-01T00:00:00"},
              "report": {"params": {"opponent_mode": "specific", "games": 20, "opponents": ["precon:a", "deck:b", "gone:z"]}}}


def recent():
    """Naive-UTC ISO timestamp 3 minutes ago (what the server emits)."""
    d = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=3)
    return d.strftime("%Y-%m-%dT%H:%M:%S")


def run(cfg):
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node not available")
    res = subprocess.run([node, "-e", HARNESS, str(SIM_JS), json.dumps(cfg)], capture_output=True, text=True, timeout=20)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def sim(running_job=None, with_report=False):
    d = {"opponent_pool": POOL, "running_job": running_job, "focus": None}
    if with_report:
        d["sim"] = REPORT_SIM
    return d


class TestResumeFailedJob(unittest.TestCase):
    def setUp(self):
        self.out = run({
            "storage": {"simJob:archidekt:1": json.dumps({"job_id": "j1", "started": "2026-01-01T00:00:00Z"})},
            "sim": sim(with_report=True),
            "job": {"job_id": "j1", "status": "failed", "error": "Forge daemon died <b>", "created_at": "2026-01-01T00:00:00",
                    "params": {"opponent_mode": "specific", "games": 5, "notes": "watch ramp",
                              "opponents": ["deck:b", "deck:c"]}},
        })
        self.html = self.out["html"]

    def test_failed_card_rendered_with_danger_and_copy(self):
        self.assertIn('id="job"', self.html)
        self.assertIn("is-failed", self.html)
        self.assertIn('data-phase="failed"', self.html)
        self.assertIn("Couldn&#39;t finish", self.html.replace("'", "&#39;"))

    def test_tray_reopened_with_params_and_error_under_notes(self):
        self.assertIn('id="tray" open', self.html)
        self.assertIn("watch ramp", self.html)
        self.assertIn('data-value="deck:b"', self.html)
        self.assertIn('data-value="deck:c"', self.html)
        err = self.html.index('id="simError"')
        self.assertGreater(err, self.html.index('id="notes"'))
        self.assertIn("Forge daemon died &lt;b&gt;", self.html[err:err + 200])

    def test_controls_enabled_and_job_not_forgotten(self):
        run_btn = self.html[self.html.index('id="runBtn"'):][:80]
        self.assertNotIn("disabled", run_btn.split(">")[0])
        notes = self.html[self.html.index('id="notes"'):][:80]
        self.assertNotIn("disabled", notes.split(">")[0])
        self.assertIn("simJob:archidekt:1", self.out["store"])


class TestActiveRun(unittest.TestCase):
    def test_running_disables_run_and_notes_and_uses_created_at(self):
        out = run({"sim": sim(running_job={"job_id": "j2", "status": "running", "games_completed": 2, "games_total": 20,
                                           "created_at": recent()}, with_report=True),
                   "job": {"job_id": "j2", "status": "running", "games_completed": 2, "games_total": 20, "created_at": recent()}})
        h = out["html"]
        self.assertIn("disabled", h[h.index('id="runBtn"'):][:80].split(">")[0])
        self.assertIn("disabled", h[h.index('id="notes"'):][:80].split(">")[0])
        self.assertIn('data-phase="sim"', h)
        self.assertIn("min left", h)
        self.assertNotIn("about 7 min left", h)

    def test_queued_is_indeterminate_phase(self):
        out = run({"sim": sim(running_job={"job_id": "j3", "status": "queued", "games_total": 20, "created_at": recent()}),
                   "job": {"job_id": "j3", "status": "queued", "games_total": 20, "created_at": recent()}})
        self.assertIn('data-phase="queued"', out["html"])

    def test_done_leaves_controls_enabled(self):
        out = run({"sim": sim(with_report=True), "job": {}})
        h = out["html"]
        self.assertNotIn("disabled", h[h.index('id="notes"'):][:80].split(">")[0])


class TestSpecificPrefill(unittest.TestCase):
    def test_prefill_from_saved_report_params_drops_unknown_ids(self):
        out = run({"sim": sim(with_report=True), "job": {}})
        h = out["html"]
        self.assertIn('data-value="precon:a"', h)
        self.assertIn('data-value="deck:b"', h)
        self.assertNotIn("gone:z", h)


class TestSm4fCloseout(unittest.TestCase):
    def test_failed_card_has_failure_copy_and_frozen_clock(self):
        out = run({"storage": {"simJob:archidekt:1": json.dumps({"job_id": "j1"})}, "sim": sim(with_report=True),
                   "job": {"job_id": "j1", "status": "failed", "error": "boom", "created_at": "2026-01-01T00:00:00",
                           "updated_at": "2026-01-01T00:02:05", "params": {"opponent_mode": "random", "games": 5}}})
        h = out["html"]
        card = h[h.index('id="job"'):]
        self.assertNotIn("until this run finishes", card.split("</section>")[0])
        self.assertIn("settings are restored", card)
        self.assertIn(">2:05<", card)

    def test_report_head_has_no_undefined(self):
        out = run({"sim": sim(with_report=True), "job": {}})
        self.assertNotIn("undefined", out["html"])


if __name__ == "__main__":
    unittest.main()
