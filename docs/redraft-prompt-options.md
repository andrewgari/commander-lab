# Options: enhancing the redraft prompt

Date: 2026-10-23. Builds on `docs/audits/redraft-prompt-length-audit.md` (task t_9cd409cc).
Context: redraft = per-section regenerate via `POST /api/decks/{id}/primer/generate`; the only
length control is the ceiling "Keep it under {max_words} words" (prompts.py:36) plus a 1.5x
`too_long` validator. There is NO floor — a 65-char reply validates (proven by test fixture).

## Option A — Explicit length floor in the prompt

Change: in `analytics/deck_writeup/prompts.py`, PRIMER_SYSTEM, replace line
`Keep it under {max_words} words.` with:

    Write between {min_words} and {max_words} words. If a draft would come out shorter, expand it with concrete detail — name specific cards, what they do in this deck, and how it plays at the table. Never pad with filler or hype.

Add `PRIMER_MIN_WORDS = 180` in `service.py` (next to LENGTH_WORDS), scale it:
`min_words = PRIMER_MIN_WORDS * len(requested_sections) / total_sections_in_prompt` — simplest
correct version: per-section floor of ~120 words applied to the requested section(s).
Pair with a validator check in `validate_primer`: raise `WriteupError(kind="too_short")`
if the requested section is under ~500 chars, with ONE automatic retry in `generate_primer`
that re-asks with "Your reply was too short; expand it with concrete detail."
Effect: reliably meatier sections; risk of occasional filler if the model has little material
(the prompt's "say less rather than guess" rule already mitigates this).
Cost: LOW — two files, ~15 lines, existing error taxonomy reused.
Risks: too-tight floor can force padding on thin decks; validator rejection adds one extra
oracle round-trip on short drafts.

## Option B — Richer structure / audience / tone instructions

Change: add to PRIMER_SYSTEM, right after the heading list:

    Write for the deck's owner, a regular player at a casual table — someone who knows Magic but not this deck. For each section: open with one sentence stating the section's job in this deck, then 2-4 short paragraphs of specifics (cards, numbers from the data, play patterns). Use plain table language; every claim must trace to CARDS, ORACLE TEXT, or the owner's answers.

Effect: longer, more organized output as a side effect; improves quality beyond length.
Cost: LOW — prompt-only, no code.
Risks: prompt grows by ~70 tokens; structure boilerplate can become formulaic across sections;
doesn't guarantee a floor by itself.

## Option C — Few-shot example embedded in the prompt

Change: append one worked example to PRIMER_SYSTEM, a condensed GOOD overview (~150 words)
showing the expected depth, e.g.:

    EXAMPLE (depth and tone to match, do not copy content):
    ## Overview
    This is a go-wide goblin deck that wins by flooding the board and swiping everyone out in combat. Krenko, Mob Boss is the engine: with him untapped and two goblins out, you make 4+ goblins per turn, and every extra token makes Skullclamp and Goblin Chieftain better. The deck races more than it controls — expect to be the aggressor. In practice you curve a one-drop turn 1, Krenko turn 3-4, and threaten lethal around turn 6-8 if the table doesn't wipe.

Effect: the single most reliable way to raise typical output depth; models copy demonstrated
length. Cost: MEDIUM — the example must be written once and kept consistent with the rules
(easy to drift out of date when rules change); adds ~200 tokens per request.
Risks: content leakage into real primers if the example is too specific; extra tokens on every
call including redrafts.

## Option D — Iterative second-pass expansion

Change: in `service.generate_primer`, after the first successful validate, add:

    if _words(requested_section_text) < MIN_WORDS:
        text = ask(expand_prompt(text, min_words))  # one pass only
        re-validate

where `expand_prompt` is:

    Your reply was too short. Rewrite it at least {min_words} words, expanding each section with concrete detail from the provided card data and owner's answers. Keep all RULES. Output ONLY the primer markdown.

Effect: guarantees a floor regardless of what the model returns the first time; works for both
generate and redraft since they share the path.
Cost: MEDIUM — new code path + retry logic + one extra oracle call per short draft (~doubling
latency for those drafts, up to +60s).
Risks: doubles latency on weak drafts; expansion pass can contradict the first pass; retries
consume oracle budget.

## Recommendation

Default: Option A (with its one-retry validator pair), optionally adding Option C's example later.
Justification: it addresses the actual defect (no floor) at the exact enforcement points the
audit identified, at the lowest cost and latency risk, and the retry makes the floor enforceable
rather than advisory — Option B improves style but not length guarantees, Option C costs tokens
on every call, and Option D alone adds latency to fix what the prompt should have asked for.
