# Audit: redraft prompt and output-length behavior

Date: 2026-10-23. Auditor: coder (task t_9cd409cc). Codebase state: origin/main as of merge into wt/t_9cd409cc.

## TL;DR

- "Redraft" is the primer builder's per-section regenerate. It is not a separate prompt: the
  frontend (`static/js/primer_builder.js:365 redraft()`) re-POSTs to `POST /api/decks/{id}/primer/generate`
  with `options.sections = [<one section id>]` and `keep = {}`.
- Output length is controlled ONLY by the system-prompt instruction
  "Keep it under {max_words} words" (word-based, no character constraint) and by a post-hoc
  validator that rejects replies longer than 1.5x max_words. There is NO minimum length anywhere —
  no prompt floor, no programmatic min-char check, no retry.
- No token cap is sent to the model (oracle client sends no max_tokens); the only truncation in
  the codebase applies to the short deck description (280 chars), never to primer/redraft sections.

## 1. Exact prompt text sent for a redraft

A redraft of a section (e.g. "Overview") sends the SAME system prompt as a full generate, with
`{sections}` reduced to the single requested heading. Built in
`analytics/deck_writeup/service.py:396-397` from `analytics/deck_writeup/prompts.py:31-47 (PRIMER_SYSTEM)`.

Verbatim system prompt for a redraft of the "overview" section at default settings
(length="standard" -> max_words=900, voice="plain"):

```
You write deck primers for Commander (EDH) decks in Commander Lab, from the owner's answers and the deck's card data.
Write markdown with exactly these sections, in this order, each as a '## ' heading. Skip a section only when the
inputs give nothing to say about it:
## Overview
Keep it under 900 words. Be specific and concrete; no filler.
RULES:
- Mention only cards that appear in the CARDS or KEY CARDS sections below. Never invent cards, numbers, prices or win rates.
- Describe what a card does only from ORACLE TEXT or the owner's notes.
- Precedence of intent: owner's form answers > confirmed DECK SPIRIT > card data. If DECK SPIRIT is labeled 'draft spirit, may be wrong', treat it as a tentative hint.
- Never suggest cutting a card listed under PETS.
- Warm and concrete, not a sales pitch. No hype words (powerhouse, dominates, unstoppable, cEDH-level). Don't claim a power level beyond BRACKET TARGET. Say how the deck feels to play, not just what it does.
- VOICE: Plain and direct: clear sentences, everyday table language, zero fluff.
- Write Upgrades and Swaps only from the owner's budget/upgrade notes; if they're blank, skip the Upgrades and Swaps section.
- List only combos from the owner's notes or the COMBOS block. Never infer a combo from card names. Describe a combo's result exactly as the COMBOS entry states it (who does what to whom); do not upgrade, generalize or reword it (e.g. a result that sends a player to 1 life plus unblocked damage is not 'target opponent loses').
- Table Talk: state only TABLE FACTS (and owner-selected Rule 0 facts). Plain, neutral wording: name the card and what it does to the table. Flag, never forbid; don't apologize for it or hype it. If TABLE FACTS is empty, write 'No stax, MLD, extra turns, or infinites detected.' If TABLE FACTS confirms a category is absent (no extra turns, no MLD, no stax), say so explicitly rather than only listing what is present. When COMBOS is non-empty and BRACKET TARGET is 3 or lower, do not simply assert the bracket is safe; name the earliest turn the combo can go online and compare it plainly against the bracket's deck-strength expectations instead of declaring compliance.
- Reserve the word 'pillar' (or 'pillars') only for entries explicitly listed under PILLARS in DECK SPIRIT. If DECK SPIRIT has no PILLARS, do not describe any card, combo or theme as a 'pillar'; use plain words instead (strategy, plan, line).
- The owner's answers are the source of intent. Expand them; do not contradict them. If an answer is missing, use the card data and say less rather than guess.
- Output ONLY the primer markdown.
```

The user prompt is `prompts.USER_TEMPLATE` (prompts.py:55-85) filled by
`service.build_user_prompt` (service.py:183-201): DECK / COMMANDER / COLOR IDENTITY / BRACKET TARGET /
CARD COUNT / DECK SPIRIT / OWNER'S NOTES / TABLE FACTS / COMBOS / KEY CARDS / ORACLE TEXT /
CARD TYPES / AVG MANA VALUE / TOP TAGS / CARDS. It is identical for a redraft and a full generate.

Frontend call path: `redraft(id)` (static/js/primer_builder.js:365-376) -> `answers()` collects the
current wizard answers and options (length, voice) -> `api("POST", "/primer/generate", ...)` ->
`app.py:4292 api_deck_primer_generate` -> `_deck_writeup` (app.py:4217) ->
`service.generate_primer(..., wizard=True, length=opts.get("length"), sections=[id], keep={})`.

Important: the redraft inherits whatever Length option is currently selected in the builder UI
(`S.opts.length`, default "standard", static/js/primer_builder.js:82, 291). A user on "brief"
gets a 450-word cap on the redraft; there is no per-section length control.

## 2. Existing length constraints / truncation logic

Word-based ceilings (service.py:19-25):

| Constant | Value | Applies to |
|---|---|---|
| `DESCRIPTION_MAX_WORDS` | 90 (prompt asks for 50) | short deck description only |
| `DESCRIPTION_MAX_CHARS` | 280 | short deck description only |
| `PRIMER_MAX_WORDS` | 900 | primer + redraft (default "standard") |
| `LENGTH_WORDS` | brief=450, standard=900, deep=1400 | primer + redraft, user-selectable |
| `MAX_CARDS_IN_PROMPT` | 100 | input packet, not output |
| `MAX_ORACLE_CARDS` / `ORACLE_TEXT_CHARS` | 14 / 500 | input packet, not output |

Enforcement points:

- Prompt instruction: "Keep it under {max_words} words." (prompts.py:36) — the ONLY statement
  about primer length. It is a ceiling; there is no floor.
- Validator: `validate_primer` (service.py:258-270) raises WriteupError("far too long", "too_long")
  only when `_words(t) > max_words * 1.5` (i.e. >1350 words at standard). Empty replies and
  missing/unknown `## ` headings are also rejected. No lower bound, no char check.
- Description only: `trim_to_chars` (service.py:226-235) hard-trims the short description to 280
  chars at a sentence/word boundary, and `validate_description` errors past 1.5x max words.
  Primer/redraft sections are NEVER truncated — the model's text is kept verbatim.
- Save-time bound: `store.MAX_JSON_BYTES = 200_000` (store.py:18,55) — payload-size guard, not a
  content-length policy.
- Client/server transport: oracle_client sends no max_tokens/n_predict (oracle_client.py:138-148);
  GEN_TIMEOUT_MS = 120000 on the frontend; ORACLE_TIMEOUT_S >= 600 server-side.

## 3. Where default output length is / could be enforced

Current enforcement lives entirely in `analytics/deck_writeup/`:

1. Prompt level — `prompts.py:36` ("Keep it under {max_words} words") formatted in
   `service.py:396-397` from `LENGTH_WORDS` (service.py:22). A minimum-length instruction
   ("produce at least N characters; expand with concrete detail") belongs here, in PRIMER_SYSTEM,
   so it reaches both full generate and redraft (redraft reuses this prompt).
2. Programmatic validation — `validate_primer(text, max_words)` (service.py:258). This is the
   natural place to add a min-char/min-word check with kind="too_short", and `generate_primer`
   (service.py:365) is the natural place to add a single retry/expand pass, since it already owns
   the ask() call and the error taxonomy (WriteupError.kind).
3. Per-section note: for a wizard redraft, `max_words` applies to the whole reply even though only
   ONE section is requested (service.py:386-400). If a per-section floor is added, scale it
   (e.g. divide by number of requested sections) — otherwise a full 5-section generate would be
   held to a single-section minimum.

Nothing in app.py or the JS enforces length; both just pass `length` through.

## 4. Example current outputs with character counts

Live measurement was NOT possible in this audit: the local Redis has no saved `primer:*` keys
(no real generated primer on record), and the running app's `ORACLE_KEY` env is empty, so a live
generation against oracle could not be performed. What exists:

- Test fixture `GOOD_PRIMER` (tests/test_deck_writeup.py:20):
  `"## Overview\nGoblins.\n\n## Game Plan\nGo wide.\n\n## Weaknesses\nWipes."` = 65 chars / 11 words.
  Confirms the validator happily accepts extremely short output — there is no floor.
- Description fixture reply `"A goblin swarm led by Krenko."` = 29 chars; passes (<=280).
- Derived bounds for a real redraft reply: the prompt caps at 450/900/1400 words
  (~2,700 / ~5,400 / ~8,400 chars at ~6 chars/word) and the validator errors past 1.5x that.
  Nothing stops a one-line section: a single-section redraft asking for "## Overview" typically
  returns on the order of 50-250 words (~300-1,500 chars), but as low as a few words — the
  fixture proves short replies validate.

Downstream tasks that need a real-world typical length should either run one live generation
with ORACLE_KEY configured and record the char count, or instrument `validate_primer` to log
`len(text)` / `_words(text)` for a day of real traffic.

## Where the change for the 500-char minimum should go (for t_1ee8ca35)

- `analytics/deck_writeup/prompts.py` PRIMER_SYSTEM: add the floor instruction near
  "Keep it under {max_words} words" (line 36), parameterized like max_words.
- `analytics/deck_writeup/service.py`:
  - add e.g. `PRIMER_SECTION_MIN_CHARS = 500` next to LENGTH_WORDS (line 22);
  - enforce in `validate_primer` (per-section for wizard replies after `split_sections`, or on
    the single requested section) with a new WriteupError kind, and/or retry once in
    `generate_primer` before surfacing the error.
- `app.py` needs no change — it already maps WriteupError.kind to 422 responses.
- Frontend needs no change — a failed redraft already shows "Could not redraft ... The section is
  unchanged." (primer_builder.js:373).
