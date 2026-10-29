// node --test tests/drafts_commit_flow.test.ts   (drafts commit flow: UI/UX audit page-specs §2)
import assert from "node:assert/strict";
import { describe, test } from "node:test";
import {
  BOARD_ORDER,
  groupByBoard,
  nextQty,
  stagedQty,
  stagedQtyByOracle,
  toApiBoard,
} from "../src/web/src/lib/drafts/boards.ts";
import {
  abandonSentence,
  deckName,
  donorSentence,
  moveRows,
  summarize,
  type SummaryCard,
  type SummaryMove,
} from "../src/web/src/lib/drafts/summary.ts";
import { describeCommitFailure } from "../src/web/src/lib/drafts/errors.ts";
import { pickExactMatch } from "../src/web/src/lib/drafts/match.ts";
import {
  buildRows,
  countAnnouncement,
  enterTarget,
  moveHighlight,
  nameSegments,
  ownedLabel,
  ownedSuggestUrl,
} from "../src/web/src/lib/card-search/combobox.ts";
import type { CardHit } from "../src/web/src/lib/card-search/types.ts";

const card = (over: Partial<SummaryCard> & { name: string }): SummaryCard => ({
  oracle_id: `oid-${over.name}`,
  qty: 1,
  board: "main",
  color_identity: [],
  ...over,
});
const move = (over: Partial<SummaryMove> & { card_name: string }): SummaryMove => ({
  instance_id: `inst-${Math.random()}`,
  action: "assign",
  from_deck: null,
  oracle_id: `oid-${over.card_name}`,
  status: "in_collection",
  deck_id: null,
  ...over,
});
const hit = (name: string, over: Partial<CardHit> = {}): CardHit => ({
  oracle_id: `oid-${name}`,
  name,
  mana_cost: "{1}",
  cmc: 1,
  type_line: "Artifact",
  color_identity: [],
  rarity: "uncommon",
  oracle_text: "",
  image: name,
  owned_qty: 4,
  free_qty: 1,
  can_be_commander: false,
  pairing: null,
  ...over,
});

describe("boards", () => {
  test("maybeboard maps to the API's `maybe`; all five boards round-trip", () => {
    assert.equal(toApiBoard("maybeboard"), "maybe");
    for (const b of BOARD_ORDER) assert.equal(toApiBoard(b), b);
    assert.equal(toApiBoard("nonsense"), null);
  });
  test("nextQty adds to the existing qty (upsert sets, it does not add)", () => {
    assert.equal(nextQty(0, 2), 2);
    assert.equal(nextQty(1, 2), 3);
    assert.equal(nextQty(1, 0), 2); // a request below 1 still adds one
  });
  test("stagedQty looks up by card and API board (accepts maybeboard)", () => {
    const cards = [{ oracle_id: "a", board: "maybe", qty: 2 }, { oracle_id: "a", board: "main", qty: 1 }];
    assert.equal(stagedQty(cards, "a", "maybeboard"), 2);
    assert.equal(stagedQty(cards, "a", "main"), 1);
    assert.equal(stagedQty(cards, "b", "main"), 0);
    assert.equal(stagedQtyByOracle(cards).get("a"), 3);
  });
  test("groupByBoard orders boards, omits empty ones, keeps unknown boards last", () => {
    const rows = [
      { board: "main", qty: 3, n: 1 },
      { board: "commander", qty: 1, n: 2 },
      { board: "weird", qty: 2, n: 3 },
      { board: "maybe", qty: 1, n: 4 },
    ];
    const groups = groupByBoard(rows);
    assert.deepEqual(groups.map((g) => g.board), ["commander", "main", "maybe", "weird"]);
    assert.deepEqual(groups.map((g) => g.label), ["Commander", "Main", "Maybeboard", "Weird"]);
    assert.equal(groups[1].qty, 3);
  });
});

describe("summarize", () => {
  const draft = { target_name: "Atraxa Superfriends" };

  test("boards, commanders and identity union in WUBRG order", () => {
    const s = summarize({
      draft,
      cards: [
        card({ name: "Atraxa", board: "commander", color_identity: ["G", "W", "U", "B"] }),
        card({ name: "Sol Ring", qty: 1 }),
        card({ name: "Forest", qty: 97, color_identity: ["G"] }),
        card({ name: "Doom Blade", board: "maybe", qty: 4, color_identity: ["B"] }),
      ],
      moves: [],
    });
    assert.equal(s.newDeckName, "Atraxa Superfriends");
    assert.equal(s.totalCards, 103);
    assert.equal(s.cardsLine, "103 cards: 1 commander · 98 main · 4 maybe");
    assert.deepEqual(s.commanders, ["Atraxa"]);
    assert.deepEqual(s.identity, ["W", "U", "B", "G"]);
    assert.equal(s.blockedReason, null);
  });

  test("a 100-card commander + main deck has no size warning", () => {
    const s = summarize({
      draft,
      cards: [card({ name: "Atraxa", board: "commander" }), card({ name: "Forest", qty: 99 })],
      moves: [],
    });
    assert.deepEqual(s.warnings, []);
  });

  test("warns when there is no commander and when the count is not 100", () => {
    const s = summarize({ draft, cards: [card({ name: "Forest", qty: 60 })], moves: [] });
    assert.deepEqual(s.warnings, [
      "No commander staged.",
      "Commander decks have 100 cards; this one will have 60.",
    ]);
  });

  test("an empty draft is blocked with a reason and does not warn about size", () => {
    const s = summarize({ draft, cards: [], moves: [] });
    assert.equal(s.blockedReason, "Stage at least one card to commit.");
    assert.equal(s.cardsLine, "0 cards");
    assert.deepEqual(s.warnings, []);
  });

  test("expected status mirrors plan_commit: any assign -> Built (have), else Virtual", () => {
    const base = { draft, cards: [card({ name: "A" })] };
    assert.equal(summarize({ ...base, moves: [] }).expectedStatus, "virtual");
    assert.equal(summarize({ ...base, moves: [move({ card_name: "A", action: "unassign", status: "in_deck" })] }).expectedStatus, "virtual");
    const have = summarize({ ...base, moves: [move({ card_name: "A" })] });
    assert.equal(have.expectedStatus, "have");
    assert.equal(have.expectedStatusLabel, "Built (have)");
  });

  test("move counts and the moves sentence", () => {
    const s = summarize({
      draft,
      cards: [card({ name: "A" })],
      moves: [move({ card_name: "A" }), move({ card_name: "B" }), move({ card_name: "C", action: "unassign", status: "in_deck", deck_id: "d1" })],
    });
    assert.equal(s.movesLine, "2 copies move into the new deck · 1 returns to the collection");
    assert.equal(summarize({ draft, cards: [card({ name: "A" })], moves: [] }).movesLine, null);
  });

  test("donor pulls group assigns of in-deck copies by donor deck name, never by id", () => {
    const names = new Map([["deck-sliver", "Sliver Swarm"], ["deck-elf", "Elves"]]);
    const s = summarize(
      {
        draft,
        cards: [card({ name: "A" })],
        moves: [
          move({ card_name: "Sliver Queen", status: "in_deck", deck_id: "deck-sliver", from_deck: "deck-sliver" }),
          move({ card_name: "Hive Stirrings", status: "in_deck", deck_id: "deck-sliver", from_deck: "deck-sliver" }),
          move({ card_name: "Metallic Sliver", status: "in_deck", deck_id: "deck-sliver", from_deck: "deck-sliver" }),
          move({ card_name: "Llanowar Elves", status: "in_deck", deck_id: "deck-elf", from_deck: "deck-elf" }),
          move({ card_name: "Sol Ring", status: "in_collection" }), // free copy: no donor
        ],
      },
      names,
    );
    assert.deepEqual(s.donors.map((d) => [d.deckName, d.count]), [["Sliver Swarm", 3], ["Elves", 1]]);
    assert.equal(s.donorTotal, 4);
    assert.equal(donorSentence(s.donors), "Pulls 3 copies out of Sliver Swarm, 1 out of Elves");
    assert.ok(s.warnings.some((w) => w.startsWith("Sliver Swarm will lose these copies")));
    assert.ok(s.warnings.some((w) => w.startsWith("Elves will lose this copy")));
    assert.ok(!JSON.stringify(s).includes("deck-sliver") || s.donors[0].deckId === "deck-sliver"); // ids stay internal
  });

  test("an unassign is not a donor pull", () => {
    const s = summarize({ draft, cards: [card({ name: "A" })], moves: [move({ card_name: "A", action: "unassign", status: "in_deck", deck_id: "d1" })] });
    assert.equal(s.donors.length, 0);
    assert.equal(donorSentence(s.donors), null);
  });

  test("when deck names are unavailable the donor is called 'another deck', not the id", () => {
    const s = summarize({ draft, cards: [card({ name: "A" })], moves: [move({ card_name: "X", status: "in_deck", deck_id: "draft:secret-id" })] }, null);
    assert.equal(s.donors[0].deckName, "another deck");
    assert.ok(!s.warnings.join(" ").includes("secret-id"));
    assert.equal(deckName("x", new Map()), "another deck");
  });
});

describe("moveRows / abandon", () => {
  test("rows carry names and states but disambiguate duplicates with an ordinal", () => {
    const names = new Map([["d1", "Sliver Swarm"]]);
    const rows = moveRows(
      [
        move({ instance_id: "i1", card_name: "Sol Ring", oracle_id: "sr", from_deck: "d1", status: "in_deck", deck_id: "d1" }),
        move({ instance_id: "i2", card_name: "Sol Ring", oracle_id: "sr", from_deck: "d1", status: "in_deck", deck_id: "d1" }),
        move({ instance_id: "i3", card_name: "Command Tower", oracle_id: "ct", action: "unassign", status: "in_mail" }),
      ],
      names,
    );
    assert.deepEqual(rows.map((r) => r.displayName), ["Sol Ring (copy 1)", "Sol Ring (copy 2)", "Command Tower"]);
    assert.equal(rows[0].from, "Sliver Swarm");
    assert.equal(rows[0].state, "In Sliver Swarm");
    assert.equal(rows[0].moveLabel, "Into new deck");
    assert.equal(rows[2].from, "Collection");
    assert.equal(rows[2].moveLabel, "Back to collection");
    assert.equal(rows[2].state, "In the mail");
  });
  test("abandon sentence counts staged cards (by qty) and moves", () => {
    assert.equal(abandonSentence([{ qty: 99 }, { qty: 1 }], [1, 2, 3]), "Deletes 100 staged cards and 3 staged moves. Your decks and collection are untouched.");
    assert.equal(abandonSentence([{ qty: 1 }], [1]), "Deletes 1 staged card and 1 staged move. Your decks and collection are untouched.");
  });
});

describe("commit failure", () => {
  test("keeps the server's reason and offers refresh for changed-world errors", () => {
    assert.deepEqual(describeCommitFailure(409, "draft is committed, not open"), { message: "draft is committed, not open", stale: true });
    assert.equal(describeCommitFailure(400, "instance not found").stale, true);
    assert.equal(describeCommitFailure(503, "db_unavailable").stale, false);
    assert.equal(describeCommitFailure(undefined, "").message, "The server rejected the commit.");
  });
});

describe("no-JS add by name", () => {
  test("only an exact, case-insensitive name match is accepted", () => {
    const results = [{ oracle_id: "1", name: "Sol Ring" }, { oracle_id: "2", name: "Sol Ring Tribute" }];
    assert.equal(pickExactMatch(results, "sol ring")?.oracle_id, "1");
    assert.equal(pickExactMatch(results, "  SOL   RING ")?.oracle_id, "1");
    assert.equal(pickExactMatch(results, "Sol"), null);
    assert.equal(pickExactMatch(results, ""), null);
    assert.equal(pickExactMatch([{ oracle_id: "3", name: "Fire // Ice" }], "fire")?.oracle_id, "3");
  });
});

describe("combobox rows", () => {
  const base = { term: "sol", results: [] as CardHit[], degraded: false, showSearching: false, errorDetail: "" };

  test("owned suggestions come from the owned local search, never autocomplete", () => {
    const url = ownedSuggestUrl("Sol Ring");
    assert.ok(url.startsWith("/api/card-search?"));
    const p = new URLSearchParams(url.split("?")[1]);
    assert.equal(p.get("owned"), "owned");
    assert.equal(p.get("name"), "Sol Ring");
    assert.equal(p.get("page_size"), "8");
  });

  test("the four non-result rows are distinct and an error is never 'no matches'", () => {
    assert.deepEqual(buildRows({ ...base, phase: "loading", showSearching: true }).map((r) => r.kind), ["searching"]);
    assert.deepEqual(buildRows({ ...base, phase: "loading", showSearching: false }), []); // 300 ms flicker guard
    assert.deepEqual(buildRows({ ...base, phase: "ready" }).map((r) => r.kind), ["empty"]);
    const err = buildRows({ ...base, phase: "error", errorDetail: "502 from card search" });
    assert.deepEqual(err.map((r) => r.kind), ["error"]);
    assert.ok(!err.some((r) => r.kind === "empty"));
    assert.deepEqual(buildRows({ ...base, phase: "ready", degraded: true, results: [hit("Sol Ring")] }).map((r) => r.kind), ["degraded", "card"]);
  });

  test("previous results stay while the next request is in flight", () => {
    const rows = buildRows({ ...base, phase: "loading", results: [hit("Sol Ring")] });
    assert.deepEqual(rows.map((r) => r.kind), ["card"]);
  });

  test("↓/↑ wrap and skip non-option rows; Enter acts on the highlight or the top row", () => {
    const rows = buildRows({ ...base, phase: "ready", degraded: true, results: [hit("A"), hit("B"), hit("C")] });
    // rows: degraded, A, B, C
    assert.equal(moveHighlight(rows, -1, 1), 1);
    assert.equal(moveHighlight(rows, 1, 1), 2);
    assert.equal(moveHighlight(rows, 3, 1), 1); // wraps
    assert.equal(moveHighlight(rows, -1, -1), 3);
    assert.equal(moveHighlight(rows, 1, -1), 3); // wraps backwards
    const top = enterTarget(rows, -1);
    assert.equal(top?.kind === "card" && top.hit.name, "A");
    const picked = enterTarget(rows, 3);
    assert.equal(picked?.kind === "card" && picked.hit.name, "C");
    assert.equal(moveHighlight([], -1, 1), -1);
    assert.equal(enterTarget([], -1), null);
  });

  test("Enter on the error row is the retry action", () => {
    const rows = buildRows({ ...base, phase: "error", errorDetail: "x" });
    assert.equal(enterTarget(rows, -1)?.kind, "error");
  });

  test("name segments emphasise the match and split faces at ' // '", () => {
    assert.deepEqual(nameSegments("Sol Ring", "ol r"), [
      { text: "S", match: false }, { text: "ol R", match: true }, { text: "ing", match: false },
    ]);
    assert.deepEqual(nameSegments("Fire // Ice", "zzz").map((s) => s.text), ["Fire", "//", "Ice"]);
    assert.equal(nameSegments("Fire // Ice", "zzz")[1].sep, true);
    assert.deepEqual(nameSegments("Sol Ring", ""), [{ text: "Sol Ring", match: false }]);
  });

  test("labels", () => {
    assert.equal(ownedLabel({ owned_qty: 4, free_qty: 1 }), "Own 4 · 1 free");
    assert.equal(countAnnouncement(5), "5 owned matches");
    assert.equal(countAnnouncement(1), "1 owned match");
    assert.equal(countAnnouncement(0), "No owned matches");
  });
});
