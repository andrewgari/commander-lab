# Role tags as user-defined Scryfall queries

**Status:** spec (no code) · **Owner:** Andrew · **Card:** ARCH-2
**Supersedes:** the fixed 15-resource `produces:*` / `consumes:*` role vocabulary
(`analytics/interactions/vocab.py`) as the thing archetypes score against.

---

## 0. The idea in one paragraph

The archetype editor becomes a **rudimentary query builder over what the archetype
cares about**. Every role target is a **tag**, and every tag is exactly **one Scryfall
query string**. A tag has a label, a facet (IS / TALKS ABOUT / MAKES / AMPLIFIES /
FREE), and optionally a parent *target* ("Birds"). The facet templates that pre-fill a
query are scaffolding; the raw query box is always visible and editable, and changing
it is how you get anything the templates cannot express. Archetype Required Roles are
`(tag, min_count, weight)` pointing at those tags, and scoring reports **exactly which
cards matched which query**. No vocabulary is imposed: "Birds matter" is
`o:bird`, not `t:bird`, and the two stack as separate tags.

SYNTAX NOTE: in prose below, `o:bird` and `t:bird` stand for the Scryfall queries
`o:bird` / `t:bird`. Section 6 gives the literal query strings.

---

## 1. Data model

### 1.1 Target

A *target* is the noun the archetype cares about. It is only a grouping device: it
names the thing X so the UI can offer facet tags for X without a menu of 40 unrelated
queries. A target is never scored and never appears in a role.

```
Target {
  id:        "bird"                     # slug, stable key
  label:     "Birds"
  kind:      subtype|type|color|land|keyword|concept|enemy
  signal:    "bird"                     # the bare token/word used inside generated queries
  updated_at: ISO-8601
}
```

`kind` decides which facet templates the UI offers for that target. `signal` is the
word that gets substituted into the query templates (`bird` in `t:bird`, `o:bird`).

Kind → signals:

| kind    | signal example | what the templates do |
|---------|----------------|-----------------------|
| subtype | `bird`         | `t:bird`, `o:bird`, token regex |
| type    | `artifact`     | `t:artifact`, `o:artifact` |
| color   | `w`            | `id:w`, `o:/white/` |
| land    | `plains`       | `t:plains`, `o:landfall` note (see OQ-6) |
| keyword | `flying`       | `keyword:flying` (IS), `o:"flying"` (TALKS — see §6.8, these are different sets)
| concept | *(free)*       | no templates; a concept target starts as a blank query box
| enemy   | `vampire`      | "answers X" starters in the FREE facet: `o:/destroy target [^.]*vampire/`

A **concept** target (Historic, Lands Matter, Birds Matter, Vampires are the enemy) is
not a single `t:` type; its tags are free-form queries that may span several types
(see §6.7.11 for the Historic worked case).

### 1.2 Tag

```
Tag {
  id:            "bird:makes"                 # stable slug; see §1.5
  target_id:     "bird" | null                # null for an unparented free tag
  facet:         "IS" | "TALKS" | "MAKES" | "AMPLIFIES" | "FREE"
  label:         "Bird Generator"
  query:         "o:/creates? a [^.]*Bird creature token/"
  explanation:   "Cards that put a Bird token onto the battlefield."  # free prose, user-editable
  source:        "user" | "template" | "library" | "migrated"
  created_at:    ISO-8601
  updated_at:    ISO-8601
}
```

Rules that matter:

- **`query` is the only thing that decides membership.** `label` and `explanation` are
  display only. Two tags may share one query; that is legal (they are separate roles).
- **Tags stack and overlap.** The same card may carry `bird:is`, `bird:talks`,
  `bird:makes`, and `landfall` simultaneously. Nothing is exclusive, nothing is "the
  one true tag" for a card. A Birds deck can require both `bird:is` and `bird:talks`
  and a card that is a Bird and mentions Birds counts toward both.
- **No implicit union.** A role points at one tag = one query. If the owner wants
  "Birds or Bird tokens", that is one tag whose query is
  `t:bird or o:/creates? a [^.]*Bird creature token/` — the union must be written in
  the query, so the role's breakdown always shows which rule admitted a card.

### 1.3 Facet → template family

Facets are pre-fill templates, not types. They differ only in the query they seed:

| facet     | means | seed query for target `bird` |
|-----------|-------|------------------------------|
| IS        | is X  | `t:bird` |
| TALKS     | mentions/cares about X | `o:bird` (word) |
| MAKES     | produces X tokens / copies | `o:/makes? a [^.]*Bird/` or `o:/creates? a [^.]*Bird/` |
| AMPLIES   | makes IS/Makes-X better (anthem, doubler, lord) | start blank, hint: `o:/Birds you control get/` |
| FREE      | anything else; concept cuts | blank |

Templates are seeded **once**, at tag creation, into the editable box. They are never
re-applied afterwards — if better Scryfall syntax appears, it lands in a new tag or a
library update (ARCH-4), never by silently rewriting the user's saved query.

### 1.4 Target → facet tag spawning

Selecting a target X in the editor offers, in order, the facet tags that already exist
for X; below them, a "new tag for Bird…" row whose facet picker sets the query template:

```
X = "Birds" (subtype)
  IS          → t:bird
  TALKS       → o:bird
  MAKES       → o:/creates? a [^.]*Bird creature token/
  AMPLIFIES   → (blank; hint "Birds you control get")
  FREE        → (blank)

X = "Vampires" (subtype, used as an enemy)
  IS          → t:vampire
  TALKS       → o:vampire
  MAKES       → o:/creates? a [^.]*Vampire creature token/
  DESTROYS    → facet FREE, seeded o:/destroy target [^.]*vampire/
  WIPES       → facet FREE, seeded o:/destroy all [^.]*vampire/
```

DESTROYS/WIPES are named **starter queries in the FREE facet keyed by target kind**,
not facets of the data model: the enum stays IS / TALKS / MAKES / AMPLIFIES / FREE.
That is what makes "destroy target vampire" a role like any other instead of a
special-case, per the owner's anti-Vampire example. The same two starters appear for
any target kind with a creature-ish signal — which is the point: an anti-Artifact
archetype gets `o:/destroy all [^.]*artifact/` from the same mechanism.

### 1.5 IDs

`id` = `<target-id>:<facet-slug>` when the tag is generated for a target
(`bird:makes`); `free:<slug>` for unparented tags (`free:historic-matters`). Slugified
label, de-duplicated with `-2`, `-3` on collision. IDs are stable across query edits —
**a role references the tag id, never the query text**, so editing a query re-scores
every archetype using it instead of orphan-0ing the role.

### 1.6 Redis layout

Existing keys are untouched:

```
archetypes                      JSON list of archetype records (unchanged shape, roles now carry tag ids)
archetypes:version              int, bumped on write (unchanged)
archetype_sel:{registry_id}     per-deck selection (unchanged)
```

New:

```
role_tags                       JSON list of Tag records            (source of truth)
role_tags:version               int, bumped on any tag/target write
role_targets                    JSON list of Target records
tag_match:{tag_id}:{query_hash}:{deck_version}   JSON {names:[], excluded:[], total, built_at}
tag_query_index                 JSON {query_hash: [card names]}      shared across tags with the same query
tag_preview:{query_hash}        JSON {names:[], excluded:[], total} scratch, 1h TTL, for the editor preview
```

`query_hash` = first 16 hex of sha256 of the **normalized** query text (whitespace
collapsed, lowercased operators, see §2.4). Two tags with the same normalized query
share one evaluation.

`deck_version` = `decks:version`-style counter if present, else a hash of the deck's
`(name, oracle_id, quantity)` list. A deck edit changes the key; nothing else
invalidates a deck-scoped result.

Size: one tag per deck is a list of card names. A 100-card deck yields at most 100
names per tag (≈1–2 KB JSON). 20 roles × 60 decks = 1,200 small values — well inside
Redis's comfort zone, and none of them ever expire on their own (they are derived, so
they are safe to flush).

---

## 2. Matching

### 2.1 The two candidate engines

**(A) Search-then-intersect.** Run the tag's query against Scryfall `/cards/search`,
collect card **names**, intersect with the deck's card names.

**(B) Local predicate evaluation.** Store, per card, the data a query needs
(`type_line`, `oracle_text`, `keywords`, `color_identity`) and re-implement Scryfall
syntax against it.

**Decision: A, with B as the fast path for a small, enumerated operator set.**

Rationale, with numbers measured against the live Scryfall API on 2026-10-24:

| query | total_cards | final page (175/card) |
|---|---|---|
| `t:bird` | 463 | 3 |
| `o:bird` | 112 | 1 |
| `o:/creates? a [^.]*Bird creature token/` | 44 | 1 |
| `o:/destroy target [^.]*(creature\|permanent)/` | 702 | 5 |
| `otag:spot-removal` | 5,415 | 31 |
| `otag:tutor` | 1,168 | 7 |

Cost per *novel* query: one request per 175 results plus one. A no-match search is a
single 404. Median tag the owner will write is 1–2 requests; a broad `otag:` tag is
tens. **Second and subsequent evaluations of the same query cost zero** — the result
set is cached by `query_hash` in `tag_query_index`, and matching a deck is then a set
intersection in Python over ≤100 names.

Why not B: Scryfall's syntax is large (regex `o:/…/`, `otag:`, `is:`, `-`, `or`/`and`,
`cmc`, `pow`, `set`, `format`, `lang`…). Re-implementing it means the preview count and
the scored count may disagree after any Scryfall release, which breaks the one promise
the whole feature makes — that the query box means what it says. Divergence was real in
this design's own verification: `/cards/search`'s `otag:` universe is only *partly* the
Tagger universe. All 85 otags the legacy vocabulary uses resolve (so migration is safe),
but plausible Tagger names like `otag:bird-matters`, `otag:tutor-selective` and
`otag:counter-payoff` return 404, and some distinct names collapse onto one alias set
(`boardwipe`/`sweeper` = the same 947 cards; `otag:spot-removal` 5,415 vs
`otag:removal` 6,465). A local re-implementation would have to reproduce that alias table
and would drift from it. So the query text must be evaluated by the same engine the
preview used — the Scryfall API — not by a second interpreter (see OQ-1).

### 2.2 Where the LLM tagger stops and stays

Stops, entirely, for role scoring:

- IS is `t:`, TALKS is `o:`, MAKES is a token regex, DESTROYS/WIPES/EXILES are oracle
  regexes, tutors are oracle regexes, Historic is an `o:/historic/` concept query.
- Every one of those is deterministic and re-derivable from the card's public oracle
  data. The deterministic pass already in `tagger.py` (`oracle_rules.tag_from_oracle`)
  covers a slice of it; nothing in role scoring needs the constrained-LLM
  `produces`/`consumes` judgement anymore.

Stays, unchanged, for prose and for the legacy graph:

- The interaction graph (`produces:*`/`consumes:*` edges, engines, combos) keeps its
  LLM tagger for as long as the graph exists — see §3.3. A new tag does **not** feed
  the graph.
- Free-prose explanations and engine naming still use the LLM (unchanged).
- **Optional:** an "ask the LLM for a query" button in the editor that proposes a
  *seed query* from a natural-language description ("cards that punish vampires").
  It writes text into the visible box; it never decides membership. Ship it in a later
  card, and only after the deterministic path works. Recommendation in OQ-2.

### 2.3 Evaluation algorithm

```
evaluate_tag(tag, deck_names) -> {matched: [...], excluded: [...], total: int}

  q  = normalize_query(tag.query)
  idx = tag_query_index[q]                      # Redis, no TTL, semantic key below
  if idx is None:
      idx = scryfall_search_all(q)              # names only, unique=cards
      tag_query_index[q] = idx
  matched  = [n for n in deck_names if n in idx]
  excluded = [n for n in deck_names if n not in idx]   # only shown in breakdown on demand
```

`deck_names` comes from the deck record (`registry` deck cards or the `decks` blob) —
one read, no Scryfall. `scryfall_search_all` pages with `unique=cards`, ~75 ms sleep
between pages, honours `Retry-After` on 429, and stops at a hard cap (§2.6).

**Copy counts.** The existing `archetype_match` counts *copies*, not distinct names.
That stays: `have = Σ quantity(card) for card in matched`. The breakdown shows both
(`3 copies · 2 cards`).

### 2.4 Query normalization (cache key)

Normalize before hashing, so whitespace/paren noise does not fork the cache:

- collapse runs of whitespace to one space; trim
- strip a trailing `;`
- sort nothing, reorder nothing else (Scryfall's `or`/`and` are positional)
- hash = sha256(normalized)[:16]

Every write of a `Tag.query` stores the normalized text and its hash. Editing a query
therefore gets a fresh hash and never reads a stale index entry.

### 2.5 Refresh policy

| thing | lifetime | why |
|---|---|---|
| `tag_query_index[q]` | **no TTL**, refreshed on demand | Scryfall's card pool only grows; a stale index means new cards are missing from roles until refresh |
| refresh trigger | (a) any editor "Refresh" click; (b) a nightly cron that re-runs **each distinct tag query once** and rewrites entries whose name set changed; (c) a tag query edit (new hash, so the old entry just goes cold) | one Scryfall pass per night over the *distinct* query set, not per deck, and not per sync |

Nightly pass cost: at 20 distinct tags, ~20-60 search runs; worst case ~400 pages if
several tags are broad. The number of distinct queries is bounded by `archetypes x roles`
(at most 12 x 6 = 72 under the current `MAX_ROLES`, realistically far lower). **This is
unverified against the real registry** — the local Redis has no archetypes yet, so ARCH-3
must count the actual distinct queries before enabling the cron. Tags refreshed inside a
20-hour window are skipped, so a manual refresh also resets the clock.

Stale-index consequence, stated plainly: a card released after the last refresh is
**missing from every role** until the next one. That is a bounded, visible error (the
breakdown names the matched cards) and strictly cheaper than re-searching every deck on
every page load.

Set-level semantics: Scryfall search results are the *currently legal-in-some-format,
paper-or-digital* printings of cards; banned cards still exist. A tag query that
matches a banned card is fine — the deck already holds the card, and role scoring is
not a legality check.

### 2.6 Guardrails

- **Hard result cap** `TAG_MAX_RESULTS = 5000` (≈29 pages). Above it the tag is
  stored but marked `oversized: true` and **excluded from scoring** with a visible
  editor warning: "this query matches 8,185 cards, narrow it". `(t:artifact or t:legendary
  or t:saga)` at 8,185 already trips it — which is the correct answer: "spot removal" as a role should
  be a narrower query (§6.7.6).
- **Query errors never score 0 silently.** A 400/404 from Scryfall stores
  `last_error` on the tag and the role shows `blocked`, not `missing`. See §3.5.
- **`not found` handling** on `/cards/search` is a 404 — that is "0 cards", the normal
  result for a strict query, and must NOT be treated as an error. Distinguish by
  reading the error body's `status`: 404 = empty result, 400 = bad syntax.
- **Private/illegal-only cards** in a deck (un-sets, banned) still intersect fine if
  the query matches them; if a card name is not on Scryfall at all (nicknamed/spelled
  differently in Archidekt), it is listed in the breakdown's "not found on Scryfall"
  group rather than being silently dropped.

---

## 3. Migration

### 3.1 Inventory of what holds the old vocabulary today

| where | what | fate |
|---|---|---|
| `analytics/interactions/vocab.py` `RESOURCES` | 15 fixed resources, `otag:` lists per side | **kept** for the interaction graph; no longer read by archetypes/genres role code |
| `analytics/archetypes.py` `role_vocabulary()` | dropdown source | replaced by `role_tags.all_tags()` |
| `analytics/archetypes.py` `clean_roles()` / `normalize_tag()` | regex `produces\|consumes:<key>` | replaced by `clean_roles()` over tag ids, with a legacy-accepting shim (§3.4) |
| `analytics/archetypes.py` `archetype_match()` | counts `produces:`/`consumes:` per card | replaced by tag evaluation (§4) |
| `analytics/genres.py` | duplicate of the same layer (its own `role_vocabulary`, `normalize_tag`, `clean_roles`) | same replacement; consider deleting the duplicate in favour of importing from `archetypes` (ARCH-3) |
| `card_interactions:{name}` (`vocab_version: 1`) | per-card `produces`/`consumes` + evidence | **kept, not migrated, not bumped** (§3.3) |
| `interaction_graph:collection`, `card_interactions:*` consumers | graph, engines, combos, bracket, writeup, journal, insights, personality | **kept, unchanged** (§3.3) |
| saved `archetypes` blobs | `required_roles: [{tag: "produces:tokens", ...}]` | **migrated** to tag ids (§3.2) |
| `archetype_sel:{id}` | selection by archetype id | unchanged |

### 3.2 Legacy role → tag mapping

Each of the 15 resources generates **two** tags from its existing `otag:` lists, i.e.
exactly the sets the current scoring counts — so a migrated archetype's score is
unchanged, not "recomputed by a new algorithm".

```
produces:<key>  → Tag id "legacy:produces:<key>"
                  query  = "(otag:a or otag:b or …)"      # the Resource.produce_otags list
                  label  = "<Resource.label> (produces)"
consumes:<key>  → Tag id "legacy:consumes:<key>"
                  query  = "(otag:a or otag:b or …)"      # the Resource.consume_otags list
```

**All 85 `otag:` names in `RESOURCES` resolve on `/cards/search`** (audited live
2026-10-24: 85 checked, 0 returned 404). So 28 of the 30 legacy tags are verbatim
mappings and score *identically* before and after migration. The two exceptions:

- `produces:spells` (`produce_otags = ()`) — "is or copies an instant/sorcery spell".
  An empty otag list means this role **already scores 0 for every deck today**:
  `otag_queries()` skips empty lists and `oracle_rules` never emits the `spells` key.
  Migration maps it to `t:instant or t:sorcery` so it starts working. That is a real
  behaviour change (a saved archetype gets *better*, never worse) and it belongs in
  CHANGELOG under `### Fixed`, not under Changed.
- `produces:death` uses a single otag (`sacrifice-outlet-creature`) — maps trivially.

Thinnest legacy tags, for the owner's awareness when choosing min_counts (these count
few cards, so a role of min_count 3 is effectively unsatisfiable):
`affinity-for-tokens` 1, `synergy-leaves-creature` 3, `synergy-proliferate` 7,
`prowess-anthem` 8, `flicker-permanent` 15, `lifegain-increaser` 15,
`token-doubler` 17, `repeatable-enchantment-tokens` 28.

Note these are *card-level* counts of the tag query, not deck counts; a card in the
breakdown proves the mapping, and §3.2's real gate is the per-archetype equality test.

Migration is **read-time and idempotent** — no Redis rewrite script, no downtime:

```
_ensure_legacy_tags(r):
    if role_tags exists: return                  # already migrated, nothing to do
    build the 30 legacy Tags (source="migrated") from RESOURCES
    seed role_targets from Resource keys that read as types (artifacts, enchantments, lands)
    write role_tags, bump role_tags:version
```

and in `clean_roles()` / the archetype read path:

```
def resolve_role_tag(raw_tag, tag_index):
    if raw_tag in tag_index:        return raw_tag        # already a tag id
    if looks_like_legacy(raw_tag):  return legacy_id(raw_tag)   # produces:tokens -> legacy:produces:tokens
    raise ArchetypeError(...)                             # unknown, refuse the write
```

Saved archetypes are **not rewritten on read**. They keep `produces:tokens` in Redis;
resolution happens at score time. A later, explicit `scripts/migrate_archetype_roles.py`
(ARCH-3, dry-run first, prints a diff per archetype) rewrites the blobs once the tag
system is live; until then both spellings score identically. That is the backwards-compat
guarantee: **no saved archetype can score 0 because of this change.**

Verification gate for ARCH-3 (must be a test, not a claim): for every saved archetype,
`score_old(deck) == score_new(deck)` for all 60-ish decks before the legacy path is
retired.

### 3.3 Interaction-graph consumers — what actually changes

The task lists several modules as graph readers. Reading the code, most of them do not
touch `produces:`/`consumes:` at all:

| consumer | reads | change |
|---|---|---|
| `analytics/interactions/service.py` (`get_deck_graph`, `build_deck_graph`, collection graph) | tagger cache, graph | **none** |
| `analytics/interactions/graph.py`, `engines.py` | `RESOURCE_BY_KEY` labels | **none** |
| `bracket_check.py` | `card_interactions:{name}` for **oracle text** / infinite-turn prose, and combos | **none** (it uses the cache as an oracle-text store, not as tags) |
| `deck_writeup/context.py: _oracle_cache` | `card_interactions:{name}` `.card` for card text | **none** |
| `insights.py` | `card_interactions:*` `.card` text + engine picks | **none** |
| `journal.py` | `get_deck_graph` for the graph snapshot | **none** |
| `personality.py` | combos + prompts | **none** |
| `genres.py` | duplicates the archetype role layer | **changed** (§3.1) — same treatment as `archetypes.py` |
| `analytics/archetypes.py` | `archetype_match` on graph roles | **changed** (§4) |

So the migration surface is two modules, not eight: `archetypes.py` and `genres.py`.
Everything else reads the `card_interactions:` cache as a card-text/evidence store and
keeps working untouched. **Recommendation (OQ-3): do not bump `VOCAB_VERSION`.** Bumping
it retags every card with an LLM pass and recomputes the whole graph to produce
identical results — pure cost. The cache keeps its meaning; it just stops being the
source of archetype roles.

### 3.4 Validation changes

```
old: normalize_tag("Produces: Sacrifice") -> "produces:sacrifice" | None
new: resolve_role_tag("produces:sacrifice") -> "legacy:produces:sacrifice"   (alias accepted, deprecated)
     resolve_role_tag("bird:makes")         -> "bird:makes"
     resolve_role_tag("o:bird")             -> error with hint "that looks like a query; create a tag first"
```

`MAX_ROLES = 12`, `MAX_MIN_COUNT = 40`, `MAX_WEIGHT = 10`, duplicate-tag rejection and
the merge-out behaviour all stay.

New limits: `MAX_QUERY_LEN = 400` chars (Scryfall's own practical bound; long regexes
are a prompt to split the tag), `MAX_TAGS = 200`, `MAX_TARGETS = 60`,
`MAX_LABEL = 60`.

### 3.5 Failure states a role can be in

```
ok        count >= min_count
short     count <  min_count        (have 3, need 4)
empty     0 matches, query valid    (the query is simply too strict)
blocked   query failed (400 syntax, oversize, Scryfall down) — never shown as "0"
```

The current UI renders `empty` and `blocked` identically, which is exactly how a saved
archetype "silently scores 0". Fixing that is part of this change (§5.4).

---

## 4. Scoring

### 4.0 Naming: there is no archetype `score_deck`

The card this spec implements says "the concrete change to `score_deck`". Verified on
`origin/main`: the archetype scorer is **`archetype_match`** in
`analytics/archetypes.py`. `score_deck` (in `analytics/personality.py`) is a *different*
function — it gathers deck-personality inputs from Redis (commander, curves, journal) and
returns `PersonalityScores`. It knows nothing about roles and must not be touched by this
work.

So: **§4 is the change to `archetype_match`**, and nothing in this spec modifies
`analytics/personality.py`. If ARCH-3's implementer greps for `score_deck` and starts
editing `personality.py`, they are in the wrong file. This note exists because the
source card conflated the two names.

### 4.1 What changes in `archetype_match`

Current:

```
counts["produces:"+k] += qty          # from the deck's interaction-graph nodes
have = counts.get(role["tag"], 0)
```

New:

```
matched = tags.evaluate(tag_id, deck_names)     # §2.3, memoized per (tag, deck)
have    = sum(quantity[n] for n in matched)
```

Everything else — `coverage = min(have/min_count, 1)`, weighted mean, 4 dp, tie-break
on name then id, "archetype with no roles scores 0" — is unchanged. Signature becomes:

```
archetype_match(deck_cards, archetypes, tag_store) -> [{
    archetype: {id, name},
    coverage: 0.8712,
    roles_hit:     [{tag, label, query, have, cards:[{name, qty}], min_count, weight}],
    roles_missing: [{tag, label, query, have, cards:[...], min_count, weight, state: "short"|"empty"|"blocked"}],
}]
```

`deck_from_graph()` goes away for this path (it existed to get `produces`/`consumes`
onto card records). The new input is the deck's plain card list — `[{name, quantity}]`
plus the commander flag — which `registry` already provides.

### 4.2 Cost per scoring run

Per deck: `O(roles)` Redis reads for the cached tag index (one `MGET` over hashes) +
set intersection over ≤100 names. **No Scryfall calls** once the index is warm. Cold
index = §2.1 numbers, paid once per distinct query globally.

Ranking all archetypes for one deck: `O(archetypes × roles)` intersections ≈ 12 × 6 =
72 set operations over ≤100-element lists — microseconds, well inside the existing
request path.

### 4.3 The breakdown the UI shows

Per role:

```
Bird Generator        o:/creates? a [^.]*Bird creature token/     3 copies · 2 cards   ✓
  Aether Channeler, Emeria Angel
Birds you control get  o:/Birds you control get/                  1 copy  · 1 card     short (need 2)
  Kangee, Sky Warden
Enemy vampires        (blocked: query error)                       —                   blocked
```

The card names are the whole point: they are what makes a score explainable instead of
trusted. Clicking a role expands the full matched list plus, on demand, the deck cards
that did **not** match.

---

## 5. UI

### 5.1 Editor flow

```
[Archetype name]  [description ...]

Required roles                                   + Add role
┌──────────────────────────────────────────────────────────────────────────┐
│ Target   [Birds ▾]  (+ New target…)     Facet [MAKES ▾]                  │
│ Query    ┌──────────────────────────────────────────────────────────┐    │
│          │ o:/creates? a [^.]*Bird creature token/                  │    │  ← always visible
│          └──────────────────────────────────────────────────────────┘    │
│ Preview against [My Deck ▾]    3 copies · 2 cards  [Show]    [Refresh]   │
│ Min [ 2 ]   Weight [ 1.0 ]                                   [Remove]    │
└──────────────────────────────────────────────────────────────────────────┘
```

Step by step:

1. **Pick X.** The Target select lists targets, not queries. Selecting one offers that
   target's existing tags below the select as chips (`IS · TALKS · MAKES · AMPLIFIES`
   and any FREE tags authored for it). Picking a chip = "add this tag as a role".
2. **Or make a new tag.** "New tag for Birds…" opens the row with a facet picker that
   seeds the query template (§1.3) and puts the cursor in the query box.
2b. **Or skip targets entirely.** "+ Add role" creates a FREE tag with a blank box, so a
   one-off query never requires inventing a target first.
3. **Edit the query.** The box is never hidden, never read-only, never replaced by the
   dropdown. A "syntax" link opens the Scryfall reference. Below the box: the tag's
   `explanation` (editable prose, for the human), and the seeded template's origin
   ("seeded from MAKES template — edited").
4. **Preview.** Choosing a real deck from the deck list runs the query intersection
   against *that deck's* cards and shows `N copies · M cards [Show]`. This is the same
   code path scoring uses (`/api/role-tags/preview` calls `evaluate_tag` with a deck's
   names) — not a separate count. Empty preview on a valid query shows "0 of this deck's
   cards match" with the deck name spelled out, so "0" is never ambiguous.
5. **Min / weight** as today.
6. **Save** writes the tag first (if new) then the archetype, so a saved role always
   points at an existing tag.

### 5.2 Archetype detail view

Under each role, the breakdown from §4.3, collapsed to one line, expandable to the
matched card names. This view is what makes "why is my archetype 78%" answerable.

### 5.3 Tag management

`/archetypes` gains a second pane (or `/role-tags`) listing every tag with target,
facet, label, query, and "used by N archetypes". Editing a query there re-scores every
archetype that points at it — with a confirmation that names them ("changes will
re-score 3 archetypes"). Deleting a tag still used by an archetype is refused with the
list, unless the archetype's role is removed first.

### 5.4 The "blocked" state

`blocked` roles render amber with the Scryfall error text and the archived last-good
count ("last evaluated 12 min ago: 3"). Deprecation of a legacy alias tag renders with a
"migrated" chip and a link to the equivalent new tag. Neither is ever rendered as a
bare 0.

---

## 6. File / function change list and worked examples

### 6.1 New module — `analytics/role_tags.py`

| function | purpose |
|---|---|
| `load_tags(r)` / `save_tags(r, tags)` / `load_targets(r)` / `save_targets(r, targets)` | Redis CRUD on `role_tags`, `role_targets`, versioned |
| `all_tags(r)` | list for the editor + validation index |
| `create_tag(r, body)` / `update_tag(r, tag_id, body)` / `delete_tag(r, tag_id)` | validation (`MAX_QUERY_LEN`, label, facet enum, target exists), bump version |
| `normalize_query(q)` / `query_hash(q)` | §2.4 |
| `seed_query(target, facet)` | §1.3 templates; returns "" for AMPLIFIES/FREE |
| `search_all(q, source)` | Scryfall paging, 429/Retry-After, 404→empty, 400→error, cap §2.6 |
| `query_index(r, q, source, refresh=False)` | `tag_query_index` read/refresh |
| `evaluate_tag(r, tag, deck_cards, source)` | intersection + copy counts, `state` |
| `preview(r, query, deck, source)` | `/api/role-tags/preview` backing |
| `legacy_tags()` / `_ensure_legacy_tags(r)` | §3.2 |
| `resolve_role_tag(raw, index)` | §3.4 alias shim |

### 6.2 Changed — `analytics/archetypes.py`

- `role_vocabulary()` → returns `role_tags.all_tags()` (keep the name so `genres.py`
  and the API keep working during transition; drop it after).
- `normalize_tag()` → thin wrapper over `resolve_role_tag()`; deprecate.
- `clean_roles()` → validates tag ids via the registry, unchanged numeric rules.
- `archetype_match(deck, archetypes)` → `archetype_match(deck, archetypes, tag_store)`,
  §4.1; drop `deck_from_graph`, add `role_breakdown`.
- `resolve_for_deck()` → threads the tag store through; one `MGET` of tag indexes.
- `_save()` → bump `archetypes:version` as today.

### 6.3 Changed — `analytics/genres.py`

Delete its duplicated `role_vocabulary` / `normalize_tag` / `clean_roles` bodies and
import from `role_tags` (it drifted from `archetypes.py` once already; a third copy is
not worth maintaining). Same `archetype_match` change.

### 6.4 Changed — `app.py`

```
GET  /api/role-tags                     -> {tags, targets, version}
POST /api/role-tags                     -> create tag
PUT  /api/role-tags/{tag_id}            -> edit (reports affected archetypes)
DEL  /api/role-tags/{tag_id}            -> delete (refuses if in use)
POST /api/role-tags/preview             -> {query, deck_name} -> {total, matched:[{name,qty}], error?}
POST /api/role-tags/{tag_id}/refresh    -> re-run the query, rewrite the index entry
GET  /api/archetypes/{id}/score?deck=   -> per-role breakdown for one deck
```

`GET /api/archetypes` keeps returning `vocabulary` (now tag records) so the template's
`state.vocab` contract only needs a shape update.

### 6.5 Changed — `templates/archetypes.html`

`roleRow()` gains target + facet selects, the always-editable query textarea, and the
preview row; `readRoles()` reads `{tag_id, min_count, weight}`. `state.vocab` becomes
`state.tags` + `state.targets`. New CSS in the existing `hud.css` token vocabulary
(`.ar-role` grid grows to 6 columns desktop / stacked mobile).

### 6.6 Unchanged files (explicitly)

`analytics/interactions/vocab.py`, `tagger.py`, `graph.py`, `engines.py`, `service.py`,
`llm.py`, `oracle_rules.py`, `bracket_check.py`, `insights.py`, `journal.py`,
`personality.py`, `deck_writeup/context.py`. `sync.py` needs **no** change: the card
data a query needs is already fetched during sync (`card:{oracle_id}` carries `type`,
`types`, `sub_types`, `keywords`, `oracle_text`; `card_meta:{name}` carries the same by
name), and matching uses Scryfall search, not local text.

### 6.7 Worked examples — exact query strings

Every query and count below was **executed against the live Scryfall API on 2026-10-24**
(`unique=cards`, totals are that day's `total_cards`). Counts move as sets release; the
durable content is *which query form expresses which facet*.

#### 6.7.1 Birds — IS X

```
t:bird                                        → 463 cards
```

#### 6.7.2 Birds Matter — TALKS about X, as its own stacking tag

```
o:bird                                        → 112 cards
```

This is deliberately **not** `t:bird`. A Bird that doesn't mention Birds is not a
Birds-Matter card; a non-Bird that does ("whenever a Bird you control attacks") is.
`Aerie Worshippers` is in `o:bird` and not in `t:bird`. A Birds archetype requires both
tags at different weights — allowed because tags stack, not mutually exclusive.

#### 6.7.3 Bird Generator — MAKES X

```
o:/creates? a [^.]*Bird creature token/       → 44 cards
```

Breadth-first on purpose. The literal phrase `o:"create a 1/1 white Bird creature
token"` finds only **12** and misses cards that phrase the token differently. The regex
is the template; the literal is the query the owner will write first and then edit once
the preview count looks too low — which is exactly the intended editor loop.

#### 6.7.4 Anti-Vampire — destroy one (owner example)

```
o:/destroy target [^.]*vampire/               → 3 cards
```

Literal-form comparison, kept because it motivates the regex template:

```
o:"destroy target vampire"                    → 2 cards
```

The regex is **one** card broader (`Slayer of the Wicked`-style phrasings that put words
between "target" and the type). The query-builder rule: seeds are regex templates, and
the raw box shows the literal string the owner can tighten to, not the other way round.

#### 6.7.5 Anti-Vampire — destroy all (owner example)

```
o:/destroy all [^.]*vampire/                  → 0 cards
o:/destroy all [^.]*vampires/                 → 0 cards
```

**The honest result is zero.** No card in Scryfall's pool reads "destroy all Vampires".
The tag is still valid, searchable, and scorable — it is simply a role the deck cannot
fill yet. This is why §3.5 distinguishes `empty` (valid query, 0 matches) from `blocked`
(query error): an anti-Vampire archetype that asks for the wipe shows **"0 — no card
matches this query"** in amber, never as a silent 0. That distinction is the single most
important consequence of the owner's anti-Vampire example, because the interesting
answer is "that role is unwinnable", not "that role is worth 0".

Working wipes for comparison:

```
o:/destroy all creatures/                     → 100 cards
o:/destroy all [^.]*creatures/                → 161 cards
```

#### 6.7.6 Spot removal — the owner's fine cuts

Each cut is a **separate tag**, which is the whole point: they are different roles with
different min_counts, not one "removal" bucket.

```
spot: destroys a single permanent, any type
  o:/destroy target [^.]*(creature|permanent)/               → 702 cards

spot: exiles a single permanent, any type
  o:/exile target [^.]*(creature|permanent)/                 → 419 cards

spot: destroy OR exile, single permanent                      → union tag
  o:/destroy target [^.]*(creature|permanent)/ or o:/exile target [^.]*(creature|permanent)/
                                                              → 1,120 cards

non-selective spot (will hit any creature):
  o:"destroy target creature"                                → 379 cards
```

#### 6.7.7 Selective spot removal

Selectivity = the query names the types it answers:

```
o:/destroy target (enchantment|artifact)/      → 397 cards
```

This is the "destroy target enchantment" cut the owner asked for: it answers enchantments
and artifacts and nothing else. Contrast `o:"destroy target creature"`
(379, non-selective). Both are valid roles; the difference is only the query text, which
the breakdown makes visible card by card.

**Syntax trap found while probing** — worth putting in the editor's inline help:
`o:/destroy target (creature|permanent)` returns **404** (the regex needs its closing
slash and, for alternation over variable-length phrases, a wildcard anchor).
`o:/destroy target [^.]*(creature|permanent)/` is the 702-card form. Alternation inside
`o:/…/` is legal; the returns-404 case is a missing terminator, and the live preview
(§5.1 step 4) catches it before save.

#### 6.7.8 Exile vs destroy, side by side

```
o:"exile target" -o:"exile all"                → 605 cards
o:/exile target [^.]*(creature|permanent)/     → 419 cards
```

Different sets, different roles. An archetype that needs "answers creatures without
letting them come back" scores `exile`, not `destroy` — the owner gets this distinction
for free once each is a tag.

#### 6.7.9 Symmetric vs asymmetric wipe

```
symmetric (hits everything, no controller restriction):
  o:/destroy all [^.]*creatures/                        → 161 cards
  o:/destroy all [^.]*/ -o:/each player/ -o:/you control/ → 223 cards
  (the 223 form additionally captures non-creature wipes: lands, artifacts, all permanents)

asymmetric (only your opponents lose their board):
  o:/destroy all creatures you don.t control/           → 2 cards (In Garruk's Wake, Plague Wind)
  o:/each player sacrifices/                            → 72 cards
  o:/exile all [^.]*creatures/                          → 33 cards
```

Load-bearing caveat, stated in the tag explanation rather than hidden: most
"asymmetric" wipes in Magic are worded as *each opponent sacrifices* or *each player
sacrifices*, not "destroy all creatures you don't control". The 2-card literal form
understates the real cut by an order of magnitude. **Recommendation for the starter
library (ARCH-4):** seed asymmetric wipe as
`o:/destroy all creatures you don.t control/ or o:/each opponent sacrifices a creature/`
and let the owner see the card list decide. Scryfall's oracle text uses a typographic
apostrophe, so regexes over possessives must use `.` for it (`don.t`, not `don't`).

#### 6.7.10 Selective vs non-selective tutor

Non-selective tutor = you reveal what you get, so opponents see it:

```
o:/search your library for [^.]*reveal/        → 349 cards
```

Selective tutor = named-type or name-your-card search:

```
"search your library for a card" with NO reveal:  o:/search your library for a [^.]*card/ -o:/reveal/
                                                   → 341 cards
named type:                                     o:"search your library for a creature card"
                                                   → 47 cards
```

The 349-person and 341-person sets are a **clean partition** (verified: zero overlap,
349 + 341 = 690 of the 829 cards matching `o:/search your library for/`). That makes the
reveal/not-reveal split the honest mechanical proxy for the selective/non-selective cut.
Caveat for the tag explanation: "reveal" is a strong indicator, not a proof — `Demonic
Tutor` is in the 341 side and is the archetypal selective tutor, so the split behaves
correctly there, while a few edge cases will land on the wrong side. The builder's answer
to an edge case is to edit that tag's query, not to add a carve-out to the model.

#### 6.7.11 Historic matters (owner example — a concept spanning types)

Historic is a keyword action over artifacts, legendaries and sagas, so "any rules text
referencing historic things" is an **oracle-text concept**, not a type:

```
o:/historic/                                   → 58 cards
```

```
is historic (for reference only):
  (t:artifact or t:legendary or t:saga)        → 8,185 cards
```

The 8,185 form is the reason concepts need the FREE facet rather than a `t:` template:
"Historic matters" is 58 cards, "is historic" is 8,185, and they are not the same role.
The 8,185 query also trips the 5,000 cap (§2.6) and is marked oversized, which is the
correct outcome — "half the card pool is in this role" carries no archetype signal.
A tag that cares about artifacts-as-historic specifically is written as
`o:/historic/ -t:artifact` (49 cards) — a query the owner writes in the box once they see
the 58-card breakdown and judge the artifact-heavy cards redundant.

#### 6.7.12 Lands Matter (free-form concept, per the brief)

```
o:/landfall|whenever a land enters/            → 212 cards
```

No `t:` type behind it — the same shape as `o:/historic/`. This is the concept the
owner named ("Lands Matter") and it demonstrates that a target can be a concept whose only
facet tag is FREE.

#### 6.7.13 Related: how these compose into one Birds archetype

```
required_roles = [
  {tag: "bird:is",    min_count: 12, weight: 1.0},   # t:bird
  {tag: "bird:talks", min_count: 4,  weight: 2.0},   # o:bird  (the archetype's identity)
  {tag: "bird:makes", min_count: 1,  weight: 1.0},   # bird-token generator
]
```

A card in both `t:bird` and `o:bird` counts toward both roles — stacking, as specified.
Coverage = weighted mean of `min(have/min_count, 1)`, unchanged from today (§4.1).

### 6.8 Keyword and colour targets, for completeness

Both operators exist and the two senses of a keyword are **different sets** — verified:

```
keyword:flying                                 → 3,291 cards   (the keyword itself, rule 702.9)
o:"flying"                                     → 4,590 cards   (every mention, incl. "creature with flying")
```

So `keyword:flying` is the correct IS seed for a keyword target, and `o:"flying"` is the
TALKS seed — exactly the same IS/TALKS split as Birds, expressed with the operator
Scryfall actually provides for it. (An earlier draft of this spec claimed `keyword:` was
not an operator; it is. The editor must still verify each seed's non-zero count once at
first use rather than trust a hardcoded assumption.)

```
target kind=color, signal w:   IS → id:w      TALKS → o:/white/       (weak; prefer concepts)
```

---

## 7. Open questions for the owner

Each with a recommendation; none blocks writing code, all block merge of ARCH-3's
first PR if unresolved.

**OQ-1 · `otag:` is only *partly* the Tagger universe.**
Audited live: all **85** otags used by `RESOURCES` resolve, so the migration is safe.
But `/cards/search` is not the same tag universe as Scryfall Tagger itself — several
plausible names 404 (`bird-matters`, `tutor-selective`, `counter-payoff`,
`graveyard-matters`), and some distinct names collapse onto one alias set
(`boardwipe`/`board-wipe`/`mass-removal`/`sweeper` all return the same 947 cards;
`spot-removal` 5,415 vs `removal` 6,465).
→ **Recommendation:** keep every migrated `legacy:*` tag on its verified `otag:` query
(nothing to fix, scores don't move), and make new templates seed `t:`/`o:`/regex only.
Add an editor rule: a query containing `otag:` is validated once at save (fetch the
count); if it 404s, the tag is saved as `blocked` with the message "Scryfall does not
recognise this tag name", never as 0.
**Question:** do you want `otag:` offered at all in the facet templates? My reading is
no — it is a Tagger concept the search API only partially honours, and silently
partial matching is the one failure mode worst for a scoring system.

**OQ-2 · LLM-authored seed queries.**
An "describe it in words → propose a query" button is cheap and would help the
"rudimentary" half of the query builder.
→ **Recommendation:** ship it late and visibly non-authoritative (fills the box, never
saves a tag). Not in ARCH-3. Confirm you want it at all.

**OQ-3 · `VOCAB_VERSION`.**
Retagging all cards to drop `produces`/`consumes` costs a full LLM pass for zero
functional gain while the interaction graph still needs them.
→ **Recommendation:** leave `VOCAB_VERSION = 1` and the cache alone indefinitely; the
graph and role tags are parallel readers of the same card text. Only bump if the graph
itself changes.

**OQ-4 · Result cap.**
`TAG_MAX_RESULTS = 5000` refuses to score "is historic" (8,185) and "spot removal"
(5,415).
→ **Recommendation:** keep 5,000 and make "oversized" a visible editor warning. A role
that matches half the card pool carries no information. Alternative is to allow
oversized tags to score (they'd make every archetype ~100% coverage on that role,
which is noise). Confirm.

**OQ-5 · Deck scope.**
Does a role count the whole deck, or mainboard-only? Today `archetype_match` uses
whatever graph the deck has, which includes the commander.
→ **Recommendation:** mainboard + commander, excluding maybeboard/considering (matches
`decks.py` slot semantics already established). Confirm the commander's double-count
rule ("commander counts once toward a role") — currently it does.

**OQ-6 · Land targets.**
"Land type X" has two unrelated senses: *is a Plains* (`t:plains`) and *landfall /
lands-matter* (`o:/lands? you control enter/`). Same word, different facet, both valid.
→ **Recommendation:** for `kind=land`, offer IS → `t:<signal>` and a FREE starter
`o:/lands? you control enter/`, and put "Lands matter" in the library as a concept
rather than pretending land targets have a uniform facet set.

**OQ-7 · What happens to `genres.py`'s copy?**
It duplicates `archetypes.py`'s role layer and has already drifted.
→ **Recommendation:** delete the duplicate and import from one module. Confirm you're
happy for the Deep Review "Deck Tags" feature and the Archetype feature to share one tag
registry — the module docs currently insist they "must not be conflated".

---

## Appendix A — what this spec deliberately does not decide

- The **starter library of queries** (ARCH-4). This spec names the mechanism; every
  query above is an example that ARCH-4 may replace wholesale.
- Whether tags carry a `min_count` default per facet (e.g. MAKES defaults to 2). I'd
  default all facets to `1` and let the library send defaults, so the data model stays
  free of per-facet semantics.
- Card art/legend rendering for breakdown rows.
- Anything about Moxfield/Archidekt export of tags.

## Appendix B — verification notes

Every query, count and claim marked "verified" in this document was executed against
`https://api.scryfall.com/cards/search` (`unique=cards`) from this machine on 2026-10-24;
totals are that day's `total_cards`. Exactly what was run:

- **Facet and cut queries** in §6.7 and §6.8 — all counts as printed.
- **`otag:` audit** of `analytics/interactions/vocab.py` — 85 distinct otags extracted
  from `RESOURCES`, each searched: 85 resolved, 0 returned 404. Thinnest tags listed in
  §3.2.
- **Rate limiting is real, not theoretical** — a burst of ~25 distinct searches in one
  minute from one machine returned HTTP **429 with a 60-second block warning** from
  Scryfall. That is the empirical justification for `tag_query_index` (§2.5): without a
  persistent per-query cache, scoring a registry of dozens of roles across dozens of decks
  would rate-limit the app into failure. The probe was re-run at 1.2 s spacing after the
  back-off and every count reproduced exactly.
- **Tutor partition** — name sets of `o:/search your library for [^.]*reveal/` (349) and
  `o:/search your library for a [^.]*card/ -o:/reveal/` (341) fetched in full and
  intersected: **zero overlap**.
- **`keyword:` operator** — confirmed to exist as a real search operator
  (`keyword:flying` 3,291 vs `o:"flying"` 4,590). An earlier draft wrongly claimed it
  did not; the draft was corrected, and this note records why the claim is now trusted.
- **Redis key collisions** — the live `commander-lab-redis` currently holds
  `interaction_graph:collection`, `interactions:runs`, `lab_tags`, `lab_tags_source`,
  `comp:presets`, and two github-version markers. None of the proposed `role_tags`,
  `role_tags:version`, `role_targets`, `tag_match:*`, `tag_query_index`,
  `tag_preview:*` keys are in use.
- **Not verified, and flagged as such in the text:** the number of distinct tag queries
  the real registry will hold (local Redis has no `archetypes` key yet); the per-archetype
  old-vs-new score equality gate, which is ARCH-3's test to write, not this spec's claim;
  and any total for a query the owner has not yet written.

Scryfall totals change as sets release. The durable content here is which query *form*
expresses which facet, plus the failure semantics (404 = no matches, 400 = bad syntax,
oversized = refused): those do not move with the card pool.
