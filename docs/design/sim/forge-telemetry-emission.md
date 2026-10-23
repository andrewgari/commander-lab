# Forge sim daemon: per-game `players[].cards` + `curve`

`forge_sim/forge_game_telemetry.py` parses verbose Forge `sim` logs (no `-q`) into the
`games_log[i].players[]` shape that `analytics/sim_telemetry.py` consumes:

    {"seat", "deck", "curve":[{turn, land_played, mana_spent, spells_cast}],
     "cards":{name:{drawn, cast_turn, stuck}}}

## Real vs null
| Field | Source | Status |
|---|---|---|
| `curve[].land_played`, `spells_cast`, `turn` | `Land:` / `Add To Stack: ... cast` / `Turn:` lines | real; turn = the player's OWN turn count |
| `cards[name].cast_turn` | first `cast` line for that card (commander included; copies/triggers excluded) | real |
| `cards[name].drawn`, `stuck` | Forge logs neither draws nor hand contents | **null** (never guessed) |
| `curve[].mana_spent` | Forge mana lines carry no player | **null** |

Only cards that were cast appear in `cards`. Aggregates therefore get n>0 for `cast` and
`first_cast_turn`; `drawn`/`stuck_in_hand` stay zero-sample until Forge itself is patched.

## Daemon wiring
In `forge_sim_daemon.execute_job`, after `combined.log` is written (verbose = standard/specific
modes), call `enrich_games_log(combined_text, games_log, f"user_{deck_id}")`. Opponent `deck` values
already in `games_log[].opponents` are the backend ids (`user_{id}` for Commander Lab decks).
`-q` (random mode) runs have no verbose block, so `players` is omitted for them. The backend keeps
filtering card metrics to owned decks by deck id; the daemon emits for all seats.

The daemon lives outside this repo (mtg-forge appdata `forge/sim-workspace`); the module is stdlib-only
so the file can be copied there unchanged.
