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
| played | card2vec played-with score (AN-7, `docs/CARD2VEC.md`): how often real Commander decklists play the card with the rest of this deck, 0 = a random card, 1 = clearly belongs | the model doesn't know the card (new set, very rare), or no artifact |

A cut is scored with the same formula, so "gap" always compares like with like.

## Swap rules

- **Cuts**: weak performers, weakest first. Never pets, lands or commanders.
  At most 12 swaps.
- **Candidates**: every owned card (in the box, or sleeved in another deck
  when "Include sleeved-elsewhere" is on) plus every card on the commander's
  EDHREC page. They must be inside the colour identity and not already in
  this deck. A buy needs a Scryfall USD price (an unpriced card can't be
  bought).
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
soft|null, on_plan}`.

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
