# Commander Lab — Analytics / Review / Tuner spec (workshopped 2026-10-12)

Repo: andrewgari/commander-lab (local /home/andrewgari/workspace/personal/commander-lab, base branch ui-hud-overhaul).
Prereqs: PR #63 (Phase 0 data truth) merged + deployed; PR #62 (Identity Lab design, Direction C) merged as the design reference.
Existing code to build on: analytics/ (models, provider registry, EDHREC + CommanderSalt providers), user_intent.py, deck_intake.py.

## Layer 1 — Analytics
- Interaction graph: every card tagged with what it PRODUCES and CONSUMES (tokens, sac, ETB, +1/+1 counters, graveyard, mana, cards, life...). Edge = one card's output feeds another's input. Edges are LABELED with reasons; weight = number of reasons.
- Tagging: Scryfall tagger data first, then an LLM pass. Tags are cached per card forever; only never-seen cards cost a call.
- Scope: per-deck graph + whole-collection graph (the tuner uses the collection graph to find owned cards that plug into an engine).
- Rebuild triggers: sync, manual refresh, nightly job. No constant polling.
- Engines: loops/dense clusters detected in the graph (resource made → spent → payoff), each named (e.g. "Aristocrats: tokens → sac → drain").
- Combos: Commander Spellbook — combos in the deck + combos one card away (owned cards flagged).
- Composition: Command Zone 2025 template as the default (Lands 38, Ramp 10, Card advantage 12, Targeted disruption 12, Mass disruption 6, plan ~25 — verify the numbers). Overridable per deck.
- EDHREC: inclusion %, popularity (provider already exists).
- Theme: a generator drafts a thesis + 3-5 pillars from the cards. The user edits/pins/rejects, and edits survive re-syncs (a re-sync only proposes new pillars). Cards are scored against the user's pillars via local embeddings.
- Charts: graph view, composition vs template, curve, EDHREC inclusion, pillar fit, combo list.

## Layer 2 — Review
- Deck Journal (form, not chat): vision, win cons, target power, budget, pet cards (never cut), novelties, pillars. Backed by the existing user_intent.py schema.
- Filled out once, editable anytime. Every review opens with a check-in: "since last review: +/- cards, power change, is this still true?" per field.

## Layer 3 — Tuner
- Recommends cuts, adds, ramp/composition fixes, and weak performers.
- Weak performer = computed only (few graph edges + off-pillar + low EDHREC inclusion). NO game logging.
- Owned-first, buys allowed. A buy is recommended only when it clearly beats the best owned option ("good enough" judgement, shown as score gap + price).
- Price caps are PER CARD: a hard cap (never suggested) and a soft cap (allowed but flagged with a justification). Global default + per-deck override. Prices in USD from Scryfall.
- Pet cards are never suggested as cuts.

## AI providers
- Primary: Gemini API (key exists in the Hermes .env). Fallback: Nous. Anthropic API = a pluggable slot for later (a Claude subscription does NOT give API access).
- Local HF: a small embedding model (e.g. bge-small) on CPU inside the app container on Tower, for theme/pillar fit. No Framework dependency.
- LLM role = labels + prose only. All stats are deterministic math.

## Phase 2 (after layers 1-3 ship)
- card2vec: a local model trained on real Commander decklists → "cards actually played together" co-occurrence similarity. Shipped in AN-7: see docs/CARD2VEC.md.

## Build rules
- Sequential chain, each card gated on the previous one landing (prevents the auto-decomposer fan-out).
- Coder acceptance = pushed + PR merged + deployed + verified on the live app.
