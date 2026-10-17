"""Printing-aware Scryfall image URL resolver (static/js/scryfall_image.js).

The helper is plain JS with no DOM, so it runs in node against hand-built card
refs shaped like Archidekt / Scryfall payloads. Covers: id path (image_uris and
API fallback), set/number path incl. encoding, name fallback
(printingSpecific=false + warning), DFC front/back, cache hits, the throttle,
and the throttled JSON lookup (headers, cache write-through, failure fallback).
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SI_JS = ROOT / "static" / "js" / "scryfall_image.js"

SID = "0c3e2a5b-4f4f-4a53-9c3f-5d1a6c1d9e10"
CDN = "https://cards.scryfall.io"
SOL_URIS = {
    "small": CDN + "/small/front/0/c/0c3e.jpg",
    "normal": CDN + "/normal/front/0/c/0c3e.jpg",
    "large": CDN + "/large/front/0/c/0c3e.jpg",
}
DELVER_FACES = [
    {"name": "Delver of Secrets", "image_uris": {"normal": CDN + "/normal/front/d/e/delver.jpg",
                                                  "large": CDN + "/large/front/d/e/delver.jpg"}},
    {"name": "Insectile Aberration", "image_uris": {"normal": CDN + "/normal/back/d/e/delver.jpg",
                                                     "large": CDN + "/large/back/d/e/delver.jpg"}},
]

# Each scenario is a JS expression evaluated with a fresh module instance
# (S) so caches never leak between scenarios.
SCENARIOS = {
    # ---- id path -------------------------------------------------------------
    "id_with_image_uris": "S.resolve({scryfallId: SID, imageUris: SOL_URIS})",
    "id_prefers_normal": "S.resolve({scryfallId: SID, imageUris: {small: 's', large: 'l', normal: 'n'}})",
    "id_uris_without_normal": "S.resolve({imageUris: {large: CDN + '/large/x.jpg', small: CDN + '/small/x.jpg'}})",
    "id_api_fallback": "S.resolve({scryfallId: SID})",
    "id_snake_case_and_uid": "[S.resolve({scryfall_id: SID.toUpperCase()}), S.resolve({uid: SID})]",
    "id_back_face_api": "S.resolve({scryfallId: SID, face: 'back'})",
    "dpr": "[S.versionForDpr(1), S.versionForDpr(1.5), S.versionForDpr(2), "
           "S.resolve({scryfallId: SID, version: 'large'}, {version: S.versionForDpr(2)})]",
    # ---- set + collector number ---------------------------------------------
    "set_number": "S.resolve({set: 'CMM', collectorNumber: '123'})",
    "set_number_star": "S.resolve({set: 'plst', collectorNumber: '123\u2605'})",
    "set_number_symbols": "S.resolve({set: 'SLD', collectorNumber: '1/2 a#b?'})",
    "set_number_back": "S.resolve({set: 'isd', collectorNumber: '51', face: 'back'})",
    "set_without_number_is_name": "S.resolve({set: 'cmm', name: 'Sol Ring'}, {onWarn: () => {}})",
    "id_beats_set_number": "S.resolve({scryfallId: SID, set: 'cmm', collectorNumber: '1'})",
    # ---- name fallback -------------------------------------------------------
    "name_only": "S.resolve({name: \"Jace, the Mind Sculptor\"}, {onWarn: m => WARNS.push(m)})",
    "name_encoding": "S.resolve({name: \"Sol Ring & Co/Fire // Ice\"}, {onWarn: () => {}})",
    "name_warns_once": "(() => { const a = S.resolve({name: 'Sol Ring'}, {onWarn: m => WARNS.push(m)});"
                       " S.cache.clear(); S.resolve({name: 'Sol Ring'}, {onWarn: m => WARNS.push(m)});"
                       " return WARNS.length; })()",
    "name_console_warn": "(() => { S.config.warn = true; const seen = []; const orig = console.warn; console.warn = m => seen.push(m);"
                         " try { S.resolve({name: 'Thassa'}); } finally { console.warn = orig; }"
                         " return seen; })()",
    "printing_specific_no_warn": "(() => { S.resolve({scryfallId: SID}, {onWarn: m => WARNS.push(m)});"
                                 " S.resolve({set: 'cmm', collectorNumber: '1'}, {onWarn: m => WARNS.push(m)});"
                                 " return WARNS.length; })()",
    "empty_ref": "[S.resolve({}), S.resolve()]",
    # ---- DFC -----------------------------------------------------------------
    "dfc_front": "S.resolve({scryfallId: SID, cardFaces: DELVER_FACES})",
    "dfc_back": "S.resolve({scryfallId: SID, cardFaces: DELVER_FACES, face: 'back'})",
    "dfc_snake_case": "S.resolve({scryfall_id: SID, card_faces: DELVER_FACES})",
    "dfc_large": "S.resolve({cardFaces: DELVER_FACES, version: 'large'})",
    "dfc_top_level_wins": "S.resolve({imageUris: SOL_URIS, cardFaces: DELVER_FACES})",
    "dfc_api_faces": "[S.resolve({scryfallId: SID, layout: 'transform', "
                     "cardFaces: [{name: 'Front'}, {name: 'Back'}]}),"
                     " S.resolve({set: 'isd', collectorNumber: '51', layout: 'modal_dfc', face: 'back', "
                     "cardFaces: [{name: 'Front'}, {name: 'Back'}]})]",
    "split_card_single_image": "S.resolve({scryfallId: SID, layout: 'split', "
                               "cardFaces: [{name: 'Fire'}, {name: 'Ice'}]})",
    "dfc_partial_images_ignored": "S.resolve({scryfallId: SID, cardFaces: [DELVER_FACES[0], {name: 'Back'}]})",
    # ---- cache ---------------------------------------------------------------
    "cache_same_object": "(() => { const a = S.resolve({scryfallId: SID}); const b = S.resolve({scryfallId: SID});"
                         " return { same: a === b, stats: S.cache.stats() }; })()",
    "cache_key_precedence": "[S.cacheKey({scryfallId: SID, set: 'cmm', collectorNumber: '1', name: 'x'}),"
                            " S.cacheKey({set: 'CMM', collectorNumber: '1', name: 'x'}),"
                            " S.cacheKey({name: 'Sol Ring'}), S.cacheKey({}),"
                            " S.cacheKey({scryfallId: SID, face: 'back'})]",
    "cache_serves_ref_without_image_uris": "(() => { S.resolve({scryfallId: SID, imageUris: SOL_URIS});"
                                           " return S.resolve({scryfallId: SID}); })()",
    "cache_per_version": "[S.resolve({scryfallId: SID, imageUris: SOL_URIS}, {version: 'small'}).url,"
                         " S.resolve({scryfallId: SID}, {version: 'large'}).url,"
                         " S.resolve({scryfallId: SID, imageUris: SOL_URIS}).url]",
    "cache_per_face": "[S.resolve({scryfallId: SID, cardFaces: DELVER_FACES, face: 'front'}).url,"
                      " S.resolve({scryfallId: SID, cardFaces: DELVER_FACES, face: 'back'}).url]",
    "cache_two_printings_differ": "[S.resolve({scryfallId: SID}).url, S.resolve({scryfallId: SID2}).url]",
    "cache_lru": "(() => { const c = S.createCache(2); c.set('a', 1); c.set('b', 2); c.get('a'); c.set('c', 3);"
                 " return { keys: c.keys(), size: c.size, has_b: c.has('b'), stats: c.stats() }; })()",
    "cache_exported": "({ cache: typeof S.cache.get + typeof S.cache.set + typeof S.cache.clear,"
                      " throttle: typeof S.throttle.run, cfg: S.config.maxRequestsPerSecond })",
}

HARNESS = r"""
const fs = require('fs');
const path = process.argv[1];
const SCEN = JSON.parse(process.argv[2]);
const K = JSON.parse(process.argv[3]);
const SID = K.SID, SID2 = K.SID2, CDN = K.CDN, SOL_URIS = K.SOL_URIS, DELVER_FACES = K.DELVER_FACES;
function fresh() {
  delete require.cache[require.resolve(path)];
  const S = require(path);
  S.config.warn = false;
  return S;
}
const out = {};
for (const name of Object.keys(SCEN)) {
  const S = fresh();
  const WARNS = [];
  try { out[name] = { value: eval(SCEN[name]), warns: WARNS }; }
  catch (e) { out[name] = { error: String(e && e.stack || e) }; }
}

// ---- async: throttle + throttled JSON lookup -----------------------------------
(async () => {
  const S = fresh();
  // Fake clock throttle: 25 calls at t=0 must be spread over >= 2.4 s (10/s).
  let clock = 0; const sleeps = []; const starts = [];
  const th = S.createThrottle(10, {
    now: () => clock,
    setTimeout: (fn, ms) => { sleeps.push(ms); setImmediate(() => { clock += ms; fn(); }); },
  });
  await Promise.all(Array.from({ length: 25 }, (_, i) => th.run(() => { starts.push(clock); return i; })));
  out.throttle = { interval: th.interval, starts,
                   minGap: Math.min(...starts.slice(1).map((t, i) => t - starts[i])),
                   perSecondMax: Math.max(...starts.map(s => starts.filter(t => t >= s && t < s + 1000).length)) };

  // resolveRemote: set/number -> JSON lookup -> real CDN url, cached under both keys.
  const calls = [];
  const card = { id: SID, set: 'cmm', collector_number: '123', name: 'Sol Ring', layout: 'normal', image_uris: SOL_URIS };
  const f = (url, init) => { calls.push({ url, headers: init.headers }); return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(card) }); };
  const r1 = await S.resolveRemote({ set: 'CMM', collectorNumber: '123' }, { fetch: f });
  const r2 = await S.resolveRemote({ set: 'CMM', collectorNumber: '123' }, { fetch: f });
  const r3 = await S.resolveRemote({ scryfallId: SID }, { fetch: f });
  out.remote = { r1, same: r1 === r2, r3, calls };

  // DFC lookup by id returns both faces
  const dfc = { id: SID2, set: 'isd', collector_number: '51', name: 'Delver', layout: 'transform', card_faces: DELVER_FACES };
  const S2 = fresh(); const calls2 = [];
  const r4 = await S2.resolveRemote({ scryfallId: SID2, face: 'back' },
      { fetch: (u, i) => { calls2.push(u); return Promise.resolve({ ok: true, json: () => Promise.resolve(dfc) }); } });
  out.remoteDfc = { r4, calls: calls2 };

  // Failure falls back to the synchronous API URL; no fetch when image_uris already known.
  const S3 = fresh();
  const bad = await S3.resolveRemote({ set: 'cmm', collectorNumber: '9' }, { fetch: () => Promise.resolve({ ok: false, status: 404 }) });
  let fetched = 0;
  const known = await S3.resolveRemote({ scryfallId: SID, imageUris: SOL_URIS }, { fetch: () => { fetched++; return Promise.reject(new Error('no')); } });
  const nameRef = await S3.resolveRemote({ name: 'Sol Ring' }, { fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve(card) }), onWarn: () => {} });
  out.remoteEdge = { bad, known, fetched, nameRef };
  out.headers = { node: S3.requestHeaders(), img: S3.requestHeaders('*/*'), ua: S3.userAgent() };
  out.emptyFetch = await S3.fetchCard({}).then(() => 'resolved', e => String(e.message));
  process.stdout.write(JSON.stringify(out));
})();
"""


@unittest.skipUnless(shutil.which("node"), "node not installed: scryfall_image.js tests need it")
class TestScryfallImage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        consts = {"SID": SID, "SID2": "11111111-2222-3333-4444-555555555555", "CDN": CDN,
                  "SOL_URIS": SOL_URIS, "DELVER_FACES": DELVER_FACES}
        res = subprocess.run(["node", "-e", HARNESS, str(SI_JS), json.dumps(SCENARIOS), json.dumps(consts)],
                             capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        cls.out = json.loads(res.stdout)

    def v(self, name):
        entry = self.out[name]
        self.assertNotIn("error", entry, entry.get("error"))
        return entry["value"]

    # ---- id path -------------------------------------------------------------
    def test_id_with_image_uris_uses_cdn_normal(self):
        self.assertEqual(self.v("id_with_image_uris"), {"url": SOL_URIS["normal"], "printingSpecific": True})

    def test_image_uris_prefers_normal_over_other_sizes(self):
        self.assertEqual(self.v("id_prefers_normal")["url"], "n")

    def test_image_uris_without_normal_falls_back_to_next_size(self):
        r = self.v("id_uris_without_normal")
        self.assertEqual(r["url"], CDN + "/large/x.jpg")
        self.assertTrue(r["printingSpecific"])

    def test_id_api_fallback(self):
        self.assertEqual(self.v("id_api_fallback"),
                         {"url": f"https://api.scryfall.com/cards/{SID}?format=image&version=normal",
                          "printingSpecific": True})

    def test_id_accepts_snake_case_uid_and_lowercases(self):
        for r in self.v("id_snake_case_and_uid"):
            self.assertEqual(r["url"], f"https://api.scryfall.com/cards/{SID}?format=image&version=normal")

    def test_id_back_face_appends_face_back(self):
        self.assertEqual(self.v("id_back_face_api")["url"],
                         f"https://api.scryfall.com/cards/{SID}?format=image&version=normal&face=back")

    def test_id_wins_over_set_and_number(self):
        self.assertIn(SID, self.v("id_beats_set_number")["url"])

    def test_dpr_selects_version(self):
        self.assertEqual(self.v("dpr")[:3], ["normal", "normal", "large"])
        self.assertIn("version=large", self.v("dpr")[3]["url"])

    # ---- set + collector number ----------------------------------------------
    def test_set_number_lowercases_set(self):
        self.assertEqual(self.v("set_number"),
                         {"url": "https://api.scryfall.com/cards/cmm/123?format=image&version=normal",
                          "printingSpecific": True})

    def test_set_number_encodes_star(self):
        self.assertEqual(self.v("set_number_star")["url"],
                         "https://api.scryfall.com/cards/plst/123%E2%98%85?format=image&version=normal")

    def test_set_number_encodes_reserved_characters(self):
        self.assertEqual(self.v("set_number_symbols")["url"],
                         "https://api.scryfall.com/cards/sld/1%2F2%20a%23b%3F?format=image&version=normal")

    def test_set_number_back_face(self):
        self.assertTrue(self.v("set_number_back")["url"].endswith(
            "/cards/isd/51?format=image&version=normal&face=back"))

    def test_set_without_collector_number_falls_to_name(self):
        r = self.v("set_without_number_is_name")
        self.assertFalse(r["printingSpecific"])
        self.assertIn("/cards/named?exact=Sol%20Ring", r["url"])

    # ---- name fallback --------------------------------------------------------
    def test_name_only_is_not_printing_specific(self):
        e = self.out["name_only"]
        self.assertEqual(e["value"], {
            "url": "https://api.scryfall.com/cards/named?exact=Jace%2C%20the%20Mind%20Sculptor"
                   "&format=image&version=normal",
            "printingSpecific": False})
        self.assertEqual(len(e["warns"]), 1)
        self.assertIn("Jace, the Mind Sculptor", e["warns"][0])

    def test_name_is_url_encoded(self):
        self.assertIn("exact=Sol%20Ring%20%26%20Co%2FFire%20%2F%2F%20Ice&format=image",
                      self.v("name_encoding")["url"])

    def test_name_warning_emitted_once_per_name(self):
        self.assertEqual(self.v("name_warns_once"), 1)

    def test_name_warning_defaults_to_console_warn(self):
        seen = self.v("name_console_warn")
        self.assertEqual(len(seen), 1)
        self.assertIn("Thassa", seen[0])

    def test_printing_specific_refs_never_warn(self):
        self.assertEqual(self.v("printing_specific_no_warn"), 0)

    def test_empty_ref_returns_empty_url(self):
        for r in self.v("empty_ref"):
            self.assertEqual(r, {"url": "", "printingSpecific": False})

    # ---- DFC ------------------------------------------------------------------
    def test_dfc_returns_one_url_per_face(self):
        r = self.v("dfc_front")
        self.assertEqual(r["faces"], [{"url": DELVER_FACES[0]["image_uris"]["normal"], "name": "Delver of Secrets"},
                                      {"url": DELVER_FACES[1]["image_uris"]["normal"], "name": "Insectile Aberration"}])
        self.assertEqual(r["url"], r["faces"][0]["url"])
        self.assertTrue(r["printingSpecific"])

    def test_dfc_back_selects_second_face_but_keeps_both(self):
        r = self.v("dfc_back")
        self.assertEqual(r["url"], DELVER_FACES[1]["image_uris"]["normal"])
        self.assertEqual(len(r["faces"]), 2)

    def test_dfc_snake_case_payload(self):
        self.assertEqual(len(self.v("dfc_snake_case")["faces"]), 2)

    def test_dfc_honours_version(self):
        self.assertEqual([f["url"] for f in self.v("dfc_large")["faces"]],
                         [f["image_uris"]["large"] for f in DELVER_FACES])

    def test_top_level_image_uris_wins_over_card_faces(self):
        r = self.v("dfc_top_level_wins")
        self.assertEqual(r["url"], SOL_URIS["normal"])
        self.assertNotIn("faces", r)

    def test_dfc_without_images_gets_api_url_per_face(self):
        by_id, by_set = self.v("dfc_api_faces")
        base = f"https://api.scryfall.com/cards/{SID}?format=image&version=normal"
        self.assertEqual(by_id["faces"], [{"url": base, "name": "Front"},
                                          {"url": base + "&face=back", "name": "Back"}])
        self.assertEqual(by_id["url"], base)
        self.assertEqual(by_set["url"], "https://api.scryfall.com/cards/isd/51?format=image&version=normal&face=back")
        self.assertEqual(len(by_set["faces"]), 2)

    def test_split_card_is_a_single_image(self):
        r = self.v("split_card_single_image")
        self.assertNotIn("faces", r)
        self.assertEqual(r["url"], f"https://api.scryfall.com/cards/{SID}?format=image&version=normal")

    def test_partial_dfc_images_are_ignored(self):
        r = self.v("dfc_partial_images_ignored")
        self.assertNotIn("faces", r)
        self.assertTrue(r["url"].startswith("https://api.scryfall.com/cards/"))

    # ---- cache ----------------------------------------------------------------
    def test_repeat_resolve_hits_cache(self):
        v = self.v("cache_same_object")
        self.assertTrue(v["same"])
        self.assertEqual(v["stats"]["hits"], 1)
        self.assertEqual(v["stats"]["size"], 1)

    def test_cache_key_prefers_id_then_set_number_then_name(self):
        k = self.v("cache_key_precedence")
        self.assertEqual(k[0], f"id:{SID}|normal|front")
        self.assertEqual(k[1], "sn:cmm/1|normal|front")
        self.assertEqual(k[2], "name:sol ring|normal|front")
        self.assertEqual(k[3], "")
        self.assertEqual(k[4], f"id:{SID}|normal|back")

    def test_later_ref_without_image_uris_gets_cached_cdn_url(self):
        self.assertEqual(self.v("cache_serves_ref_without_image_uris")["url"], SOL_URIS["normal"])

    def test_cache_is_per_version_and_per_face(self):
        small, large, normal = self.v("cache_per_version")
        self.assertEqual(small, SOL_URIS["small"])
        self.assertIn("version=large", large)
        self.assertEqual(normal, SOL_URIS["normal"])
        front, back = self.v("cache_per_face")
        self.assertNotEqual(front, back)

    def test_two_printings_resolve_to_different_urls(self):
        a, b = self.v("cache_two_printings_differ")
        self.assertNotEqual(a, b)

    def test_cache_is_lru_bounded(self):
        v = self.v("cache_lru")
        self.assertEqual(v["keys"], ["a", "c"])
        self.assertEqual(v["size"], 2)
        self.assertFalse(v["has_b"])

    def test_cache_and_throttle_are_exported(self):
        v = self.v("cache_exported")
        self.assertEqual(v["cache"], "functionfunctionfunction")
        self.assertEqual(v["throttle"], "function")
        self.assertEqual(v["cfg"], 10)

    # ---- throttle + lookups ---------------------------------------------------
    def test_throttle_caps_at_ten_requests_per_second(self):
        t = self.out["throttle"]
        self.assertEqual(t["interval"], 100)
        self.assertGreaterEqual(t["minGap"], 100)
        self.assertLessEqual(t["perSecondMax"], 10)
        self.assertEqual(len(t["starts"]), 25)

    def test_resolve_remote_looks_up_set_number_once_then_caches(self):
        r = self.out["remote"]
        self.assertEqual(r["r1"], {"url": SOL_URIS["normal"], "printingSpecific": True})
        self.assertTrue(r["same"])
        # The lookup also primed the id key, so the id-only ref needed no request.
        self.assertEqual(r["r3"]["url"], SOL_URIS["normal"])
        self.assertEqual([c["url"] for c in r["calls"]], ["https://api.scryfall.com/cards/cmm/123"])

    def test_requests_send_accept_and_user_agent(self):
        h = self.out["headers"]
        self.assertEqual(h["node"]["Accept"], "application/json")
        self.assertEqual(h["img"]["Accept"], "*/*")
        self.assertEqual(h["node"]["User-Agent"], h["ua"])
        self.assertRegex(h["ua"], r"^CommanderLab/\S+ \(.+\)$")
        call = self.out["remote"]["calls"][0]
        self.assertEqual(call["headers"]["Accept"], "application/json")
        self.assertIn("CommanderLab", call["headers"]["User-Agent"])

    def test_resolve_remote_returns_dfc_faces_and_back(self):
        r = self.out["remoteDfc"]
        self.assertEqual(r["calls"], [f"https://api.scryfall.com/cards/11111111-2222-3333-4444-555555555555"])
        self.assertEqual(r["r4"]["url"], DELVER_FACES[1]["image_uris"]["normal"])
        self.assertEqual(len(r["r4"]["faces"]), 2)

    def test_resolve_remote_failure_falls_back_and_skips_known_images(self):
        e = self.out["remoteEdge"]
        self.assertEqual(e["bad"]["url"], "https://api.scryfall.com/cards/cmm/9?format=image&version=normal")
        self.assertEqual(e["fetched"], 0)
        self.assertEqual(e["known"]["url"], SOL_URIS["normal"])
        self.assertFalse(e["nameRef"]["printingSpecific"])  # name lookups never claim a printing

    def test_fetch_card_rejects_empty_ref(self):
        self.assertIn("empty card reference", self.out["emptyFetch"])


if __name__ == "__main__":
    unittest.main()
