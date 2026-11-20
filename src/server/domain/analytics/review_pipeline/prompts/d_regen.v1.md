<!-- @header
d_regen v1 (Pass D regeneration of ONE item). DRAFT per DECISIONS D8.3. These are the user turns appended to a
generator pass's own cached system blocks (Pass B axis blocks, Pass C blocks), so the generator sees the same
facts, card table, pool and rules it had the first time. One failing item per call; nothing else is rewritten.
Output schemas: schemas.SWAP_REGEN_SCHEMA / FINDING_REGEN_SCHEMA / CUT_REGEN_SCHEMA / TOP_REGEN_SCHEMA.
-->
<!-- @section user.swap -->
A check rejected one of your swaps. Replace ONLY that swap; everything else stays as it is.

REJECTED SWAP {{swap_id}} (position {{position}} in your ranking)
  cut: {{cut}}
  add: {{add}}
  trade-off you wrote: {{tradeoff}}

WHY IT WAS REJECTED
{{reasons}}

SWAPS THAT ARE ALREADY ACCEPTED (do not duplicate their cut or their add, and do not undo them)
{{accepted}}

Return {"action": "replace", "swap": {...}, "note": "..."} with ONE new swap that fixes what was wrong: the add is a pool id (pc-N) and the alternative another pool id; the cut is a deck card; resolves_finding_id is a PROBLEM id; the trade-off is specific and honest. The new swap may resolve the same problem or a different one. If no pool card can fix the problem without breaking the rules above, return {"action": "withdraw", "swap": null, "note": "<why>"}. Withdrawing is better than forcing a bad swap.
<!-- @section user.finding -->
A check rejected one finding from your analysis of this axis. Rewrite ONLY that finding.

REJECTED {{kind}} ({{finding_id}})
  claim: {{claim}}
  severity: {{severity}}, confidence {{confidence}}
  cited cards: {{cards}}
  cited fact keys: {{fact_keys}}

WHY IT WAS REJECTED
{{reasons}}

Return {"action": "rewrite", "finding": {...}, "note": "..."} with one finding in the usual finding shape that keeps whatever was true and specific and fixes what was wrong: name the cards and the mechanism, cite only cards in the deck and fact keys that exist, and make sure the cited evidence actually proves the claim. If the claim cannot be supported by the data, return {"action": "withdraw", "finding": null, "note": "<why>"}. Do not invent a different problem to replace it.
<!-- @section user.cut -->
A check rejected one entry of your cuts_to_consider. Rewrite ONLY that entry.

REJECTED CUT: {{card}}
  reason you wrote: {{reason}}
  fact keys you cited: {{fact_keys}}

WHY IT WAS REJECTED
{{reasons}}

Return {"action": "replace", "cut": {"card": ..., "reason": ..., "fact_keys": [...]}, "note": "..."} with a corrected entry (the same card or a different one that the evidence supports), or {"action": "withdraw", "cut": null, "note": "<why>"} if the evidence does not support cutting anything. Never list a pet card, a commander or the cut of an accepted swap.
<!-- @section user.top -->
The "top changes" selection must be redone because it failed a check or the swaps it refers to changed.

THE SWAPS THAT REMAIN (ranked; positions are 1-based)
{{swaps}}

PREVIOUS why_these_first: {{previous}}

WHY IT WAS REJECTED
{{reasons}}

Return {"ranks": [...], "why_these_first": "..."} choosing the {{want}} change(s) to make first by their position in the list above, with a why_these_first that is specific to this deck (name the cards and the problems they fix).
