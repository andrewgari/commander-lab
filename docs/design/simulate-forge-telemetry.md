# Forge Headless Sim Telemetry Spike

## Overview
This document summarizes what data we can extract per game from running headless Forge (`forge sim`) matches. The goal is to feed a WoWLogs-style deck readout for Commander lab's Simulate mode. 

**Note on Player Count**: Forge headless supports 2-6 player Commander. We confirmed 4-player works out of the box by providing 4 decks (`forge.sh sim -d deck1.dck deck2.dck deck3.dck deck4.dck -n 1 -f commander`).

## Metric Feasibility

| Metric | Status | Details |
|--------|--------|---------|
| **Land Drops Hit/Missed** | DIRECT / DERIVED | The log explicitly prints `Land: Ai(2)-Deck2 played Island`. A missed land drop is derived by the absence of this line during a player's turn. |
| **Mana Available by Color** | NEEDS FORGE CHANGE | The log only emits mana added when it is actually spent/tapped (`Mana: Island (115) - {T}: Add {U}.`). Total available mana at the start of a main phase is not logged. |
| **Mana Spent** | DIRECT / DERIVED | We can sum the `Mana: [Source] - [Ability]` lines that precede spell casts or ability activations. |
| **Spells Cast + MV** | DIRECT / DERIVED | The log prints `Add To Stack: Ai(1)-Deck1 cast Purphoros, God of the Forge`. The spell name is DIRECT. The MV is DERIVED and requires an external Scryfall/Oracle lookup. |
| **Commander Cast Turn** | DERIVED | We know the commander's name from the `.dck` file. We can match it against the `Add To Stack: ... cast [Card Name]` events and record the current `Turn: Turn X`. |
| **Opening Hand/Mulligans** | DIRECT | The log clearly prints `Mulligan: Ai(2)-Deck2 has kept a hand of 7 cards`. |
| **Cards Drawn** | NEEDS FORGE CHANGE | The log shows `Phase: Ai(2)-Deck2's Draw step` but does not print the card drawn or the quantity of cards drawn. |
| **Cards Stuck in Hand** | NEEDS FORGE CHANGE | The log does not output the contents of a player's hand at any point. |
| **Game End Turn + Winner** | DIRECT | The log clearly prints `Game Outcome: Turn 49` and `Game Outcome: Ai(2)-Deck2 has won because all opponents have lost`. |

## Proposed Per-Game JSON Schema

```json
{
  "game_id": "sim_12345",
  "format": "commander",
  "duration_ms": 24750,
  "end_turn": 49,
  "winner": "Ai(2)-Deck2",
  "players": [
    {
      "name": "Ai(1)-Deck1",
      "commander": "Purphoros, God of the Forge",
      "opening_hand_size": 7,
      "turns": [
        {
          "turn_number": 2,
          "land_played": "Mountain",
          "mana_spent": [
            { "color": "R", "amount": 1 }
          ],
          "spells_cast": [
            { "name": "Lightning Bolt", "mv": 1 }
          ]
        }
      ],
      "metrics": {
        "commander_cast_turn": 8,
        "total_spells_cast": 14,
        "missed_land_drops": 2
      }
    }
  ]
}
```
