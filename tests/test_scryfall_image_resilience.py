"""Scryfall lookup resilience: retry on 429/network error, and warn-once across script copies."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

SI_JS = Path(__file__).resolve().parent.parent / "src/web/static" / "js" / "scryfall_image.js"

HARNESS = r"""
const path = process.argv[1];
const load = () => { delete require.cache[require.resolve(path)]; return require(path); };
const out = {};
(async () => {
  const instant = { retryDelay: 0 };
  // 429 once, then success: exactly one retry, card returned.
  let S = load(), calls = 0;
  let card = await S.fetchCard({ set: 'cmm', collectorNumber: '1', name: 'X' }, Object.assign({
    fetch: async () => (++calls === 1 ? { ok: false, status: 429 } : { ok: true, json: async () => ({ id: 'a' }) }) }, instant));
  out.retry429 = { calls, id: card.id };
  // Browser surfaces a CORS-less 429 as a TypeError: also retried once.
  S = load(); calls = 0;
  card = await S.fetchCard({ name: 'Counterspell' }, Object.assign({
    fetch: async () => { if (++calls === 1) throw new TypeError('Failed to fetch'); return { ok: true, json: async () => ({ id: 'b' }) }; } }, instant));
  out.retryTypeError = { calls, id: card.id };
  // Persistent 429: gives up after one retry (2 calls), rejects.
  S = load(); calls = 0;
  let err = '';
  try { await S.fetchCard({ name: 'Counterspell' }, Object.assign({ fetch: async () => { calls++; return { ok: false, status: 429 }; } }, instant)); }
  catch (e) { err = String(e.message); }
  out.giveUp = { calls, err };
  // 404 is not retried.
  S = load(); calls = 0; err = '';
  try { await S.fetchCard({ name: 'Nope' }, Object.assign({ fetch: async () => { calls++; return { ok: false, status: 404 }; } }, instant)); }
  catch (e) { err = String(e.message); }
  out.notFound = { calls, err };
  // Warn once per name even when the script is evaluated twice.
  const w = []; console.warn = (m) => w.push(m);
  const A = load(); const B = load();
  for (let i = 0; i < 3; i++) { A.resolve({ name: 'Counterspell' }); B.resolve({ name: 'counterspell', version: 'large' }); }
  out.warns = w.length;
  process.stdout.write(JSON.stringify(out));
})();
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestScryfallImageResilience(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        res = subprocess.run(["node", "-e", HARNESS, str(SI_JS)], capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        cls.out = json.loads(res.stdout)

    def test_429_is_retried_once(self):
        self.assertEqual(self.out["retry429"], {"calls": 2, "id": "a"})

    def test_network_error_from_cors_less_429_is_retried_once(self):
        self.assertEqual(self.out["retryTypeError"], {"calls": 2, "id": "b"})

    def test_persistent_429_gives_up_after_one_retry(self):
        self.assertEqual(self.out["giveUp"]["calls"], 2)
        self.assertIn("429", self.out["giveUp"]["err"])

    def test_404_is_not_retried(self):
        self.assertEqual(self.out["notFound"]["calls"], 1)

    def test_name_only_warning_is_logged_once_across_script_copies(self):
        self.assertEqual(self.out["warns"], 1)


if __name__ == "__main__":
    unittest.main()
