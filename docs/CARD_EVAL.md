# Card evaluation service

`analytics.card_eval.evaluate_card(r, card, deck) -> EvaluationResult` scores one card inside one deck.
Module only (no route yet); callers pass a Redis client.

```python
from analytics.card_eval import evaluate_card, DeckContext
res = evaluate_card(r, "Sol Ring", DeckContext(commanders=["Atraxa, Praetors' Voice"], cards=[...], kind="physical"))
```

`card` is a name or a Scryfall oracle_id (resolved through `cards.get_card`). Raises `UnknownCardError`
(card not found), `InvalidDeckError` (no commander), `CardEvalError` (Scryfall down). Everything else degrades
and is reported in `result.warnings` and the per-source status fields.

## Pipeline
1. **Card facts** – Scryfall `/cards/collection`, cached `card_eval:card:{name_key}` (7 d): type, text, colour identity, Commander legality.
2. **EDHREC** – `analytics/providers/edhrec.EDHRecClient` (throttle, retry on 429/5xx), cached `card_eval:edhrec:{slug}` (`EDHREC_MAX_AGE_DAYS`, default 7). A 404 is negative-cached for 1 d; on fetch failure a stale copy is served (`stale: true`).
3. **Role signal** – Hugging Face zero-shot classification (`hf_classifier.py`) over 11 roles, merged with heuristic roles (`composition.classify_card`) and EDHREC slot priors. HF failure/disabled → heuristics only.
4. **Strength** 0–100 – weighted mean of inclusion, synergy, model role confidence, and role need (deck lacks the role); components without data are dropped and the rest re-normalised.
5. **Candidate pool** – EDHREC cards for the commander, not already in the deck, filtered to the commander's colour identity and Commander-legal; same-role first (up to 12), then other roles (up to 8). Physical decks: pass `owned_lookup` to flag `owned` and apply a score bonus.

## Environment
| var | default | meaning |
| --- | --- | --- |
| `HF_TOKEN` | – | HF bearer token; without it the model is `disabled` |
| `CARD_EVAL_HF_MODE` | `api` if token else `off` | `api` or `off` |
| `CARD_EVAL_HF_MODEL` | `facebook/bart-large-mnli` | zero-shot model id |
| `CARD_EVAL_HF_URL` | HF router `…/hf-inference/models/{model}` | endpoint template |
| `CARD_EVAL_HF_TIMEOUT` | 20 | seconds |
| `CARD_EVAL_WEIGHTS` | – | JSON overriding any `ScoringWeights` field |
| `EDHREC_MAX_AGE_DAYS` | 7 | EDHREC page cache age |

## Tuning
All weights, thresholds, role labels, and pool sizes live in `analytics/card_eval/config.py`. To match the
domain rubric, edit only that file.

## Result schema (`analytics/card_eval/schema.py`, pydantic, `extra="forbid"`)
```
EvaluationResult
  card: CardInfo {name, oracle_id?, type_line, oracle_text, color_identity, legal}
  deck_kind: "physical" | "digital"
  commanders: [str]
  roles: [RoleScore {role, confidence 0..1, source: model|heuristic|edhrec}]   # max 3
  strength: float 0..100
  strength_breakdown: {inclusion?, synergy?, role_confidence?, role_need?}      # each 0..1 or null
  edhrec: EdhrecStats {status: ok|card_not_listed|no_commander_page|unavailable|no_commander,
                       commander_slug?, inclusion? 0..1, synergy?, num_decks?, decks_sampled?,
                       slot? (EDHREC cardlist tag), slot_peers [str], stale, error?}
  model: ModelSignal {status: ok|disabled|unavailable, model_id?, error?, scores {role: conf}}
  candidates: [Candidate {name, roles, same_role, inclusion?, synergy?, score, color_identity, owned?}]
  warnings: [str]
```
Roles: ramp, card_advantage, targeted_removal, board_wipe, protection, tutor, recursion, token_maker, threat, engine, utility_land.

Tests: `tests/test_card_eval.py` (EDHREC, HF and Scryfall mocked).
