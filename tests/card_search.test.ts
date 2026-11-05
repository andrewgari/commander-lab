// node --test tests/card_search.test.ts   (docs/ui/card-search.md §8, CS-2)
import assert from "node:assert/strict";
import { afterEach, describe, test } from "node:test";
import {
  activeFilterCount,
  emptyFilters,
  fromParams,
  isEmpty,
  normalizeFilters,
  searchUrl,
  suggestUrl,
  toggleColor,
  toParams,
} from "../src/web/src/lib/card-search/query.ts";
import { clearFilters, PRESETS, resolveConfig } from "../src/web/src/lib/card-search/presets.ts";
import { autocompleteTerm, parseQuickAdd } from "../src/web/src/lib/card-search/quickAdd.ts";
import type { Filters } from "../src/web/src/lib/card-search/types.ts";

const FIRST = "4c0ef2f1-7a0e-4d6b-9a1b-4d2d4f2a9c11";

const full: Filters = {
  name: "Atraxa",
  ci: ["W", "U", "B", "G"],
  ci_mode: "includes",
  type: ["creature", "artifact"],
  type_text: "legendary elf",
  mv: ["1", "2", "7+"],
  rarity: ["rare", "mythic"],
  oracle: 'draw a card "can be your commander"',
  owned: "not_owned",
  commander: true,
  pair: FIRST,
};

describe("Filters <-> params", () => {
  test("empty filters have no params", () => {
    assert.equal(toParams(emptyFilters()).toString(), "");
    assert.deepEqual(fromParams(""), emptyFilters());
  });

  test("every filter round-trips", () => {
    assert.deepEqual(fromParams(toParams(full)), full);
    assert.deepEqual(fromParams(toParams(full).toString()), full);
  });

  test("each filter on its own round-trips", () => {
    const base = emptyFilters();
    const singles: Partial<Filters>[] = [
      { name: "sol ring" },
      { ci: ["C"] },
      { ci: ["R", "G"], ci_mode: "exact" },
      { ci_mode: "exact" },
      { type: ["land"] },
      { type_text: "elf" },
      { mv: ["0"] },
      { mv: ["7+"] },
      { rarity: ["common"] },
      { oracle: "\"draw a card\" flying" },
      { owned: "owned" },
      { commander: true },
      { commander: true, pair: FIRST },
    ];
    for (const s of singles) {
      const f = { ...base, ...s };
      assert.deepEqual(fromParams(toParams(f)), f, JSON.stringify(s));
    }
  });

  test("wire format matches the §3 table", () => {
    const p = toParams(full);
    assert.equal(p.get("ci"), "wubg");
    assert.equal(p.get("ci_mode"), "includes");
    assert.equal(p.get("type"), "creature,artifact");
    assert.equal(p.get("mv"), "1,2,7+");
    assert.equal(p.get("rarity"), "rare,mythic");
    assert.equal(p.get("owned"), "not_owned");
    assert.equal(p.get("cmdr"), "1");
    assert.equal(p.get("pair"), FIRST);
    // Defaults are omitted.
    const d = toParams({ ...emptyFilters(), name: "x" });
    assert.deepEqual([...d.keys()], ["name"]);
  });

  test("cmdr is absent when off and 1 when on", () => {
    assert.equal(toParams({ ...emptyFilters(), commander: false }).has("cmdr"), false);
    assert.equal(toParams({ ...emptyFilters(), commander: true }).get("cmdr"), "1");
    assert.equal(fromParams("cmdr=0").commander, false);
    assert.equal(fromParams("cmdr=true").commander, false);
    assert.equal(fromParams("cmdr=1").commander, true);
  });

  test("pair needs cmdr: it is dropped otherwise (the server 400s on it)", () => {
    const f = { ...emptyFilters(), pair: FIRST, commander: false };
    assert.equal(toParams(f).has("pair"), false);
    assert.equal(normalizeFilters(f).pair, null);
    assert.equal(fromParams(`pair=${FIRST}`).pair, null);
    assert.equal(fromParams(`cmdr=1&pair=${FIRST}`).pair, FIRST);
  });

  test("lists are canonical regardless of input order and case", () => {
    const a = toParams({ ...emptyFilters(), ci: ["G", "W", "U"], type: ["land", "creature"], mv: ["7+", "1"] });
    assert.equal(a.get("ci"), "wug");
    assert.equal(a.get("type"), "creature,land");
    assert.equal(a.get("mv"), "1,7+");
    assert.deepEqual(fromParams("ci=GWU&rarity=Mythic,COMMON").ci, ["W", "U", "G"]);
    assert.deepEqual(fromParams("rarity=Mythic,COMMON").rarity, ["common", "mythic"]);
  });

  test("unknown and invalid values are ignored, duplicates collapse", () => {
    const f = fromParams("ci=wxz&ci_mode=bogus&type=creature,dragon,creature&mv=9,2&owned=maybe&rarity=rare,epic&junk=1");
    assert.deepEqual(f.ci, ["W"]);
    assert.equal(f.ci_mode, "within");
    assert.deepEqual(f.type, ["creature"]);
    assert.deepEqual(f.mv, ["2"]);
    assert.equal(f.owned, "any");
    assert.deepEqual(f.rarity, ["rare"]);
  });

  test("colorless is exclusive with colors", () => {
    assert.deepEqual(fromParams("ci=cw").ci, ["W"]);
    assert.deepEqual(toggleColor(["W", "U"], "C"), ["C"]);
    assert.deepEqual(toggleColor(["C"], "R"), ["R"]);
    assert.deepEqual(toggleColor(["W"], "U"), ["W", "U"]);
    assert.deepEqual(toggleColor(["W", "U"], "W"), ["U"]);
    assert.deepEqual(toggleColor(["C"], "C"), []);
  });

  test("text is trimmed and special characters survive the URL", () => {
    const f = { ...emptyFilters(), name: "Jace, the Mind Sculptor", oracle: "a & b + c=d" };
    const round = fromParams(toParams(f).toString());
    assert.equal(round.name, "Jace, the Mind Sculptor");
    assert.equal(round.oracle, "a & b + c=d");
    assert.equal(normalizeFilters({ name: "  x  " }).name, "x");
  });

  test("isEmpty and activeFilterCount", () => {
    assert.equal(isEmpty(emptyFilters()), true);
    assert.equal(isEmpty({ ci_mode: "exact" }), true, "a match mode alone is not a filter");
    assert.equal(isEmpty({ owned: "owned" }), false);
    assert.equal(isEmpty({ commander: true }), false);
    assert.equal(activeFilterCount(emptyFilters()), 0);
    assert.equal(activeFilterCount(full), 8, "type + type_text is one filter, pair is a modifier");
    assert.equal(activeFilterCount({ commander: true, ci_mode: "exact" }), 1);
  });

  test("searchUrl adds paging and targets the proxy route", () => {
    assert.equal(searchUrl(emptyFilters()), "/api/card-search");
    const url = new URL(searchUrl({ ...emptyFilters(), name: "a b" }, { page: 2, page_size: 60 }), "http://x");
    assert.equal(url.pathname, "/api/card-search");
    assert.equal(url.searchParams.get("page"), "2");
    assert.equal(url.searchParams.get("page_size"), "60");
    assert.equal(url.searchParams.get("name"), "a b");
    assert.equal(fromParams(url.searchParams).name, "a b", "paging params do not leak into filters");
  });
});

describe("quick-add suggestions", () => {
  test("normal scope uses autocomplete", () => {
    const u = new URL(suggestUrl("sol ri"), "http://x");
    assert.equal(u.pathname, "/api/card-autocomplete");
    assert.equal(u.searchParams.get("q"), "sol ri");
    assert.equal(u.searchParams.get("limit"), "8");
    assert.equal(u.searchParams.has("commanders_only"), false);
  });

  test("commander scope adds commanders_only", () => {
    const u = new URL(suggestUrl("atr", { commander: true }), "http://x");
    assert.equal(u.pathname, "/api/card-autocomplete");
    assert.equal(u.searchParams.get("commanders_only"), "true");
  });

  test("with a first commander it switches to card-search with cmdr + pair", () => {
    const u = new URL(suggestUrl("ak", { commander: true, pair: FIRST }), "http://x");
    assert.equal(u.pathname, "/api/card-search");
    assert.equal(u.searchParams.get("cmdr"), "1");
    assert.equal(u.searchParams.get("pair"), FIRST);
    assert.equal(u.searchParams.get("name"), "ak");
    assert.equal(u.searchParams.get("page_size"), "8");
  });

  test("pair without the commander chip is not sent", () => {
    const u = new URL(suggestUrl("ak", { pair: FIRST }), "http://x");
    assert.equal(u.pathname, "/api/card-autocomplete");
  });
});

describe("quick-add quantity grammar", () => {
  const ok = (input: string, qty: number, name: string) =>
    assert.deepEqual(parseQuickAdd(input), { ok: true, qty, name }, JSON.stringify(input));

  test("accepted forms", () => {
    ok("2 Sol Ring", 2, "Sol Ring");
    ok("2x Sol Ring", 2, "Sol Ring");
    ok("2X Sol Ring", 2, "Sol Ring");
    ok("2 x Sol Ring", 2, "Sol Ring");
    ok("2 X Sol Ring", 2, "Sol Ring");
    ok("12  x   Sol Ring", 12, "Sol Ring");
    ok("Sol Ring", 1, "Sol Ring");
    ok("  Sol Ring  ", 1, "Sol Ring");
  });

  test("bare name is qty 1, including names that merely contain digits or x", () => {
    ok("Xenagos, God of Revels", 1, "Xenagos, God of Revels");
    ok("2xSol Ring", 1, "2xSol Ring");
    ok("Sol  Ring", 1, "Sol Ring");
  });

  test("0 is rejected, in every spelling", () => {
    for (const s of ["0 Sol Ring", "0x Sol Ring", "0X Sol Ring", "0 x Sol Ring", "00 Sol Ring"]) {
      assert.deepEqual(parseQuickAdd(s), { ok: false, reason: "bad_qty" }, s);
    }
  });

  test("quantities past the safe-integer range are rejected", () => {
    assert.deepEqual(parseQuickAdd("99999999999999999999 Sol Ring"), { ok: false, reason: "bad_qty" });
  });

  test("empty, quantity-only and multi-line input add nothing", () => {
    assert.deepEqual(parseQuickAdd(""), { ok: false, reason: "empty" });
    assert.deepEqual(parseQuickAdd("   "), { ok: false, reason: "empty" });
    assert.deepEqual(parseQuickAdd("2x"), { ok: false, reason: "empty" });
    assert.deepEqual(parseQuickAdd("2 x "), { ok: false, reason: "empty" });
    assert.deepEqual(parseQuickAdd("1 Sol Ring\n1 Mana Crypt"), { ok: false, reason: "multiline" });
    assert.deepEqual(parseQuickAdd("Sol Ring\r\nMana Crypt"), { ok: false, reason: "multiline" });
  });

  test("fixedQty: 1 reads and drops the prefix instead of adding twice", () => {
    assert.deepEqual(parseQuickAdd("2x Atraxa", { fixedQty: 1 }), { ok: true, qty: 1, name: "Atraxa" });
    assert.deepEqual(parseQuickAdd("Atraxa", { fixedQty: 1 }), { ok: true, qty: 1, name: "Atraxa" });
    assert.deepEqual(parseQuickAdd("0 Atraxa", { fixedQty: 1 }), { ok: false, reason: "bad_qty" });
  });

  test("autocompleteTerm sends only the name part and never throws mid-typing", () => {
    assert.equal(autocompleteTerm("2x Sol Ri"), "Sol Ri");
    assert.equal(autocompleteTerm("Sol Ri"), "Sol Ri");
    assert.equal(autocompleteTerm("2x"), "");
    assert.equal(autocompleteTerm("2 "), "");
    assert.equal(autocompleteTerm("0 Sol"), "Sol");
    assert.equal(autocompleteTerm(""), "");
  });
});

describe("presets", () => {
  test("no preset: cmdr stays off and nothing is restricted", () => {
    const c = resolveConfig({ boards: ["main", "sideboard"] });
    assert.equal(c.filters.commander, false);
    assert.equal(toParams(c.filters).has("cmdr"), false);
    assert.equal(c.filters.ci_mode, "within");
    assert.deepEqual(c.boards, ["main", "sideboard"]);
    assert.equal(c.placeholder, "Add cards to main…");
    assert.equal(c.fixedQty, undefined);
    assert.equal(c.promoteFilter, undefined);
    assert.equal(isEmpty(c.filters), true);
  });

  test("commander preset: cmdr=1, ci_mode=exact, boards ['commander']", () => {
    const c = resolveConfig({ preset: "commander" });
    assert.equal(c.filters.commander, true);
    assert.equal(c.filters.ci_mode, "exact");
    assert.deepEqual(c.boards, ["commander"]);
    assert.equal(c.fixedQty, 1);
    assert.equal(c.promoteFilter, "commander");
    assert.equal(c.placeholder, "Search commanders…");
    const p = toParams(c.filters);
    assert.equal(p.get("cmdr"), "1");
    assert.equal(p.get("ci_mode"), "exact");
    assert.deepEqual(PRESETS.commander.boards, ["commander"]);
  });

  test("explicit props override the preset", () => {
    const c = resolveConfig({
      preset: "commander",
      boards: ["main"],
      placeholder: "Add a partner…",
      filters: { ci_mode: "within", commander: false },
    });
    assert.deepEqual(c.boards, ["main"]);
    assert.equal(c.placeholder, "Add a partner…");
    assert.equal(c.filters.ci_mode, "within");
    assert.equal(c.filters.commander, false);
    // Untouched preset keys still apply.
    assert.equal(c.fixedQty, 1);
  });

  test("identity and ownedDefault are explicit props and win over the preset", () => {
    const c = resolveConfig({ boards: ["main"], identity: ["W", "U", "B"], ownedDefault: "owned" });
    assert.deepEqual(c.filters.ci, ["W", "U", "B"]);
    assert.equal(c.filters.ci_mode, "within");
    assert.equal(c.filters.owned, "owned");
    const mixed = resolveConfig({ preset: "commander", identity: ["G"] });
    assert.equal(mixed.filters.ci_mode, "within");
    assert.equal(mixed.filters.commander, true);
  });

  test("Clear filters under the preset keeps cmdr=1", () => {
    const c = resolveConfig({ preset: "commander" });
    const dirty = { ...c.filters, name: "atraxa", ci: ["B" as const], owned: "owned" as const };
    assert.equal(activeFilterCount(dirty) > 1, true);
    const cleared = clearFilters(c);
    assert.equal(cleared.commander, true);
    assert.equal(cleared.ci_mode, "exact");
    assert.equal(cleared.name, "");
    assert.deepEqual(cleared.ci, []);
    assert.equal(cleared.owned, "any");
    assert.equal(toParams(cleared).get("cmdr"), "1");
  });

  test("Clear filters with no preset clears everything", () => {
    const c = resolveConfig({ boards: ["main"], identity: ["W"], ownedDefault: "owned" });
    assert.deepEqual(clearFilters(c), emptyFilters());
  });

  test("pairWith: cmdr + pair are forced on and locked, under the preset only", () => {
    const first = { oracle_id: FIRST };
    const c = resolveConfig({ preset: "commander", pairWith: first, placeholder: "Add a partner…" });
    assert.equal(c.filters.commander, true);
    assert.equal(c.filters.pair, FIRST);
    assert.equal(c.commanderLocked, true);
    const p = toParams(c.filters);
    assert.equal(p.get("cmdr"), "1");
    assert.equal(p.get("pair"), FIRST);
    // Clear filters keeps the pairing clause: the host owns it.
    assert.equal(clearFilters(c).pair, FIRST);

    const ignored = resolveConfig({ boards: ["main"], pairWith: first });
    assert.equal(ignored.filters.pair, null);
    assert.equal(ignored.filters.commander, false);
    assert.equal(ignored.commanderLocked, false);
  });
});

describe("proxy routes", () => {
  const realFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  const API = "http://api.test:8000";
  const load = async () => {
    const { searchProxy, autocompleteProxy } = await import("../src/web/src/lib/card-search/proxy.server.ts");
    return {
      search: (url: URL) => searchProxy(url, API),
      auto: (url: URL) => autocompleteProxy(url, API),
    };
  };

  test("card-search forwards allowed params to /api/v2/cards/search and drops the rest", async () => {
    const { search } = await load();
    let seen = "";
    globalThis.fetch = (async (u: string) => {
      seen = String(u);
      return new Response(JSON.stringify({ results: [], page: 1, has_more: false, total: 0 }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }) as unknown as typeof fetch;
    const res = await search(new URL(`http://app/api/card-search?name=sol&cmdr=1&pair=${FIRST}&page=2&evil=1&q=x`));
    assert.equal(res.status, 200);
    assert.equal(res.headers.get("cache-control"), "no-store");
    const up = new URL(seen);
    assert.equal(up.origin, API);
    assert.equal(up.pathname, "/api/v2/cards/search");
    assert.equal(up.searchParams.get("name"), "sol");
    assert.equal(up.searchParams.get("cmdr"), "1");
    assert.equal(up.searchParams.get("pair"), FIRST);
    assert.equal(up.searchParams.get("page"), "2");
    assert.equal(up.searchParams.has("evil"), false);
    assert.equal(up.searchParams.has("q"), false);
    assert.deepEqual(await res.json(), { results: [], page: 1, has_more: false, total: 0 });
  });

  test("card-autocomplete forwards q, limit, commanders_only to /api/cards/autocomplete", async () => {
    const { auto } = await load();
    let seen = "";
    globalThis.fetch = (async (u: string) => {
      seen = String(u);
      return new Response(JSON.stringify({ results: [], source: "cache", degraded: false }), { status: 200 });
    }) as unknown as typeof fetch;
    const res = await auto(new URL("http://app/api/card-autocomplete?q=atr&limit=8&commanders_only=true&name=no"));
    assert.equal(res.status, 200);
    const up = new URL(seen);
    assert.equal(up.pathname, "/api/cards/autocomplete");
    assert.equal(up.searchParams.get("q"), "atr");
    assert.equal(up.searchParams.get("limit"), "8");
    assert.equal(up.searchParams.get("commanders_only"), "true");
    assert.equal(up.searchParams.has("name"), false);
  });

  test("an upstream 400 reaches the page with its body", async () => {
    const { search } = await load();
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ detail: "This commander can't have a partner" }), {
        status: 400,
        headers: { "content-type": "application/json" },
      })) as unknown as typeof fetch;
    const res = await search(new URL(`http://app/api/card-search?cmdr=1&pair=${FIRST}`));
    assert.equal(res.status, 400);
    assert.deepEqual(await res.json(), { detail: "This commander can't have a partner" });
  });

  test("unreachable or slow API is a 5xx, never an empty 200", async () => {
    const { search, auto } = await load();
    globalThis.fetch = (async () => {
      throw new TypeError("fetch failed");
    }) as unknown as typeof fetch;
    const down = await search(new URL("http://app/api/card-search?name=x"));
    assert.equal(down.status, 502);
    assert.match((await down.json()).detail, /unreachable/);

    globalThis.fetch = (async () => {
      throw new DOMException("The operation timed out.", "TimeoutError");
    }) as unknown as typeof fetch;
    const slow = await auto(new URL("http://app/api/card-autocomplete?q=ab"));
    assert.equal(slow.status, 504);
  });
});
