# Preset generation backend (presetgen-v1)

Spec: `presetgen-v1` (extends `composition-v1` §8). Code: `analytics/presetgen.py`,
`analytics/composition_presets.py` (built-in presets + auto-select), on top of `analytics/composition_ai.py`
(packet, validator A1-A8, op application).

## Entry points

| Call | Result |
|---|---|
| `presetgen.build_packet(deck, texts, card_tags, deck_tags=, ident=, current_preset=, user_presets=, facet_counts=)` | `composition_ai.build_packet` plus `commander_tags`, `mdfc_eq`, `facet_counts`, `user_presets` |
| `presetgen.generate(packet, chain=None)` | Baseline, or an AI draft anchored to it. `ai.status` is `ok`, `off` (no LLM) or `fallback` (+ `reason`, `codes`). Never raises on an AI failure. |
| `presetgen.improve(packet, chain=None)` | Candidates applied (AI-off: the top 6), or the AI's chosen/reworded ops after A1-A12. `ai.status` is `ok`, `off`, `fallback` or `skipped` (no candidates). |
| `presetgen.baseline(packet)` | Pure and deterministic. B0 auto-select (pin ignored) -> B1 land math -> M1-M6 -> floors -> trim to plan room >= 20. |
| `presetgen.improve_candidates(packet, preset=None)` | R1-R10 candidates (`c1`...), one op per ref, plus info-only `deck_gaps`. |
| `presetgen.reliability(k, n, library)`, `land_math(...)` | Exact hypergeometric, and Burgess/Karsten/MDFC clamped to the A7 window. |
| `presetgen.validate_ops(preset, ops, packet, candidates)` | A1-A8 via composition_ai plus A9-A12. A failing op is dropped with its codes. |

Drafts are never saved and never pin a deck (A8). No route is added here: a route handler builds the packet,
passes `chain` (or `None`) and returns the dict.

## Notes for callers

- `facet_counts` has no classifier yet; a missing `facet:*` count is 0, so the coverage rule (M6/R8) fires until it is supplied.
- In `set` ops, `to` is the slot's complete new bounds (omitted keys are null), so `{target: 6} -> {max: 1}` is valid.
- Baseline drops `theme:` slots of inactive themes (they would count 0 and raise W4), the same fix R4 proposes.
- Config lives in `presetgen.CONFIG` (the `composition.generate` block of the spec).
- Baseline ids and timestamps are left unset so the output is byte-identical; the server assigns them on save.
