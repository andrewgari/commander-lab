# Single Card Review: debate + replacements

Orchestration on top of `analytics/card_eval` (evaluation) and `docs/single-card-review-spec.md` (domain spec).
Code: `analytics/card_review/`. Tests: `tests/test_card_review.py` (scripted LLM, no network).

## Flow
1. **Evaluate** the card in its deck (`evaluate_card`).
2. **Gates** (no LLM): pet card (Deck Journal) → keep 1.0; banned / outside colour identity → remove 0.99 with like-for-like replacements; commander → rejected.
3. **Debate**: Remove and Keep advocates, 2 or 3 rounds (default 2, `CARD_REVIEW_ROUNDS` or request body). Round 1 is blind, round 2 allows rebuttals, round 3 is a closing statement. A concession stops the debate. Every claim is validated in code: it must cite packet IDs, use only numbers found in the evidence packet, stay within 60 words, and is re-tiered by the evidence it cites.
4. **Judge** (spec §3.4.3) returns a verdict, validated per §3.5: invalid JSON is retried once, then a rubric-lean `fallback` verdict is used (confidence ≤ 0.60). Key reasons need a valid `[ID]` and no unknown numbers. A concession overrides the judge.
5. **Confidence** (§3.6): `0.5·rubric prior + 0.5·judge_confidence`, capped at 0.70 when EDHREC data is missing, 0.65 when the Journal is unconfirmed, 0.60 on fallback; floor 0.50.
6. **Replacements** (remove only): 3-5 suggestions, see below.

The LLM sits behind `DebateLLM.complete(role, system, user, temperature)` (`analytics/card_review/llm.py`). Production uses `OracleLLM` (the oracle Hermes profile); tests pass a scripted fake.

## API
### `POST /api/decks/{deck_id}/cards/{card_id}/review`
`deck_id`: registry id (`archidekt:123`) or raw id. `card_id`: card name or Scryfall oracle id, URL-encoded (names with `//` are fine). Body (optional): `{"rounds": 2 | 3}`.

`202 {"success": true, "job_id", "status": "queued", "rounds", "status_url"}`. The review runs in the background (oracle calls take minutes).
Errors: `404` unknown deck, or card not in the deck; `400` card is the commander; `422` bad `rounds`; `409` this card is already being reviewed; `429` 3 reviews already running; `503` oracle offline.

### `GET /api/decks/{deck_id}/cards/{card_id}/review/jobs/{job_id}`
Poll for progress. `{"success": true, "job": {...}}`:

| field | meaning |
| --- | --- |
| `status` | `queued`, `running`, `done`, `failed` |
| `stage` | `evaluated`, `round_start`, `turn`, `concession`, `judging`, `replacements`, `done`, `failed` |
| `events` | last 50 stage events with timestamps |
| `transcript` | validated debate turns so far (grows during the run) |
| `result` | full review when `done` |
| `error`, `error_kind` | when `failed` (`not_allowed`, `oracle_offline`-style client errors, `internal`) |

Jobs are kept in Redis for 24 h (`card_review:job:{id}`).

### Result object
```json
{"verdict": "keep|remove", "confidence": 0.84, "key_reasons": ["… [J2, S1]"],
 "provisional": false, "fallback": false, "gate": null, "deciding_tier": "spirit",
 "rubric": {"strength": 34.0, "band": "cut_candidate", "p_keep_prior": 0.12, "breakdown": {}},
 "roles": {"primary": "board_wipe", "secondary": [], "sources": {}},
 "transcript": [{"side": "remove", "round": 1, "claims": [{"id": "RM-1.1", "tier": "spirit", "claimed_tier": "spirit",
                 "text": "…", "evidence": ["J2"]}], "rebuttals": [], "concede": false, "discarded": []}],
 "winning_claims": ["RM-1.1"], "discarded": [], "packet": {"C1": "…"},
 "replacement_brief": {"mode": "like_for_like|function_change|mixed", "target_roles": [], "avoid": []},
 "replacements": [{"card": "Chaos Warp", "oracle_id": "…", "role": "targeted_removal", "like_for_like": false,
                   "rationale": "…", "score": 77.5, "score_breakdown": {}, "tier": "owned|buy", "owned": null,
                   "slot_gate": {"slot": "mainboard", "legal": true, "reason": "…", "type_class": null}, "flags": []}],
 "replacement_meta": {"mode": "mixed", "target_roles": [], "pool_exhausted": false, "dropped": [{"card": "…", "reason": "…"}]},
 "flags": ["journal_unconfirmed", "edhrec_data_missing"], "warnings": [], "card": {}, "rounds": 2}
```
`provisional: true` means a remove verdict on a deck whose Journal has no thesis or pillars; replacements are still shown (a product decision flagged in the spec for Andrew). `replacements` is empty on keep.

## Replacement rules
Hard filters are applied in code against fresh Scryfall facts, not just the evaluation pool: colour identity ⊆ commander identity, full mainboard legality via the mainboard slot gate (Commander-legal with known legality and no variant type class — planar/scheme/attraction cards are dropped with the gate's reason), not already in the deck or command zone (by name and by `oracle_id`), no duplicate candidates, and the spirit gate (must fill a target role or have pillar fit ≥ 60, proxied by EDHREC synergy ≥ 0.10 or a staple role). Rep ranking is spec §4.4 over the terms we have data for (RoleFit 0.30, PillarFit 0.25, E_c 0.15, Own 0.10, renormalised). `like_for_like` is true when the candidate's primary role equals the reviewed card's. Modes: `like_for_like` (all same-role), `function_change` (all different-role), `mixed` (3 + 2 by score). A mode that can't reach 3 results is topped up from the other group and flagged. Owned cards within 10 Rep of the top rank first. Penalties (Game Changer, salt, stax) come through an optional `annotate(name)` hook as flags and score deductions; they never filter.

## Known gaps vs the spec
The EvaluationResult has no mana value, price, Game Changer, salt, `games`, Journal pillar predicates, floor counts, Karsten sources or Spellbook combos. So: no CurveFit/Budget terms, no paper-only or budget-cap filters, no donor-deck steals, no sub-tag diversity cap, evidence IDs `K*`, `M*`, `A*`, `X*`, `T*` are absent from the packet, and the strength rubric is the evaluation service's weighted mean, not the spec's `0.45·E + 0.55·D` (so spec test items 3, 4 and 11 belong to the evaluation service). `role_need` stands in for floor counts when forcing a mixed mode. All weights live in `analytics/card_review/config.py`.

## UI (CR-F)
Deck page → card detail → **Review card** (or `R` on a focused card). Spec: `docs/design/card-review/UX_SPEC.md`; code: `static/js/card_review.js` (view, API adapter, session) + `hud-cr-*` in `static/css/hud.css`; tests: `tests/test_card_review_ui.py` (node, real job fixtures in `tests/fixtures/card_review_job_*.json`).

- Transport is polling (1.5 s). Reopening a card resumes the saved job (`localStorage cr:job:{deck}:{card}`), and a 409 "already running" resumes that job.
- Swap/add writes the existing Tune decision, `PUT /api/decks/{id}/tune/decisions {cut, decision, add}`: digital → `own`; physical → `own` if the replacement is owned, else `buy`. Undo sends `decision: null`. Skip is local only.
- Not built (the API has no equivalent): streaming tokens, Cancel, deck median, price/CMC/EDHREC % on replacements, Show more / Widen search, a ⋯ card menu and right-click entry.
