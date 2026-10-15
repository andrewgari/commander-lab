# PS-5 · Designer review: live ability scores (deck page + /profile)

Reviewer: designer · Live build: v0.4.43 (eb3254f) on `http://192.168.50.3:8020` · 2026-10-15
Against: `docs/design/personality/UX_SPEC.md` (PS-D, branch `74632eb`, reference only)
Widths: 1440 and 390 · Data: real, 39 physical / 57 all decks · Deck: The Laws of Time are Mine!! (Doctor / Rose)

## Verdict: PASS, with 2 small fixes (this PR) and a fix list

The build matches the spec. Every state I could reach on live data renders the way the spec shows it,
and every interaction does what the spec says.

## What I checked (live, measured in the DOM, not by eye)

| Area | Spec | Live | |
|---|---|---|---|
| Panel placement | hero main column, under `#deckStats` | yes; same column (560–1238 vs 577–1221 inside the panel padding) | ✓ |
| Close lean (Doctor/Rose) | dim tile + `2nd` | `hud-ps-b is-close`, Melvin `is-lean`, Vorthos `is-runner`, "Close second Vorthos, 3 points behind." | ✓ |
| Clear lean (Celes) | solid tile | "Leans Spike · 45 points clear of Johnny." | ✓ |
| Balanced, bunched (Bird UP!!!!) | no signal | "Balanced · Johnny, Spike and Vorthos are within 7 points." | ✓ |
| Balanced, low (Wild Things) | "Nothing stands out" | "Top score is Johnny 34, below 35 on every axis." | ✓ |
| Journal nudges (Holy Hellfire, Wild Things) | `+8` / `−4` / `±0` per tile | yes | ✓ |
| Evidence drawer | every signal listed, zeros included; `points / weight`; card chips; `+N more` | M = 5 rows + journal row; S lists S6 0.0 / 15; sum "cards 78.6 ≈ 79" | ✓ |
| Keys (deck page) | `T J S V M`, arrows, `Esc` | all work; Esc closes the drawer and puts focus back on the Melvin tile | ✓ |
| Keys (/profile) | `T…M`, `B`, `0`, `/`, `Esc` | J → 9 decks, B → 4, 0 → 39, `/` focuses the filter, Esc clears it | ✓ |
| Callout actions | act on the table | all 6 filter/sort actions land on the table (count + `aria-sort`); open → deck | ✓ |
| Strip plot | hover lights a deck on every axis; click jumps to its row | tooltip shows "Deck: Timmy 51"; 5 dots get `is-hot`; click scrolls to the row and focuses its link | ✓ |
| Scope toggle | Physical 39 / All 57 | `?scope=all` → "mostly Timmy, with a lot of Spike", 57 rows | ✓ |
| Heat-cell contrast | ≥ 4.5:1 | levels 0–5: 8.7, 11.0, 7.6, 4.9, 5.0, 7.5 | ✓ |
| Text contrast | AA | scripted scan of every text node in `main`: no node under 4.5:1 | ✓ |
| Reduced motion | everything off | callout `animation: none`, meter `transition 0s` | ✓ |
| No sideways scroll | both widths | `scrollWidth == clientWidth` at 1440 and 390 | ✓ |
| Phone (390) | tiles restack; table → card list; lean seg scrolls | 39 `.hud-ps-card`, table hidden, seg `overflow-x:auto` (535 / 292) | ✓ |

## Fixed in this PR (small, CSS / template only)

1. **Runner-up isn't marked in the heat cells** (flagged by PS-4). The chip said "Melvin · V" but only
   the Melvin cell was ringed. The runner-up cell now gets a **1px dashed `--text-1` ring**: neutral,
   quieter than the solid 2px signal ring, so signal still means the lean and nothing else (spec §2.2).
   It's applied to both the table and the phone card list. `--text-1` rather than `--text-2` because
   `--text-2` on `--data-4` is about 2:1, which fails the 3:1 non-text minimum.
2. **The "All" filter tooltip read "All decks (0)"**, which looks like a count of zero. The `(0)` is
   the keyboard shortcut. All the seg tooltips now read "Leans Johnny · key J", "All decks · key 0".

Test: `tests/test_personality_ui.py::test_profile_full` asserts the Doctor/Rose runner cell, the 5
runner cells on physical, and the new tooltip.

## Fix list (bigger than a CSS tweak, or copy owned by PS-3)

| # | Where | Issue | Suggested fix | Owner |
|---|---|---|---|---|
| F1 | /profile trends, group callout | Reads "**67** avg / avg **Vorthos** in your 11 tribal decks…": the word "avg" twice. | PS-3 text: drop the leading "avg" ("**Vorthos** in your 11 tribal decks, vs 22 in the rest."), or set the unit to "avg Vorthos" | coder (PS-3 copy) |
| F2 | /profile trends, outlier | "“Critically Acclaimed Commander…” **ties 1 other** as your most Vorthos deck" never names the other deck (Ezio, also 96). The truncated name also reads oddly mid-sentence. | Either name both decks ("“Critically…” and “Everything is permitted.” tie at 96…") or drop the tie clause | coder (PS-3 copy) |
| F3 | /profile strip plot | Dots can sit on the average tick (PS-4 note). The tick is z-index 2 with a 1px dark outline, so it stays visible, but at 0.55 dot opacity a stack right on the tick still muddies it. | Optional: raise the tick to `--ps-strip-h` full height plus 2px, or punch a 2px `--surface-1` gap either side of it | designer, later |
| F4 | /profile trends | The lead callout's indicator bar is per spec but carries no label, so it reads like a stuck hover. | Optional: add a "Headline" micro-label, or drop `is-lead` | designer, low priority |

Nothing in this list blocks shipping.

## Do the scores feel right? (judged against real decks)

Most leans pass the sniff test. Celes is Spike 90, Kaalia Timmy 73, Ezio and the Fourteenth Doctor
are Vorthos 96, Yuriko is Melvin, and Adventure After Elevensies is Johnny 95. **Five flags**, each with
the deck and axis. These are scoring observations; I did not change scoring.

| # | Deck | Axis | Looks off because | Evidence (live `/api/decks/{id}/personality`) |
|---|---|---|---|---|
| S-1 | **The world is slowly ebbing away…** (Celes), **We're totally gonna own you lamers** (Seifer), **Come to me, my noble Armiger!** (Noctis), **The most handome guy here.** (Squall) | **Vorthos** 4 / 7 / 7 / 13 | Four Final Fantasy character decks score near zero on "the deck tells a story", while Cloud gets 62 with the same kind of build. **V1's set theme counts only the single top set code**, and FF cards are split across `FIN` + `FIC` (+ `FCA`), so every FF deck falls below the 0.30 ramp floor (`set FIC 9/49`, `set FIN 10/58`). | V1 set part = 0 on all four; Cloud only clears it at `FIC 28/60` |
| S-2 | All decks | **Spike** (S1) | `power_level_estimate` is 6.5 on 33 of 39 physical decks, so S1 contributes a near-constant 3.75 / 10. This is known (PERSONALITY_SPEC §S1), but on live data it is **still** a dead signal. | S1 values: 6.5 ×33, 7.5 ×4, 7.0, 6.0 |
| S-3 | **The world is slowly ebbing away…** (Celes) 90, **Rumors of my death…** (Saffi) 80 | **Spike** and **Johnny** | One Spellbook count drives both J1 (25 / 25) and S6 (15 / 15): Celes has 31 included combos, which also lifts Johnny to 45. Big combo counts on these reanimator / blink shells are mostly incidental 2–3 card loops, so they double-dip into two axes. | J1 `31=25/25` and S6 `31=15/15` on Celes; Saffi `30` / `30` |
| S-4 | **Bird UP!!!!** (Choco) | Balanced vs **Johnny** 70 | Johnny 70 / Spike 65 / Vorthos 63 is "Balanced", which is correct by the §8 rule. But a deck that is high on three axes reading as "Balanced" sounds like *mild*. The copy is fine ("within 7 points"); the **word** is the issue. | — |
| S-5 | **Straight Outta Mirkwood** (Lord of the Nazgûl) | **Vorthos** 29 | A Nazgûl / LotR deck at 29 Vorthos is low; V2 and V4 are thin. It's also the one deck flagged ▲ "EDHREC unavailable", so treat it as low confidence. | V1 `LTR 13/46` = 17 |

**Recommendations** (for whoever owns PS-1/PS-2 scoring; not for this card):

- S-1: let V1's set part treat a **set family** (main set + its commander set, e.g. `FIN`+`FIC`,
  `LTR`+`LTC`, `WHO`) as one set. This is the biggest correctness win and moves four physical decks.
- S-3: have S6 count only combos that are also **cheap or fast**, or cap the shared J1/S6 contribution.
- S-4: consider renaming the bunched case "Even split" or "Well-rounded" on the deck page (UI copy;
  can be a designer card).

## Not done / deliberately skipped

- Didn't press **Score now** / **Retry** on prod (they queue real rescores); the tests cover them.
- Didn't re-shoot every state from the PS-D spec. Loading, error and empty only appear transiently on
  live, and PS-4's node tests render them from real payloads.
- No scoring code touched (card non-goal).

Screenshots (live, headless Chromium): `docs/screenshots/personality/ps5/`.
