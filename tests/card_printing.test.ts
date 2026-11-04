// node --test tests/card_printing.test.ts   (a11y card-page printing switcher)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, test } from "node:test";
import { printingHref } from "../src/web/src/lib/card-printing.ts";

const OID = "7a1f0f5c-0c3e-4d2b-9a77-1b2c3d4e5f60";
const SID = "4c0ef2f1-7a0e-4d6b-9a1b-4d2d4f2a9c11";
const page = readFileSync(new URL("../src/web/src/routes/card/[oracle_id]/+page.svelte", import.meta.url), "utf8");
const review = readFileSync(new URL("../src/web/src/routes/decks/[id]/review/+page.svelte", import.meta.url), "utf8");

describe("printingHref", () => {
  test("pins the dossier to the chosen printing", () => {
    assert.equal(printingHref(OID, SID), `/card/${OID}?printing=${SID}`);
  });
  test("encodes both segments", () => {
    assert.equal(printingHref("a/b c", "x&y=1"), "/card/a%2Fb%20c?printing=x%26y%3D1");
  });
});

describe("printing switcher navigation", () => {
  test("uses the router's goto, not a full page load", () => {
    assert.match(page, /import \{ goto \} from '\$app\/navigation'/);
    assert.match(page, /goto\(printingHref\(card\.oracle_id, selectedId\), \{ reset: false \}\)/);
    assert.doesNotMatch(page, /window\.location/);
  });
  test("review history select also navigates client-side", () => {
    assert.match(review, /goto\(`\?doc=/);
    assert.doesNotMatch(review, /window\.location/);
  });
  test("elapsed timer is outside the polite live region", () => {
    const live = review.match(/<div role="status"[^>]*>([\s\S]*?)<\/div>/);
    assert.ok(live, "live region present");
    assert.doesNotMatch(live[1], /elapsed/);
    assert.match(review, /aria-hidden="true" data-testid="review-elapsed"/);
  });
});
