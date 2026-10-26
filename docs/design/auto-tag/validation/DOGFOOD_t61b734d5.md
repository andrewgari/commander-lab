# Auto Tag: end-to-end verification and dogfood report (t_61b734d5)

Build under test: `main` @ b032c14 (engine PR #175, UI PR #177). Engine spec: `docs/auto-tag-spec.md` (autotag-v1).

## Setup

- Data: a read-only `--rdb` snapshot of the prod `commander-lab-redis` on Tower, loaded into a scratch `redis:alpine` on `localhost:6470`. Nothing was written to prod.
- App: `uvicorn app:app` on `127.0.0.1:8150` (`INTERACTIONS_DISABLED=1`), Playwright 1.64 / Chromium.
- Tag writes from the dogfood runs landed only in the scratch Redis.

## 1. Test suite

| Run | Result |
|---|---|
| `unittest test_autotag_engine test_auto_tag_ui` | **30 / 30 pass** |
| `unittest discover -s tests` (full) | 2551 run, 16 failing. None are auto-tag. See below. |
| `pytest tests/e2e/test_auto_tag_e2e.py` (new, x3) | **5 pass, 2 strict xfail** (stable) when run on b032c14; after #178 (D1 fix) it is 6 pass, 1 xfail |

Full-suite failures, both pre-existing and environmental:
- 13 errors in `test_repo_status`: the fixture runs `git commit`, which the tester profile's global `pre-commit` hook (`~/.config/git/hooks`) blocks by design. They should pass under `coder`.
- 3 failures in `test_deck_stats_ui`: the "Tune" tab regex in the test doesn't match the current tab markup on main. Unrelated to Auto Tag.

## 2. Playwright E2E (`tests/e2e/test_auto_tag_e2e.py`)

| Test | Status |
|---|---|
| Run Auto Tag: card chips (`Ramp · auto`, `Removal · auto`) and deck tag (`Spellslinger`) render, ≤3 chips per tile | pass |
| Basic lands stay untagged | pass |
| Manual deck tag (`Voltron`) and a pinned card tag (Sol Ring `Ramp · yours`) survive a re-run; the confirm dialog appears | pass |
| User deck tag survives page reload | pass |
| Filter by `ramp` shows only matching tiles, URL `?tag=ramp`, Clear restores all | pass |
| Untagged filter shows only untagged tiles | Was xfail on b032c14 (17 of 17 shown); fixed by #178 (t_64b7938d), xfail marker removed, now passes |
| numeric vs registry deck id address the same tags | xfail (strict), see issue D3. Fails as expected |

Each test seeds its own deck (`decks` record, `instance:*` in_deck records, and tag keys), then cleans up. Expectations were checked against the live engine, not guessed.

## 3. Dogfood decks

Each deck was run through `POST /api/decks/{registry_id}/auto-tag`, then read back through `GET .../tags` and `.../card-tags`. Oracle text came from the snapshot's `card_interactions`. Every card with tags was reviewed.

| Deck | Strategy | Deck tags | Tagged cards | Verdict |
|---|---|---|---|---|
| My Big, Fat, Dead Wedding (Edgar Markov, 417233) | Vampire tokens | `counters`, `kindred:vampire` | 55 / 88 | **Missing `tokens`**, the core theme |
| I cast, therefore I am (Vivi, 15373575) | Spellslinger | `spellslinger` | 61 / 84 | Good; some gaps |
| Just who the hell do you think I am!? (Shorikai, 7901595) | Artifacts / vehicles | `artifacts`, `tokens` | 61 / 86 | Good; 2 false `tokens` |
| Adventure After Elevensies (Sam & Frodo, 4816260) | Halflings / Food / lifegain | `kindred:halfling`, `tokens`, `lifegain`, `sacrifice` | 61 / 89 | Good; health hint correct (interaction 7 < floor 10) |
| Ancient Oathkeeper (Galea, 1754844) | Voltron (auras / equipment) | `voltron` | 52 / 88 | Good theme; **missed ramp / removal auras** |

Automated checks on all 5 decks:
- Per-card cap respected: max 3 tags on nonland cards, max 2 on lands.
- Deck tags ≤ 5 on every deck.
- No cards with more than 3 tags.

Treasure-maker context works: Storm-Kiln Artist, An Offer You Can't Refuse, Academy Manufactor, Reckoner Bankbuster (Treasure) → `ramp`.

## 4. Issues

Severity: H = wrong deck theme or a missed core role, M = wrong card tag that a builder would notice, L = low.

### Over-tagging (false positives)

| # | Deck | Card | Actual | Expected | Reason |
|---|---|---|---|---|---|
| O1 | Adventure After Elevensies | Essence Warden | `tokens`, `lifegain` | `lifegain` | "Whenever another creature enters, you gain 1 life" matches TK-2 (creature-enters trigger). Not a token payoff. |
| O2 | Shorikai | Intruder Alarm | `tokens` | none | "Whenever a creature enters, untap all creatures" matches TK-2. Not a token payoff. |
| O3 | Batteries Not Included | Tempered Steel; Chief of the Foundry | `tokens` | `artifacts` (or none) | Artifact-creature anthem matches TK-4. Anthems only support tokens when tokens is active. Here it's an artifact deck. |
| O4 | Galea | Danitha Capashen, Paragon; Jukai Naturalist; Hero of Iroas | `ramp` | none (or weak) | Cost reducers are RAMP-4 (w=2). This follows spec §3, but a voltron deck gets ramp from cost reducers. **Design call needed.** |
| O5 | Edgar Markov | Voldaren Estate | `kindred:vampire` | `tokens` (Blood) / none | Land. Kindred tag is loose, and it doesn't use Blood tokens. L |
| O6 | Galea | Imprisoned in the Moon | `voltron`, `ramp` | `removal` | Aura that makes the creature a colorless land. Ramp is a side effect, not the role. |

Note: a keyword sweep also flagged Chaos Warp → `removal`. That's correct (it shuffles the permanent away), so it is not an issue.

### Missed core roles and themes

| # | Deck | Card | Actual | Expected | Reason |
|---|---|---|---|---|---|
| M1 | Edgar Markov | Anointed Procession | none | `tokens` | Token doubler. Core to a tokens deck. |
| M2 | Edgar Markov | Mavren Fein, Dusk Apostle | `kindred:vampire` | `tokens` | Creates a Vampire token on attack. |
| M3 | Edgar Markov | Elenda, the Dusk Rose | `counters`, `kindred:vampire` | `tokens` | Makes tokens on death. |
| M4 | Edgar Markov (deck) | deck tags | `counters`, `kindred:vampire` | `kindred:vampire`, `tokens` | Deck theme wrong. `tokens` isn't derived even with three clear token makers (M1–M3). Fixing M1–M3 likely fixes this. **H** |
| M5 | Edgar Markov | Olivia's Wrath | `kindred:vampire` | `board_wipe` | Each non-Vampire creature gets -X/-X, a mass removal. |
| M6 | Galea | Utopia Sprawl | `voltron` | `ramp` | Aura that doubles Forest mana. Abundant Growth gets `ramp`; this doesn't. **M** |
| M7 | Galea | Kenrith's Transformation | `voltron` | `removal`, `card_advantage` | Aura that turns the creature into a 3/3 Elk and draws. **H** (no removal aura in a voltron deck) |
| M8 | I cast, therefore I am | Ponder | `spellslinger` | `card_advantage` | Preordain is tagged `card_advantage`. Ponder isn't. Inconsistent. |
| M9 | I cast, therefore I am | Expressive Iteration | `spellslinger` | `card_advantage` | Card selection plus a playable exile. |
| M10 | I cast, therefore I am | Grapeshot | `spellslinger` | `finisher` | Storm burn. |
| M11 | Edgar Markov | Olivia Voldaren | `counters`, `kindred:vampire` | + `removal` | Repeatable 1-damage ping. L |

### Bugs

| # | Where | Repro | Expected | Actual |
|---|---|---|---|---|
| D1 | Untagged filter (UI) | Deck with tags → click "Untagged" | Only untagged tiles | No-op (17 of 17 shown). **Fixed in #178 (t_64b7938d).** |
| D2 | Commander showcase (UI) | Tag filter `?tag=ramp` | Commander tiles hidden too | Commander tile stays visible. The grid count ("6 of 100") is correct. L |
| D3 | Tag API (ids) | `POST /api/decks/417707/auto-tag` then `GET /api/decks/archidekt:417707/tags` | Same tags | Numeric route writes `deck_tags:417707`; the registry id reads `deck_tags:archidekt:417707`. Result: `[]`. The UI always uses the registry id, so the UI is consistent; API callers can split a deck's tag store. Strict xfail in `TestApiIdentity`. **M** |

### Already known (not re-verified here)
Keep-as-yours is DELETE then POST, so a failed POST drops the auto tag. Re-run can bring back a removed auto tag (no tombstone). Both are in the t_374aa594 handoff.

## 5. What's left for coder

1. Commit `tests/e2e/test_auto_tag_e2e.py`, the `.github/workflows/ci.yml` step, and this report. The worktree is detached at `origin/main`. The branch `wt/t_61b734d5` still has 3 unmerged commits not on main, which I didn't touch.
2. File follow-ups for O1–O4, O6, M1–M7 and D3. See the cards I created.
3. D1 is fixed (#178) and its xfail removed. Once D3 lands, remove the remaining `xfail` on `TestApiIdentity`.
