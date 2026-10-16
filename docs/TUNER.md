# Tuner (AN-6)

Spec: `docs/ANALYTICS_SPEC.md` Layer 3 and `docs/design/analytics/UX_SPEC.md` §3.4.
Code: `analytics/tuner.py`, `templates/deck_tune.html`, the Tune helpers in
`static/js/deck_analytics.js`. Tests: `tests/test_tuner.py` (rules),
`tests/test_deck_tune_ui.py` (page, API, JS helpers).

## What it shows

`/deck/{name}/tune` (tab 5; the check-in's **Finish → Tune** lands here):

1. **Shape fixes.** Composition rows that are 2 or more short of the template
   (never Plan). Each fix names owned cards: free ones first, then cards
   sleeved in another deck. The action is `Add N lands` for Lands (basics in
   the deck's colours, added to the change list) or `See N owned` for any
   other row. That button opens chips you can add, and it filters the swaps
   to cuts in that row.
2. **Swaps.** Weak performer → best owned card / best buy. See the rules below.
3. **Weak performers.** The AN-3 table, with pets marked ♥ and never cut.
4. **Change list.** Accepted swaps plus extra adds, Spend (buys only; owned
   cards cost $0), the projected On-plan, and physical moves ("Pull X from
   deck Y"). It offers **Copy as text** only. There is no Archidekt write
   API yet, so Apply is hidden (UX_SPEC §3.4: "don't show it disabled").

Keys: `j`/`k` move, `a` accept, `b` buy instead, `x` dismiss, `h` mark the
cut as a pet (it is added to the Journal's pets). Keys never fire while
you're typing. The 1–5 tab keys still work.

## Scores (deterministic)

The LLM never produces a number. A card's score in this deck is the mean of
the parts that exist, each 0..1:

| part   | value | dropped when |
|---|---|---|
| edges  | `min(1, plan edges between the card and the deck / p75)`; a "plan edge" has at least one reason other than mana/lands; p75 = the deck's 75th-percentile plan degree (the same p75 the weak-performer score uses) | never |
| fit    | best pillar fit (local bge-small embeddings, same scale as Stats/Identity) | the deck has no live pillars |
| edhrec | `min(1, inclusion / 0.50)` on the commander's EDHREC page; not listed = 0 | EDHREC unavailable |
| played | card2vec played-with score (AN-7, `docs/CARD2VEC.md`): how often real Commander decklists play the card with the rest of this deck, 0 = a random card, 1 = clearly belongs. When on, every card has it; a card the model doesn't know gets the deck's median (neutral) | no artifact, or too few of the deck's cards are in the model |

A cut is scored with the same formula, so "gap" always compares like with like.

## Swap rules

- **Cuts**: weak performers, weakest first. Never pets, lands or commanders.
  At most 12 swaps.
- **Candidates**: every owned card (in the box, or sleeved in another deck
  when "Include sleeved-elsewhere" is on) plus every card on the commander's
  EDHREC page. They must be inside the colour identity and not already in
  this deck. A buy needs a Scryfall USD price (an unpriced card can't be
  bought). A card sleeved in another deck also has to pass the steal rule
  below.
- **Role**: a cut in a functional row (ramp, card advantage, targeted or
  mass disruption) is only replaced by a card in one of the same rows. A
  Plan cut takes any non-land card. Role-bound cuts pick first, so a Plan
  cut can't take the only removal spell. Each card is suggested once.
- **Upgrade**: an option has to beat the cut by at least `MIN_UPGRADE`
  (0.05).
- **Owned first (D5)**: a buy is shown only when
  `buy.score − best_owned.score ≥ GAP_WORTH` (0.10). Otherwise the row
  shows the near buy: "Keep yours: +0.08 isn't worth $39.58 (Ancient Brass
  Dragon)."
- **Soft cap**: the buy is shown with `over soft cap $X` and a
  justification line. The numbers in that line are filled in by the code;
  only its reason sentence comes from the LLM. The pick stays owned unless
  the gap is at least `GAP_SOFT_OVERRIDE` (0.25).
- **Hard cap**: never shown as an option. It is only counted: "1 better buy
  hidden · over hard cap $40".
- **Modes**: Owned first (default) · Owned only (no buys) · Best regardless
  (the higher score wins, caps still apply).
- **Pet notice (D7)**: one quiet row for the weakest pet.

## Steal rule (AN-8)

Taking X from another deck B for this deck A is only suggested when B gets a
replacement Y and the trade is good for both decks:

- **Y** is one of: an owned card in no deck (`available > 0`), the card A is
  cutting for X, or a basic land in B's colours (basics are assumed owned and
  are exempt from singleton; Wastes only for a colourless deck).
- **Legal in B**: inside B's colour identity, not already in B, and it keeps
  X's job in B (the same role rule as A's cuts: a land for a land, removal
  for removal, any non-land for a plan card).
- **Never pulled**: B's pets and B's commander.
- **Scores**: A's score must improve (`receiver_delta` = X's score in A
  minus the cut's, always ≥ `MIN_UPGRADE`). B is scored with the same
  `score()` but B's own inputs: B's graph edges and p75, B's pillars, B's
  commander's EDHREC page (cached only, never fetched for B) and card2vec
  against B. The trade is shown only when `score_B(Y) ≥ score_B(X)`
  (`donor_delta ≥ 0`, so a fair 1-for-1 is fine). The best Y wins (ties:
  free card, then A's cut, then a basic). Price never matters for Y: it is
  owned, so it costs $0.
- **No fair Y**: the sleeved card is dropped and the next-best option takes
  its place (a free card, another sleeved card that passes, or a buy).
  `counts.steals_dropped` says how many were dropped.
- A free card promised to one donor deck isn't offered again, either as
  another donor's Y or as an add for A.
- One hop only: A takes from B; B never takes from C. When X is sleeved in
  several decks, the deck with the best `donor_delta` is the donor.
- Shape fixes list a sleeved-elsewhere card only when it passes the same
  check, e.g. "Minas Tirith (in Adventure After Elevensies; it gets Forest,
  +0.00)".

The UI shows one line under the options: **Take X from B → B gets Y
(+0.40)**, and the change list's physical moves say "Pull X from B; put Y in
it". The Deck Salvager's "steal" status (`salvage.py`) is a separate report
and is unchanged.

Donor contexts are built lazily per deck (`DonorBook`), read-only from Redis:
the only write is the embedding vector cache, as for A.

Caps come from the deck's Journal (`budget_constraints.hard_cap` /
`soft_cap`), else the global default (`journal:defaults`, $30/$10 unless
changed via `PUT /api/journal/defaults`). "No budget" turns both off.

## LLM prose

`POST /api/decks/{id}/tune/why` asks Gemini (falling back to Nous) for one
sentence per swap, plus the reason half of each soft-cap note. The prompt
gets the commander, thesis, pillar names and the card pairs. It is told
never to state numbers. Results are cached per cut→add pair in
`tuner:prose:{registry_id}`, so a pair is only paid for once. Every call's
tokens and estimated cost go to `interactions:runs` with trigger `tuner`
(`GET /api/interactions/runs`). The page asks for prose after the numbers
render. If the LLM is down, the rows simply have no prose line.

## API

| | |
|---|---|
| `GET /api/decks/{id}/tune?mode=owned_first\|owned_only\|best&sleeved=true` | fixes, swaps, weak, pet_notice, caps, counts, on_plan, adds |
| `POST /api/decks/{id}/tune/why` | `{prose, llm:{status, provider, model, cost_usd, calls, cached}, tune}`; 502 when no LLM answered |
| `PUT /api/decks/{id}/tune/decisions` | `{cut, decision: own\|buy\|dismissed\|null, add}` |
| `PUT /api/decks/{id}/tune/adds` | `{adds: [{name, qty, source}]}` (basics, combo pieces, fix chips) |
| `POST /api/decks/{id}/tune/pet` | `{name}`: adds the card to the Journal's pets |

Swap shape: `{cut:{name, weak_score, score, why:{edges, fit, edhrec, played?}, rows,
on_plan}, own|null, buy|null, gap, pick: own|buy, verdict:
owned|keep_yours|worth_buying|owned_good_enough|buy|best, near_buy|null,
hidden_hard, soft_note|null, why|null, decision, decision_add}`. An option
is `{name, score, pillar, source: free|deck|buy, deck, price, cap:
soft|null, on_plan}`. A `source: deck` option (AN-8) also carries
`{donor_deck, donor_replacement, donor_replacement_source: free|cut|basic,
donor_before, donor_after, donor_delta, receiver_delta, steal_line}`. Each
shape fix carries `steals: [{name, donor_deck, donor_replacement, ...,
steal_line}]`. `counts` adds `steals` and `steals_dropped`.

## Saved recommendations

`analytics/tune_store.py`. `GET /api/decks/{id}/tune` is still computed on demand and
never saves. Saving is explicit via recompute. Each deck has at most one `active`
record; recompute stores the new result as active and archives the previous one in the
same Redis MULTI/EXEC (archived rows are never deleted).

| | |
|---|---|
| `GET /api/decks/{id}/tune/saved` | `200 {success, recommendation}`; `404 {success:false, recommendation:null, error}` if none |
| `POST /api/decks/{id}/tune/recompute` | body optional `{mode?, sleeved?}`; `200 {success, recommendation, archived}` (`archived` = previous record without payload, or null); 400 bad mode / unknown deck 404 / 409 save lock busy |
| `GET /api/decks/{id}/tune/history?limit=20&include_payload=false` | `{success, history: [record...]}` archived, newest first |

Record: `{id, deck_id, status: active|archived, created_at, archived_at|null,
params: {mode, sleeved}, payload: <GET /tune "tune" object>}`.

Redis: `tunerec:rec:{id}`, `tunerec:active:{deck_id}`, `tunerec:index:{deck_id}`,
`tunerec:lock:{deck_id}`, `tunerec:schema_version`. Run
`python scripts/migrate_tune_recommendations.py [--apply]` (idempotent; repairs the
one-active invariant, stamps the schema version).

## Redis

- `tuner:state:{registry_id}`: `{review_id, decisions, adds}`. Decisions
  and adds last until the next finished review (a new review is a new
  tuning pass, so dismissed rows may come back).
- `tuner:prose:{registry_id}`: the prose cache.
- `tuner:count:{registry_id}`: the open-suggestion count used for the Tune
  tab badge. It is written by each tuner run; the badge stays hidden until
  the first run and whenever the count is 0.

## Entry points

- Journal check-in **Finish → Tune** (D4).
- Stats isolated tray **See in Tune →** (`/tune#weak`).
- Stats one-away combos **Add** (`/tune?add={card}` puts the missing piece
  on the change list).
