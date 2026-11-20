# Uniform Provider Integration Architecture Spec

Status: draft (architect, t_61c41cfb). Scope: deck-data integrations only.
UI decisions, new providers, and implementation are out of scope.

## 1. Current state (verified against repo @ wt/t_61c41cfb)

| Integration | Direction | Entry points | Fragility |
|---|---|---|---|
| Archidekt | read | `sync.py` (full-resync), `providers/archidekt.py` (single-deck fetch) | `sync.py` HTTP calls have **no timeouts** (lines 43, 57, 126); hardcoded `FOLDER_MAP` (sync.py:82) drives folder names *and* default lifecycle status; per-deck loop is serial with `sleep(0.5)` and `raise_for_status()` aborts the whole sync on one bad deck |
| Archidekt | write | `POST /api/sync-tags` in `app.py` (`sync_deck_tags_to_archidekt`, app.py:547) — duplicated in `scripts/sync_to_archidekt.py` | Cookie+CSRF auth that expires silently; no health signal; two copies of the same logic drift |
| Moxfield | read | `providers/moxfield.py` | Unofficial `api2.moxfield.com` endpoint; could 403/change without notice; no detection |
| Scryfall | read (one-off) | `scripts/migrate_card_meta_to_oracle_id.py` only | Not a runtime integration; exclude from provider framework |
| CommanderSalt | **dead** | name appears in `ENABLED_PROVIDERS` default (app.py:25,30,49) and template links, but there is **no `providers/commandersalt.py`** in this tree | `ENABLED_PROVIDERS` default `"archidekt,moxfield,commandersalt"` is copy-pasted 3x in app.py; any provider lookup of `commandersalt` raises `ProviderError` at runtime. Note: root card references `analytics/providers/commandersalt.py` — that file does not exist in this worktree (unverified on main; may exist on a newer branch) |
| EDHREC | none | outbound links in templates only | Not an integration; keep out of the framework |

Additional cross-cutting fragility: dual card keyspace (`card:{name}` legacy vs
`card:{oracle_id}` via `cards.py`) guarded only by the Redis marker
`migration:legacy_card_keys_purged` (sync.py:15) — a missing marker silently
recreates legacy keys.

## 2. Data ownership rules (source of truth)

One writer per datum. Violations today are called out and fixed by this spec.

| Data | Source of truth | Commander Lab may |
|---|---|---|
| Deck lists, card quantities, commander identity | Archidekt (and Moxfield for Moxfield-hosted decks) | cache in Redis (`decks`, `card:{oracle_id}`); **never edit deck contents locally** |
| Card printings/images (`commander_uids`) | Provider (Archidekt uid / Moxfield scryfall_id) — semantics differ per provider | cache; normalize via adapter, tag with its origin |
| Card metadata (types, keywords, oracle text) | Scryfall via provider payload | cache in `card:{oracle_id}` |
| Physical copy status (`have/virtual/pending/possible`) | **Commander Lab** (derived once from folder/label heuristics, then user-managed) | own it; sync.py must not overwrite user-set values |
| Deck lifecycle status (`deck_status:{id}`) | **Commander Lab** | own; folder-based value is a *default on first sight only* |
| Lab tags (`lab_tags`, `card_tags:*`) | **Commander Lab** | own; push to Archidekt is a one-way export |
| Tag vocabulary (`tag_vocabulary`) | Archidekt (read-only reference stats) | rebuild from scratch each sync |

Rule: every Redis key written from an external payload must be rebuildable from
that source alone; every key owned by the Lab must never be written by a sync
pass except with explicit per-field merge logic.

## 3. Uniform provider interface

Extend the existing `providers/` package pattern (it is already the right
shape — normalized dict, `ProviderError`, registry dispatch) rather than
inventing a new one.

```python
# providers/base.py
class Capability(str, Enum):
    READ_DECKS   = "read_decks"    # list + fetch decks
    WRITE_TAGS   = "write_tags"    # push Lab tags back
    READ_ACCOUNT = "read_account"  # enumerate a user's decks (bulk sync)

@dataclass
class HealthResult:
    ok: bool
    detail: str            # e.g. "HTTP 200, 183ms" or "cookie expired (401)"
    latency_ms: int
    checked_at: float

class Provider(Protocol):
    name: str                       # "archidekt"
    capabilities: set[Capability]

    def fetch_deck(self, identifier: str) -> dict: ...   # NORMALIZED_DECK_SHAPE
    def list_decks(self, account: str) -> list[dict]: ...  # optional, READ_ACCOUNT
    def push_tags(self, deck_id: str, tags: dict) -> dict: ...  # optional, WRITE_TAGS
    def health(self) -> HealthResult: ...                # required, cheap probe
```

Adapter requirements (uniform across all providers):
1. Every HTTP call carries an explicit `timeout` (default 20s, matching
   `providers/archidekt.py:28`).
2. Auth material comes from env, is loaded in one place, and a failed/expired
   credential surfaces as `HealthResult(ok=False)` — never as a mid-sync
   exception.
3. Optional capabilities raise `NotImplementedError`, not `ProviderError`;
   callers gate on `capabilities`.
4. `commander_uids` must be annotated with their ID scheme
   (`archidekt_uid` vs `scryfall_id`) in the normalized shape — today the two
   adapters silently put different IDs in the same field.

### Required adapters now

- `providers/archidekt.py` — extend: add `list_decks` (absorb sync.py's
  user→decks flow), `push_tags` (absorb `app.py:547` + delete
  `scripts/sync_to_archidekt.py` duplicate), `health` (GET one public deck).
- `providers/moxfield.py` — add `health` (GET a tiny public deck); document
  the api2 endpoint as unofficial in the module docstring.
- `providers/commandersalt.py` — **either implement for real or remove
  `commandersalt` from every `ENABLED_PROVIDERS` default.** Shipping a default
  that names a nonexistent module is the exact failure this spec exists to kill.
  Decision owner: Andrew (the root card implies an implementation exists on
  another branch — verify before choosing).

## 4. Health checks (dead integrations must be visible)

1. `GET /api/integrations/health` runs each registered provider's `health()`
   concurrently (asyncio.gather, per-provider 5s cap) and returns
   `{provider: {ok, detail, latency_ms, checked_at, capabilities}}`.
2. Results are cached in Redis (`integration_health:{provider}`, TTL 5 min) so
   page loads never block on external calls; a stale/failed entry renders as
   "degraded", not silently missing.
3. The decks page already receives `providers` — pass health alongside and
   badge any provider with `ok=False` (designer owns presentation; coder only
   supplies the data).
4. Registry construction validates at startup: every name in
   `ENABLED_PROVIDERS` must resolve to an importable module with the Provider
   interface, else log loudly and exclude it (never raise on the request path).
5. `ENABLED_PROVIDERS` parsing lives in exactly one helper
   (`providers/__init__.py::enabled_providers()`), replacing the 3 duplicated
   lines in app.py.

## 5. Migration notes (no data loss)

Ordered, PR-sized; each independently shippable.

1. **PR-1 (no behavior change):** add `providers/base.py`, `health()` stubs,
   `/api/integrations/health`, `enabled_providers()` helper; remove
   `commandersalt` from defaults or add a stub module that reports
   `ok=False, "not implemented"`. All existing flows untouched.
2. **PR-2:** move Archidekt write into the adapter: `push_tags` absorbs
   `sync_deck_tags_to_archidekt`; `POST /api/sync-tags` becomes a thin route;
   delete `scripts/sync_to_archidekt.py` (or make it call the adapter). No
   Redis key changes — zero data risk.
3. **PR-3:** move bulk sync: `list_decks` + per-deck `fetch_deck` absorb
   sync.py's Archidekt HTTP; sync.py becomes orchestration over the adapter
   (timeouts, per-deck error isolation, keep `sleep(0.5)` rate limiting).
   Redis keys and shapes unchanged.
4. **PR-4 (ownership fixes):** first-sync-only defaults for `deck_status:*`
   (stop re-deriving from FOLDER_MAP when a saved status exists — already true,
   formalize it); move `FOLDER_MAP` to env/config; annotate `commander_uids`
   scheme. Requires a read-only audit pass first to confirm no consumer
   depends on folder re-derivation.
5. **Deferred:** legacy/oracle_id keyspace convergence (tied to
   `migrate_to_instances.py`) stays out of this program; spec only requires
   that provider adapters write the oracle_id keyspace and never the legacy
   one.

Invariants throughout: `decks`, `lab_tags`, `card:*`, `tag_vocabulary`,
`deck_status:*` key formats never change in PRs 1–3; links out to
Archidekt/Moxfield deck URLs are derived from `source`+`source_id`
(`registry_id`, providers/__init__.py:41) and are unaffected.

## 6. Non-goals

No UI/layout decisions (designer), no new providers, no analytics-pipeline
integrations (engines/Spellbook/card2vec chain is separate), no changes to the
instance-model migration.
