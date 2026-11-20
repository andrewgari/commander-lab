# Evidence: Required Roles as "Makes X" / "X Matters" (2026-11-04)

Supporting data for [../../role-targets-spec.md](../../role-targets-spec.md). Nothing here is read by code.

## Files

- `scryfall-probes.tsv`: every Scryfall `/cards/search` probe run for the spec (291 rows,
  all `unique=cards`, 2026-11-04). Columns: HTTP status, `total_cards`, date, any Scryfall
  warning or error detail, and the exact query string. 288 rows are status 200, 2 are 400
  and 1 is 404. One 200 row carries a warning (`t:bird ot:mana-rock`: "Unknown keyword
  'ot'"). The probes cover:
  - all 97 distinct `otag:` names in `vocab.py`, each on its own: all resolve with a total
    above 0;
  - the 30 legacy otag unions exactly as `role_tags.legacy_tags()` builds them;
  - all 64 library v1 queries verbatim;
  - every template and example query in the spec.

## Live registry on Tower (v0.4.334, read-only GETs, no Scryfall calls)

13 archetypes (11 with roles), 17 genres (all with roles), 94 role tags (30 migrated + 64
library), 18 targets, 58 decks. All 132 saved role references are legacy
`produces:`/`consumes:` strings.

Legacy roles that are `blocked` for every deck today, because they are over ARCH-3's
5,000-card cap (Scryfall total on 2026-11-04 / the count in Tower's index):

| role | query | total | used by |
|---|---|---|---|
| `produces:spells` | `t:instant or t:sorcery` | 7,675 / 7,644 | 2 archetypes, 6 genres |
| `produces:cards` | `(otag:draw or otag:draw-engine or otag:repeatable-draw or otag:repeatable-card-advantage)` | 5,135 / 5,087 | 3 archetypes, 12 genres |

Records with at least one blocked role: archetypes Group hug, Spellslinger, Storm; genres
(by display name, id in parentheses where different) Aikido, Combo, Eggs, Group hug,
Kindred / tribal (`kindred-tribal`), Paradox (`impulse-draw-cast`), Reanimator, Spellslinger,
Storm, Superfriends, Tempo (`stompy`), Voltron.

## Old vs ARCH-3 vs proposed (11 archetypes × 58 decks)

Mean coverage per archetype over all 58 decks. "Pre-ARCH-3" counts the merged tagger tags
from each deck's interaction graph. "ARCH-3" is `/api/decks/{id}/archetype` as served.
"Proposed" is ARCH-3's matched cards (with blocked roles re-evaluated without the cap) ∪
`oracle_rules.tag_from_oracle` run on oracle text fetched for all 2,452 distinct deck cards.

| archetype | pre-ARCH-3 | ARCH-3 | proposed |
|---|---|---|---|
| +1/+1 counters | 0.420 | 0.371 | 0.413 |
| Aristocrats | 0.621 | 0.504 | 0.609 |
| Group hug | 0.916 | 0.589 | 0.902 |
| Landfall | 0.619 | 0.539 | 0.619 |
| Lands Matter | 0.935 | 0.918 | 0.931 |
| Reanimator | 0.653 | 0.545 | 0.648 |
| Spellslinger | 0.662 | 0.052 | 0.637 |
| Stax | 0.893 | 0.747 | 0.893 |
| Storm | 0.703 | 0.360 | 0.688 |
| Tokens | 0.515 | 0.353 | 0.503 |
| Voltron | 0.648 | 0.600 | 0.642 |

- Pairs that score lower under the proposal than under ARCH-3: **0 of 638**.
- Pairs at 0 under the proposal whose pre-ARCH-3 score was above 0: **0**. Under ARCH-3:
  44 (Spellslinger 42, Tokens 2).
- Decks whose best match equals the pre-ARCH-3 best match: ARCH-3 23/58; proposed 53/58;
  union with the full tagger graph, LLM included, 57/58.
- Role evaluations where pre-ARCH-3 had members and the proposal has none: 22 of 2,030.
  These come from LLM-only tags (199 of 4,931 graph nodes have source `llm:gemini`) and
  card-text differences.

Per legacy role, the number of decks whose ARCH-3 count is below the pre-ARCH-3 count
(and of those, how many dropped to 0), out of the decks where the role was above 0:

```
produces:cards        171/171 (171 to 0)   produces:mana     153/174   produces:spells  116/116 (116 to 0)
produces:tokens       104/116 (8)          produces:lands     70/116 (4) produces:counters  58/104 (4)
consumes:lands         58/104              produces:etb       58/58 (13) produces:artifacts 58/58 (16)
consumes:etb           49/56 (16)          produces:life_loss 47/47 (29) consumes:attacks   41/54
consumes:spells        38/56 (24)          consumes:graveyard 32/110     produces:attacks   29/53 (3)
```

(These counts are per role reference, so a role used by several archetypes is counted once
per archetype.)

Example deck (*The Return of the King*): `produces:cards` had 21 graph members and has 0
under ARCH-3 (it is blocked). Among the cards lost: The One Ring, Commander's Sphere,
Growth Spiral, Minas Tirith.

## Rate limiting

From about 17:00 EST the probing machine (on a shared VPN exit) got HTTP 429 on almost
every second request, even at 3–8 s spacing. The probes finished only with one request
per 20 s and 4–5 minute back-offs after any 429. Scryfall's documented limits are 2/s for
`/cards/search` and `/cards/collection` and 10/s for everything else; a 429 means a 30 s
lockout.
