# User Deck Intent Questionnaire & Prompt Template

This guide provides the natural-language questionnaire, conversational flow, and LLM prompt templates designed to elicit a player's deck-review intent and map it into the `user_intent.schema.json` data model.

---

## 1. Field-to-Question Mapping Table

| Field Path | User-Facing Question | Input Format | Allowed Options / Range | Default Fallback |
| :--- | :--- | :--- | :--- | :--- |
| `deck_vision.intent` | "Are you building a brand-new Commander deck from scratch, tuning up an existing list, or doing a complete overhaul?" | Single choice / text | `new_build`, `tune_up`, `overhaul` | `"tune_up"` |
| `deck_vision.description` | "What is your main vision or primary goal for this deck? Any favorite cards, synergies, or themes you want to showcase?" | Free text | Open-ended string | `""` |
| `deck_vision.target_turn_win` | "(Optional) On what turn window do you realistically expect this deck to win or lock down the board?" | Integer / null | 1 – 20, or "No preference" | `null` |
| `target_power_level.scale` | "What power level are you targeting for this deck on a 1–10 scale? (1-4: Casual/Precon, 5-6: Focused, 7-8: Optimized, 9-10: Competitive/cEDH)" | Integer | 1 to 10 | `7` |
| `target_power_level.tier` | "Which named power tier best describes your playgroup or desired play experience?" | Single choice | `casual` (1-4), `focused` (5-6), `optimized` (7-8), `competitive` (9-10) | `"optimized"` |
| `budget_constraints.no_budget` | "Do you have an unlimited budget for new card additions or upgrades?" | Yes / No boolean | `true` or `false` | `false` |
| `budget_constraints.total_budget` | "What is your total maximum budget limit for recommended additions and upgrades?" | Numeric currency / null | >= 0, or null if no budget cap | `null` |
| `budget_constraints.max_card_price` | "What is the maximum amount you are willing to spend on any single individual card?" | Numeric currency / null | >= 0, or null if no limit | `null` |
| `budget_constraints.currency` | "Which currency would you like card prices calculated in?" | Currency code | `USD`, `EUR`, `GBP`, `CAD`, `AUD` | `"USD"` |
| `preferred_win_conditions.primary` | "What is your primary intended route to victory?" | Single choice / enum | `combat`, `combo`, `attrition`, `commander_damage`, `alternate_wincon`, `mill`, `burn`, `lockout` | `"combat"` |
| `preferred_win_conditions.secondary` | "Do you want any secondary or backup win conditions?" | Multi-select / array | Subset of win condition enums | `[]` |
| `preferred_win_conditions.disliked_or_excluded` | "Are there any win conditions you or your group dislike and strictly want to avoid (e.g. no infinite combos)?" | Multi-select / array | Subset of win condition enums (cannot overlap with preferred) | `[]` |
| `preferred_win_conditions.custom_win_conditions` | "Any specific or unconventional win conditions you're running (e.g. 'Maze's End gates', 'Revel in Riches')?" | Free-text list | Array of strings | `[]` |
| `archetype_preferences.primary_archetype` | "What primary archetype best fits how you want the deck to play?" | Single choice / enum | `aggro`, `midrange`, `control`, `combo`, `stax`, `spellslinger`, `aristocrats`, `tokens`, `graveyard`, `voltron`, `ramp`, `tribal`, `group_hug`, `group_slug` | `"midrange"` |
| `archetype_preferences.secondary_archetypes` | "Are there secondary archetypes or sub-themes you want to support?" | Multi-select / array | Subset of archetype enums | `[]` |
| `archetype_preferences.excluded_archetypes` | "Are there any archetypes or playstyles you strictly want to exclude (e.g. Stax, Group Hug)?" | Multi-select / array | Subset of archetype enums (cannot overlap with preferred) | `[]` |
| `archetype_preferences.custom_themes` | "Any specific tribal themes, pet mechanics, or flavor restrictions (e.g. 'Dinosaurs', 'Enchantress', 'Landfall')?" | Free-text list | Array of strings | `[]` |
| `freeform_notes` | "Any additional Rule 0 guidelines, local pod quirks, or specific feedback you'd like to share?" | Free text | Open-ended string | `""` |

---

## 2. LLM Intake Prompt Template (Conversational Flow)

When an AI assistant or chatbot gathers user deck-review intent interactively, use the following system prompt instructions:

```markdown
You are the Commander Lab Deck Review Intake Assistant.
Your goal is to gather the user's intent, power level, budget, win conditions, and archetype preferences for their Commander deck review.

Approach:
1. Greet the user and ask about their deck vision: Are they tuning up an existing deck or starting a fresh brew? What commander and themes are they exploring?
2. Inquire about their target power level (1-10 or casual/focused/optimized/competitive) and any budget restrictions (total budget, per-card cap, or unlimited).
3. Ask how they want the deck to win (combat, combo, attrition, etc.) and if there are any playstyles or win conditions they want to exclude (e.g. no infinite combos, no stax).
4. Gather any Rule 0 notes or playgroup constraints.
5. If the user doesn't specify an answer for a field, explain the default fallback and confirm if that works for them.
6. Once all information is gathered, summarize their configuration and output the finalized JSON payload adhering strictly to `schemas/user_intent.schema.json`.
```

---

## 3. One-Shot Extraction Prompt Template

When the user provides an unstructured block of text (e.g. a Discord message or forum post describing their deck goals), use this prompt to extract the intent into structured JSON:

```markdown
You are a Magic: The Gathering intake analyzer for Commander Lab.
Analyze the user's deck description and extract their intent into a JSON object adhering to the schema below.

Rules:
1. Missing fields MUST be populated using the documented defaults:
   - deck_vision.intent: "tune_up"
   - target_power_level.scale: 7, tier: "optimized"
   - budget_constraints.no_budget: false, total_budget: null, max_card_price: null, currency: "USD"
   - preferred_win_conditions.primary: "combat", secondary: [], disliked_or_excluded: [], custom_win_conditions: []
   - archetype_preferences.primary_archetype: "midrange", secondary_archetypes: [], excluded_archetypes: [], custom_themes: []
2. Validate mutual exclusivity: excluded win conditions and archetypes must not appear in preferred lists.
3. Validate budget caps: max_card_price must not exceed total_budget.
4. Output valid JSON only, wrapped in a ```json code fence.

User input:
"""
{{ USER_DECK_DESCRIPTION }}
"""
```

---

## 4. Few-Shot Example

### User Input:
> "Hey! I have a Wilhelt, the Rotcleaver zombie deck that I want to tune up. My playgroup plays around power level 7-8, pretty focused but definitely not cEDH. I don't want any two-card infinite combos, I prefer winning through aristocrat drain and zombie token swarms. I'm willing to spend around $100 total on upgrades, with no single card costing more than $25. No stax please!"

### Extracted JSON Payload:
```json
{
  "deck_vision": {
    "intent": "tune_up",
    "description": "Wilhelt, the Rotcleaver zombie deck tuning",
    "target_turn_win": null
  },
  "target_power_level": {
    "scale": 7,
    "tier": "optimized"
  },
  "budget_constraints": {
    "no_budget": false,
    "total_budget": 100.0,
    "max_card_price": 25.0,
    "currency": "USD"
  },
  "preferred_win_conditions": {
    "primary": "attrition",
    "secondary": ["combat"],
    "disliked_or_excluded": ["combo"],
    "custom_win_conditions": ["Aristocrat drain triggers"]
  },
  "archetype_preferences": {
    "primary_archetype": "aristocrats",
    "secondary_archetypes": ["tokens", "graveyard", "tribal"],
    "excluded_archetypes": ["stax", "combo"],
    "custom_themes": ["Zombies tribal"]
  },
  "freeform_notes": "Playgroup avoids fast 2-card infinite combos and stax lockouts.",
  "metadata": {}
}
```
