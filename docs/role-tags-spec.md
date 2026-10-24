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
| keyword | `flying`       | `keyword:flying` in oracle text, `o:"flying"` as the shared-concern sense
| concept | *(free)*       | no templates; a concept target starts as a blank query box
| enemy   | `vampire"`     | "answers X" templates: `o:/destroy target [^.]*vampire/`

A **concept** target (Historic, Lands Matter, Birds Matter, Vampires are the enemy) is
not a single `t:` type; its tags are free-form queries that may span several types
(see §6.9 for the Historic worked case).

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
this design's own verification: `otag:spot-removal` resolves to **5,415 cards** on the
API, and several `otag:` names that exist in Scryfall **Tagger** (`otag:bird-matters`,
`otag:tutor-selective`) return **404** from `/cards/search`. Tagger's tag universe and
the search API's `otag:` alias set are different things (see OQ-1), so the query text
must be evaluated by the same engine the preview used.

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
| refresh trigger | (a) any editor "Refresh" click; (b) `sync.py` full sync issues 1 `HEAD`-free `q=<tag query>` count-only? no — instead: **nightly cron runs each distinct tag query once**, rewriting changed entries; (c) tag query edited | one Scryfall pass per night over the *distinct* query set, not per deck |
| `tag_match:*` | derived, rebuilt when `tag_query_index` changes or `deck_version` changes | cheap set intersection |
| `tag_preview:*` | 1h TTL | typed-in-the-box previews are throwaway |

Nightly pass cost at 20 distinct tags ≈ 20–60 search runs, worst case ~400 pages
(E: broad `otag:` tags). Assumption: the collection has ≤ 12 archetypes × ≤ 6 roles,
so < 40 distinct queries — measured nowhere yet; card ARCH-3 must confirm with the
real registry before enabling the cron.

Set-level semantics: Scryfall search results are the *currently legal-in-some-format,
paper-or-digital* printings of cards; banned cards still exist. A tag query that
matches a banned card is fine — the deck already holds the card, and role scoring is
not a legality check.

### 2.6 Guardrails

- **Hard result cap** `TAG_MAX_RESULTS = 5000` (≈29 pages). Above it the tag is
  stored but marked `oversized: true` and **excluded from scoring** with a visible
  editor warning: "this query matches 16k cards, narrow it". `otag:spot-removal` at
  5,415 already trips it — which is the correct answer: "spot removal" as a role should
  be a narrower query (§6.4).
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

Two resources have an **empty** `produce_otags` / non-otag semantics today and cannot
be expressed as a query:

- `produces:spells` (`produce_otags=()`) — "is or copies an instant/sorcery spell".
  Mapping: `t:instant or t:sorcery` (behaviourally identical for role counting; the
  current empty list means this role already **scores 0 for every deck** — see the
  bug note below).
- `produces:death` (`produce_otags=("sacrifice-outlet-creature",)`) and others with a
  single otag map trivially.

**Known pre-existing defect this migration must not preserve:** roles whose side has
an empty `otag` list (`produces:spells`) currently count 0 for every deck because
`otag_queries()` skips empty lists and `oracle_rules` does not produce that key. The
migration deliberately gives `produces:spells` a real query (`t:instant or t:sorcery`)
so it starts scoring; that is a behaviour change and belongs in the release note
(CHANGELOG under `### Fixed`).

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

All totals below are **live Scryfall counts measured 2026-10-24**, not estimates.
`unique=cards` throughout.

**Birds — IS X (`t:bird`)** — 463 cards.

```
t:bird
```

**Birds Matter — TALKS about X, stacking with IS** — 112 cards.

```
o:bird
```

Rationale to keep visible in the tag's explanation: this is *not* `t:bird`. A bird that
doesn't care about birds is not a Birds-Matter card; a non-bird that cares ("whenever a
Bird you control attacks") is. Load-bearing example: `Aerie Worshippers` is in `o:bird`
and not in `t:bird`. A Birds archetype typically requires **both** tags at different
weights — the model allows it because tags stack.

**Bird Generator — MAKES X** — 44 cards (breadth-first; the literal phrase version
`o:"create a 1/1 white Bird creature token"` finds only 12 and misses `Attempted Murder`
style "creates a ... Bird").

```
o:/creates? a [^.]*Bird creature token/
```

**Anti-Vampire — destroy one (owner example).**

```
o:/destroy target [^.]*vampire/        → 3 cards (Cleansing Ray, Slayer of the Wicked, Victim of Night)
o:"destroy target vampire"             → 2 cards (misses Victim of Night: "destroy target non-Vampire creature … or target Vampire and its controller" — wait, exact phrasing varies; see note)
```

The regex form is the required one: it catches "destroy target creature **if** it's a
Vampire"–style phrasings that the literal string misses. **Anti-Vampire — wipe:**

```
o:/destroy all [^.]*vampire/           → 0 cards (no such card exists today; the tag is
                                         still valid and shows "0 — query is simply too strict")
```

This is the honest answer, and it must be *shown* as empty rather than suppressed: the
owner asked for "destroy all vampires" as a searchable role, and it is searchable — the
card pool just has none. Compare the general wipe `o:/destroy all creatures/` — 100 cards.

**Spot removal (restricted)** — narrow by what it hits and keep it under the cap.

```
o:/destroy target [^.]*(creature|permanent)|exile target [^.]*(creature|permanent)/   → combine with the two-part form below
```

Practical form (each a separate tag, which is the point — the owner wants the cut):

```
spot removal, any:        o:/destroy target [^.]*(creature|permanent)/ or o:/exile target [^.]*(creature|permanent)/
                          → 702 + ... (inspect: broad tags trip the cap; prefer the narrow ones)
```

**Selective spot removal** (single-permanent, targeted, destroys only):

```
o:/destroy target [^.]*(creature|permanent)/        → 702 cards
```

vs. the **literal-phrase** narrow form used to express "destroy target enchantment":

```
o:"destroy target enchantment"                      → express via: o:/destroy target (enchantment|artifact)/   → 397 cards
```

That 397 for `o:/destroy target (enchantment|artifact)/` is the concrete "selective spot
removal" cut: it only answers enchantments and artifacts, not creatures. Contrast
`o:"destroy target creature"`-class tags: those are the *non*-selective cut.

Scryfall note that bit this design while probing: **an alternation inside a regex needs
the regex form**, and `o:/destroy target (creature|permanent)` (unclosed slash) is a 404;
`o:/destroy target [^.]*(creature|permanent)/` is the 702-card form. The editor's live
preview catches this class of typo before save (§5.1 step 4).

**Exile removal** (not destroy — different answer, different role):

```
o:"exile target" -o:"exile all"                    → 605 cards
o:/exile target [^.]*(creature|permanent)/         → 406 cards
```

**Symmetric wipe** — destroys everything, no controller restriction:

```
o:/destroy all [^.]*/      -o:/you control/       → 241 cards (the broad form)
o:/destroy all creatures/                         → 100 cards
```

and the **asymmetric** cut, for contrast (the owner explicitly wants this distinction):

```
o:"destroy all creatures you don't control"       → 2 cards visible as "destroy all creatures you don't control"
```

Note the asymmetry is *usually* spelled "each opponent sacrifices" or "you control"
clauses rather than "you don't control" — the pattern to reach for is:

```
(o:/each player sacrifices/ or o:/destroy all creatures you don.t control/)
```

**Selective tutor** vs **non-selective tutor**:

```
tutor, any:            o:/search your library for/                              → 829 cards
non-selective tutor:   o:/search your library for [^.]*reveal/                  → 349 cards  (reveal = you must show what you got)
selective tutor:       o:/search your library for [^.]*you may reveal/          → narrower form; the "may reveal" + named-type phrasing is the selective cut
                       e.g. o:"search your library for a creature card"         → 47 cards
```

Honest caveat, stated in the tag explanation rather than hidden: Scryfall's text alone
cannot perfectly separate "look at your library and pick" from "search for a named type"
for every card — the split is approximate and the owner can tighten any tag by editing
its query, which is the design's escape hatch.

**Historic matters (owner example, concept spanning types).**

Historic is a keyword action over artifacts, legendaries and sagas. Two tags, because
the concept has two senses:

```
Historic, talks about it:   o:/historic/                  → 58 cards
```

```
Historic, IS one:          (t:artifact or t:legendary or t:saga)   → 8,185 cards (trips the cap → marked oversized, and that is correct: a deck doesn't need a tag for "is historic", it needs tags for the cards that care)
```

The 58-card `o:/historic/` set is the real role: cards whose text references historic.
Its matched cards must be reviewable in the breakdown (some are reminder-text false
positives), and the owner can tighten to e.g. `o:/historic/ -o:"historic"`? no — see
§7 OQ-4 for the recommended tightening tool rather than a magic rewrite.

**Lands Matter** (free-form concept, per the brief): `o:/landfall|whenever a land enters/`
— note this is a concept query with no `t:` type behind it, the same shape as
`o:/historic/`.

### 6.8 Legend / colour, for completeness

```
target kind=color, signal w:   IS → id:w          TALKS → o:/white/ (weak; recommend concepts instead)
target kind=keyword, signal flying:  IS → o:"flying"  (keyword text; keyword: is not a Scryfall operator)
```

Note `keyword:flying` **is not** a Scryfall search operator (attempting it silently
returns nonsense matches via free-text), so keyword targets must seed `o:"flying"` and
the editor must not offer a `keyword:` template. Verifying that is a one-line check in
the seed table, not runtime cleverness.

---

## 7. Open questions for the owner

Each with a recommendation; none blocks writing code, all block merge of ARCH-3's
first PR if unresolved.

**OQ-1 · `otag:` is not the Tagger universe.**
`/cards/search` understands only a small alias set of `otag:` values
(`spot-removal`→5,415, `removal`→6,465, `boardwipe`/`board-wipe`/`mass-removal`/`sweeper`
→all the same 947, `tutor`→1,168, `ramp`→2,298), and many real Tagger tags
(`bird-matters`, `tutor-selective`, `tok…`) 404. So a query written in Tagger's
vocabulary can silently under-match.
→ **Recommendation:** treat `otag:` as a convenience, never the default. Migrated
`legacy:*` tags keep their `otag:` queries (so scores don't move), but new templates seed
`t:`/`o:`/regex. Show a warning in the editor when a query contains `otag:` and the
count is 0.
**Question:** keep `otag:` in the legacy tags at all, or regenerate them as `t:/o:`
queries at migration (which *would* move some saved scores slightly but makes every tag
explainable in plain Scryfall syntax)?

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

Every query and total in §6 was executed against `https://api.scryfall.com/cards/search`
with `unique=cards` on 2026-10-24 from this machine; totals are that day's
`total_cards`. Queries change as sets release — the spec's numbers are illustrative of
*magnitude*, while the *shapes* (which query form expresses which facet) are the durable
content. Redis layout keys were checked for collision against the live
`commander-lab-redis` (`interaction_graph:collection`, `card_interactions:*`,
`archetypes`, `lab_tags`, `comp:presets`, …): none of the proposed `role_tags*`,
`tag_*` keys are in use.
