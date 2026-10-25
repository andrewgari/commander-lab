# Transfer dashboard: UX + API spec (TD)

Page `/transfers`, HUD kit, dark. Docs only, no app code. Mockup: `mockup.html` (serve the repo root, open `docs/design/transfer-dashboard/mockup.html?s=running`). CSS proposal: `transfers.css`. Screenshots: `shots/`.

Source of the numbers: the Redis → PostgreSQL importer (`redis_pg_import.py`, PR #276/#279) and the Prometheus counters from `transfer_metrics.py` (card t_24674646, not yet merged).

## 0. One decision the backend has to make first

The importer is a **short-lived CLI**. The app process never sees its in-memory counters, and Prometheus counters reset every run, so `rate()` over a 4-hour cron is mostly empty. A dashboard cannot be built honestly on `/metrics` alone.

**Recommendation: the importer also writes an append-only ledger to PostgreSQL; the app reads it.** Prometheus stays for Grafana and alerting (unchanged).

| Table | One row per | Written | Columns (minimum) |
|---|---|---|---|
| `transfer_runs` | run | insert at start, update every batch + every 5 s heartbeat | `run_id`, `pipeline`, `kind` (`full`/`incremental`), `phase` (`read`/`write`/`verify`/`done`), `status`, `started_at`, `finished_at`, `heartbeat_at`, `total` (null until read ends), `read`, `succeeded`, `unchanged`, `failed`, `retries`, `batch`, `batches`, `last_event` |
| `transfer_batches` | committed or rolled-back batch | after the batch outcome is known | `run_id`, `at`, `records`, `inserted`, `updated`, `unchanged`, `failed`, `reason` (null on success), `duration_ms` |
| `transfer_errors` | failed record, or failed batch | on failure; capped at 20 per run per reason | `run_id`, `at`, `reason`, `redis_key` (null for batch errors), `batch`, `records`, `message` (≤ 300 chars) |

Time series are `GROUP BY date_bin(bucket, at)` over `transfer_batches`. That is exact; my mockup pro-rates a run across its duration only because it has no batch rows.

Alternative (rejected): query Prometheus from the app. Needs a Prometheus the app can reach, breaks on short-lived processes, and gives no run list or error rows.

## 1. System

Everything is the shipped HUD kit. New tokens are `--tx-*` aliases of existing ones. No new colours, radii, shadows or fonts.

| Token | Value | Use | Contrast on `--surface-1` |
|---|---|---|---|
| `--tx-new` | `--data-5` `#4fb0c6` | records inserted/updated | 6.98:1 |
| `--tx-same` | `--data-4` `#368fa2` | records already up to date (idempotent no-op) | 4.68:1 |
| `--tx-fail` | `--danger` `#ee6a61` | failed records | 5.74:1 |
| `--tx-ok` | `--ok` `#6cbf7a` | success-rate line | 7.83:1 |
| `--tx-slo` | `--text-3` | dashed limit line | 5.82:1 |
| `--tx-chart-h` | `11rem` | plot height | |

`--data-3` was the first pick for "unchanged" and measured **2.98:1**, below the 3:1 non-text minimum. Use `--data-4`.

**Colour rule.** Data = cyan family, trouble = red, healthy trend = green, amber (`--signal`) = in progress / attention. Red never appears without a second cue:

| Where | Second cue |
|---|---|
| Failed bars | hollow, red outline on `--danger-dim` fill |
| Failed swatch | same hollow square |
| Rate below limit | **square** marker (healthy points are circles) |
| Status chips | word + dot ("Failing", "Stalled", "Offline") |

**Type.** Mono tabular numerals for every count (`.hud-num`). Hero tile uses `--fs-hero` (44px), other tiles `--fs-stat` (32px), micro-labels `--fs-micro` uppercase. Scale contrast is the hierarchy: the big number is *records transferred*, everything else is a step down.

**Terms (use exactly these in UI and API):**

| Term | Meaning |
|---|---|
| Read | records pulled from Redis |
| Succeeded | committed **and** hash-verified in PostgreSQL. Includes unchanged. |
| New / updated | succeeded minus unchanged |
| Unchanged | succeeded, but the checksum already matched (re-run no-op). Shown separately so a quiet re-run does not look like "0 transferred". |
| Failed | terminal failure. A rolled-back batch counts all its records. |
| Settled | succeeded + failed (read but not yet settled is "in flight") |
| **Success rate** | `succeeded / settled` |
| **Error rate** | `failed / settled` |
| Retries | transient batch retries. Shown as a count, **never** part of either rate. |

Rates are over *settled*, not *read*, otherwise a run at 40% looks like a 60% error rate.

## 2. Layout

Top to bottom, newest-first reading order. Grid: 12 col on `.hud-work`; panels in `.tx-cols` (3fr/2fr or even).

```
┌ OPERATIONS · Transfers ───────── ● LIVE · updated 1s ago  [Pause P] [redis_postgres ▾] [1h|24h|7d|30d] ┐
├ banner (only when offline / stalled / error-rate above limit) ───────────────────────────────────────┤
├ LIVE RUN  ● Running  Transfer r_9c41e7 · full        phase [read][WRITE][verify]  elapsed 1m 14s  eta 17s ┤
│ ███████████████░░░░░░░░░░░░░░░░░  progress (new | unchanged | failed | remaining)                      │
│ Run total 12,460 │ New 490 │ Unchanged 5,320 │ Failed 2 │ Retries 0 │ Settled 47%                       │
│ › batch 59/125 committed · 100 records · 241 ms                                       ≈ 410 rec/s     │
├ STAT STRIP (range) ────────────────────────────────────────────────────────────────────────────────────┤
│ RECORDS TRANSFERRED (hero, 2 cols) │ SUCCESS RATE │ FAILED RECORDS │ RETRIES │ LAST SUCCESS            │
├ Records over time (stacked bars) ─────────────┬ Success rate (line + limit) ──────────────────────────┤
├ Error trend (bars, own scale) ───────────────────────┬ Failure reasons (ranked, click to filter) ─────┤
├ Runs (table) ──────────────────────────────────────────────────────────────────────────────────────────┤
│   └ run detail panel (inline, below table)                                                              │
└ Recent errors (table) ─────────────────────────────────────────────────────────────────────────────────┘
```

Why this order: *Is something running, and is it healthy?* (live) → *How are we doing over the range?* (stats) → *When did it go wrong?* (charts) → *Why?* (reasons, then rows).

**Width.** ≥ 1100px two-column chart rows. < 1100 single column. < 720: hero tile loses its 2-col span, banner stacks, runs table drops Retries/Duration and scrolls inside its wrapper.

## 3. Components

### 3.1 Header
- Eyebrow `Operations`, title `Transfers`. Page head is `hud-page-head`.
- **Freshness chip** (`.tx-fresh`): square dot + words. States: `Live · updated 1s ago` (green), `Paused` (amber), `Idle · polling every 10s` (grey), `Offline · last data 19:41:12` (red), `Connecting…`. Never colour alone.
- **Pause live** (`P`): freezes polling and the counters so Andrew can read them. Only shown while a run is live.
- **Pipeline** select: single option today (`redis_postgres`); present so the second pipeline needs no redesign.
- **Range** `hud-seg` radiogroup: `1h · 24h · 7d · 30d`, keys `1`–`4`. Default `24h`. Persist in `localStorage["tx.range"]`.

### 3.2 Live run panel (`.tx-live`)
The only panel that moves. Left edge bar carries state: amber running, green idle, red failing/stalled.

| State | Trigger | Look |
|---|---|---|
| Running | `live.status = running`, heartbeat < 30 s | amber edge, `Running` chip, phase chips with the active one `aria-current="step"` |
| Reading | `phase = read`, `total = null` | progress bar indeterminate (`.is-unknown`, sliding amber block), counters show `Read 3,412` only, ETA `—`. *Specified, not mocked.* |
| Failing | running and run error rate > limit | red edge + `Failing` chip + banner. Run keeps going; it is not "failed" until it ends. |
| Stalled | no heartbeat for **≥ 30 s** while `status = running` | red edge, `Stalled` chip, counters frozen, ETA `—`, banner says which batch it stopped at |
| Idle | no live run | green edge, shows the **last** run (id, how long ago, took, final counters, `verify OK`) with a link into it |
| Empty | zero runs ever | dashed empty block with the exact command to run |
| Loading | first fetch | three skeleton blocks, `aria-busy="true"` |

- **Progress bar**: 3 stacked `scaleX` spans on one track, drawn back to front: settled (red, so the red tail is the failed share), succeeded (darker cyan = unchanged), new/updated (cyan). Remaining is the bare track. `role="progressbar"`, `aria-valuenow` = settled, `aria-valuetext` = "5,812 of 12,460 records, batch 59 of 125". Width animates with `transform` only, 320ms.
- **Counters** are a `<dl>`; each has a swatch that matches the chart legend so the colour mapping is learned once.
- **Last event** line: one line, truncated, `›` prefix; `✕` prefix and red text when the event is an error. New line fades/rises 3px (`--dur-slow`). Not `aria-live` (it ticks every batch); the freshness chip and banner are the announcements.
- **ETA**: remaining ÷ rate over the last 10 batches; `—` until 3 batches exist.

### 3.3 Stat strip (`hud-stat-strip`)
Five tiles for the selected range, each with a delta against the **previous equal range**.

| Tile | Value | Foot | Delta |
|---|---|---|---|
| Records transferred (hero, signal top edge, sparkline) | succeeded | `{n} unchanged` | % vs previous, up = good |
| Success rate | `99.19%` (2 dp) | `limit 99.0% · met` / `· below` | **pp** change, up = good |
| Failed records | count | `0.81% error rate` | % change, **up = bad (red)** |
| Retries | count | `transient errors` | % change, up = bad |
| Last success | `8h ago` | `run r_cba737` | none |

Delta direction colours follow *meaning*, not sign (failures going up is red). Delta hidden when previous is 0 or empty. Success-rate tile gets the signal top edge when it is below the limit.

### 3.4 Charts
Hand-rolled SVG like `hud.bar_chart` (no chart library; matches the shipped kit and keeps the page light). Each chart is a `hud-panel` with: title, **Table** toggle (`aria-pressed`, swaps plot for an equivalent `<table>`), legend, and an axis note.

| Chart | Encoding | Scale | Notes |
|---|---|---|---|
| **Records over time** | stacked bars: unchanged (bottom) → new → failed (top, hollow) | 0 → nice max | per-bucket unit shown in the axis note (`per 30 min · stacked`) |
| **Success rate** | green line + dots, dashed limit line, **square** red markers below limit | y axis starts at a rounded floor *below* the limit (e.g. 95%), never at 0 | axis note states the floor. Buckets with no transfers get **no point** and the line skips them. Idle ≠ 0%. |
| **Error trend** | failed-record bars | **own** scale, not shared with records | otherwise 3 failures vanish next to 5,000 transferred |

Buckets per range: `1h` → 1 min (60), `24h` → 30 min (48), `7d` → 6 h (28), `30d` → 1 day (30). The server picks the bucket; the client never aggregates.

**Hover / focus.** Pointer or arrow keys move a 1px cursor line and a tooltip (`Home`/`End` jump). The plot is `tabindex="0"`, `role="img"` with a generated summary in `aria-label` ("Success rate per 30 min, limit 99.0%. 3 buckets below the limit.") and the key hint. The tooltip flips side past 60% width. The Table toggle is the full screen-reader path.

**Empty:** dashed box "No transfers in this range". Error chart with zero failures says "No failed records · last 24 hours" (good news, not an error state).

### 3.5 Failure reasons (`.tx-reasons`)
Ranked list of the 4 reason codes, **always all four** (zero rows dim, so absence is visible). Row: code (mono), count, plain-English description, `% of failed · last seen`, proportion bar.

- Click row = toggle filter (`aria-pressed`). It filters the **error trend chart** and the **errors table**, shows a removable chip in both headers, and the reasons list itself stays unfiltered (so you can switch).
- Codes: `hash_mismatch`, `validation`, `db_error`, `source_error` (from `transfer_metrics.REASONS`; the UI must not hard-code more, render whatever `reasons[]` returns).

### 3.6 Runs table + detail
`hud-table`, newest first, 8 rows then "Load more · N older in range". Columns: Run (button, signal-coloured link), Started, Status chip (`success`=ok, `partial`/`running`=signal, `failed`=danger), Read, Succeeded, Failed (red if > 0), Retries, Duration. Numerics right-aligned mono.

Selecting a run: row gets `is-selected` (signal edge), an inline **detail panel** opens under the table (kind, duration, batches, read, succeeded, unchanged, failed, retries), and the errors table filters to that run with a removable chip. `Esc` clears. No modal: Andrew compares a run against the charts above it.

`partial` = finished, `failed > 0`, and run completed (importer exit code 1: rejected records, `--skip-invalid`). `failed` = run aborted (exit 2/3 or exception).

### 3.7 Recent errors table
Time, reason chip, `Key / batch`, message (truncated, full text in `title`). 8 rows + Load more. **Values are never shown, only keys and error text.** Batch-level failures render `batch 52 · 100 records` instead of a key. Empty states differ: "No errors in this range" vs "No errors match these filters".

### 3.8 Banner (`.tx-banner`, `role="alert"`)
At most one, highest priority wins: Offline > Stalled > Error rate above limit. Copy states the fact, the evidence and the next action; one action button.

## 4. Motion

Same vocabulary as the rest of the app: `transform`/`opacity` only, 100–320ms, `--ease`.

| What | Effect | Why |
|---|---|---|
| Progress / reason bars | `scaleX` 320ms | width = a quantity changing |
| Last-event line | rise 3px + fade | something new arrived |
| Tooltip | opacity 100ms | tracks the pointer; anything slower lags |
| Indeterminate bar | sliding block 1.6s | total unknown |
| Skeleton | opacity pulse 1.4s | loading |

**Counter numbers do not tween.** Rolling digits hide the real value for 300ms and cost layout; they snap. The progress bar carries the motion.

`prefers-reduced-motion`: bar transitions and tooltip fades off, sliding block becomes a static 35% bar, skeleton pulse off, event fade off. All state stays readable.

## 5. Accessibility

- AA text contrast throughout; non-text (bars, markers, edges) ≥ 3:1 (table in §1).
- Every red signal has a second cue (§1). Success markers are circles, breach markers squares.
- Keyboard: `1`–`4` range, `P` pause, `E` jump to errors, `←`/`→`/`Home`/`End` inside a focused plot, `Esc` clears reason/run filters. Shortcuts ignored while a form control has focus. Footer line lists them.
- Focus rings: `--focus-ring`; plots, reason rows, run links all focusable.
- Charts have text equivalents (aria summary + Table toggle). Table headers use `scope="col"`; tables have `<caption>`.
- Banner `role="alert"`; offline/stalled/failing are announced once on entry, not on every poll.
- Targets ≥ 24px (chips' remove buttons are padded, existing HUD rule).

## 6. API contract

All JSON, following the app's existing envelope: `{"success": true, ...}`; errors `{"success": false, "error": "..."}` with 400 (bad param) / 404 (unknown run) / 503 (ledger unreachable). Read-only. No auth beyond the rest of `/api`. Put these in a new `transfers_api.py` `APIRouter` and include it in `app.py`; **do not add ~300 lines to `app.py`** (already ~4,900 lines).

Common params: `pipeline` (default `redis_postgres`), `range` ∈ `1h|24h|7d|30d` (default `24h`). Every response carries `server_time` (ISO-8601 UTC) so staleness and stall are computed against the server clock, not the browser's.

### `GET /transfers`
HTML page (`templates/transfers.html`, extends `base.html`). Renders the shell + first `overview` and `live` inline to avoid an empty flash.

### `GET /api/transfers/live?pipeline=`
Cheap, polled. Index on `transfer_runs(pipeline, status)`. `Cache-Control: no-store`.
```json
{ "success": true, "server_time": "2026-10-23T23:42:00Z", "pipeline": "redis_postgres",
  "live": {
    "run_id": "r_9c41e7", "kind": "full", "phase": "write", "status": "running",
    "started_at": "2026-10-23T23:40:46Z", "heartbeat_at": "2026-10-23T23:41:59Z",
    "stalled": false,
    "total": 12460, "read": 12460, "succeeded": 5810, "unchanged": 5320, "failed": 2, "retries": 0,
    "batch": 59, "batches": 125, "records_per_sec": 410, "eta_seconds": 17,
    "failed_by_reason": {"hash_mismatch": 0, "validation": 2, "db_error": 0, "source_error": 0},
    "last_event": {"at": "2026-10-23T23:41:59Z", "level": "info", "text": "batch 59/125 committed · 100 records · 241 ms"}
  },
  "last_run": { "run_id": "r_cba737", "status": "success", "finished_at": "...", "duration_seconds": 41, "read": 1204, "succeeded": 1204, "unchanged": 1100, "failed": 0, "retries": 0, "verified": true } }
```
`live` is `null` when nothing is running; `total` is `null` while `phase = read`; `stalled` is computed server-side (`server_time − heartbeat_at ≥ 30 s`). `last_run` is always present once any run exists, `null` otherwise.

### `GET /api/transfers/overview?pipeline=&range=&reason=`
One call for stat strip + all three charts + reasons, so a range change is one request.
```json
{ "success": true, "server_time": "...", "range": "24h", "bucket_seconds": 1800, "slo": 0.99,
  "totals":   { "read": 25102, "succeeded": 24800, "unchanged": 22889, "failed": 202, "retries": 6, "runs": 7, "success_rate": 0.9919, "error_rate": 0.0081 },
  "previous": { "succeeded": 21400, "failed": 106, "retries": 3, "success_rate": 0.9951 },
  "last_success_at": "2026-10-23T15:12:08Z", "last_success_run": "r_cba737",
  "series": [ { "t": "2026-10-22T23:30:00Z", "new": 0, "unchanged": 0, "failed": 0, "success_rate": null } ],
  "reasons": [ { "reason": "db_error", "count": 100, "last_at": "..." }, { "reason": "validation", "count": 71, "last_at": "..." } ] }
```
- `series` is **dense**: exactly `range ÷ bucket` entries, oldest first, zero-filled, `success_rate = null` when `new+unchanged+failed = 0`.
- `reason` filters `series[].failed` only; `totals` and `reasons` stay unfiltered.
- `reasons` lists all known codes including zero counts.
- `previous` is the equal-length window immediately before `range`.

### `GET /api/transfers/runs?pipeline=&range=&status=&limit=8&cursor=`
Newest first. `status` ∈ `running|success|partial|failed`. Keyset pagination: `next_cursor` (opaque, `null` at end), `total` for the "N in range" label.
```json
{ "success": true, "total": 23, "next_cursor": "eyJ0IjoiLi4uIn0",
  "runs": [ { "run_id": "r_9c41e7", "kind": "full", "status": "running", "started_at": "...", "finished_at": null,
              "duration_seconds": 74, "read": 12460, "succeeded": 5810, "unchanged": 5320, "failed": 2, "retries": 0, "batches": 125 } ] }
```

### `GET /api/transfers/runs/{run_id}`
Same fields as a list item plus `failed_by_reason` and `verified` (bool|null). 404 if unknown.

### `GET /api/transfers/errors?pipeline=&range=&reason=&run_id=&limit=8&cursor=`
Newest first, same pagination. Filters combine with AND.
```json
{ "success": true, "total": 17, "next_cursor": null,
  "errors": [ { "at": "...", "run_id": "r_9c41e7", "reason": "validation", "key": "card_tags:Sol Ring", "batch": null, "records": 1,
                "message": "unserialisable value: Object of type bytes is not JSON serializable" },
              { "at": "...", "run_id": "r_9c41e7", "reason": "db_error", "key": null, "batch": 52, "records": 100,
                "message": "batch rolled back: deadlock detected (40P01), 3 retries exhausted" } ] }
```
`key` is a Redis key name, never a value. `message` ≤ 300 chars, no row contents, no connection strings (scrub `DATABASE_URL`/`REDIS_URL`).

### Polling, not SSE
| Condition | Interval |
|---|---|
| `live != null` | `/live` every **2 s**; `/overview` + `/runs` + `/errors` every 15 s |
| idle | `/live` + all others every **10 s** |
| tab hidden (`visibilitychange`) | stop; refetch immediately on return |
| Paused | stop |
| request fails | backoff 1·2·4·8·30 s, **Offline** banner after the 2nd failure, keep last data on screen |
| 503 | treat as failure |

Same polling model as `/sim/jobs/{id}` and `/review/jobs/{id}`; one transport to maintain. SSE would be nicer at 2 s but the app has no streaming endpoint today, and a short-lived importer makes 2 s polling indistinguishable.

Pause stops polling and freezes the display; on resume it fetches immediately and jumps to current.

## 7. Importer changes this implies (for the coder)

1. Create the three tables (idempotent DDL next to `redis_import`'s).
2. On `run_import`: insert `transfer_runs`; after each batch (success or rollback) insert `transfer_batches` and update the run row; failures insert capped `transfer_errors`; a 5 s heartbeat thread updates `heartbeat_at`; finish sets `status`/`finished_at`/`verified`.
3. Ledger writes must **never** raise into the transfer path (same rule as `transfer_metrics._safe`) and must use a **separate connection** so a rolled-back data batch does not roll back its own failure record.
4. Dry runs write nothing.
5. Report `phase` truthfully: `read` while scanning Redis, `write` per batch, `verify` for `--verify`.
6. A run row stuck at `status = running` with a stale heartbeat for > 10 min is reported by the API as `status: "failed"`, `reason: "abandoned"` (crashed importer), not left as running forever.
7. Retention: prune batches/errors older than 90 days on each run start.

## 8. Decisions

| Decision | Chose | Against | Why |
|---|---|---|---|
| Data source | PG ledger | Prometheus passthrough | short-lived CLI; need rows, not just counters |
| Chart lib | hand-rolled SVG | Chart.js / uPlot | matches `bar_chart`, zero bytes, full control of a11y |
| Live transport | polling | SSE | no streaming infra; same as job endpoints |
| Unchanged | own series | folded into succeeded | a no-op re-run must not look like 0 |
| Rate denominator | settled | read | in-flight records are not failures |
| Rate axis | floor below limit | 0-100 | otherwise 98.9% and 99.9% are the same line |
| Error chart scale | independent | shared | small failure counts must stay visible |
| Counter animation | none | rolling digits | readability beats flourish |
| Detail view | inline panel | modal | keep charts visible while inspecting a run |
| Skipped | parallax, tile flips, gauges | | none carry information a number does not |

Bold option I did not ship: a full-width "heartbeat" ECG strip for the live run. It looked great and told you less than the progress bar. Safe option is what's here.

## 9. Acceptance (for the reviewer)

- [ ] `/transfers` renders all 7 states in §3.2 (mock has 6; `Reading` is spec-only).
- [ ] Success-rate chart never shows a point for an empty bucket; axis note states the floor.
- [ ] Failed bars are hollow; breach markers are squares.
- [ ] Hand-check: `new + unchanged + failed` in a bucket = `settled`; `success_rate = (new+unchanged)/settled`.
- [ ] Stall banner appears ≥ 30 s after the last heartbeat, using `server_time`.
- [ ] Hidden tab makes zero requests.
- [ ] Reduced motion: no animation, all state readable.
- [ ] Keyboard-only: pick a range, filter by a reason, open a run, return, with no mouse.
- [ ] 390px: no horizontal page scroll (tables scroll inside their wrapper).
- [ ] No ledger exception can fail or slow an import (kill PG ledger connection mid-run).

## 10. Files

| File | |
|---|---|
| `mockup.html` | interactive; `?s=running\|failing\|stalled\|idle\|offline\|empty\|loading`, `&r=1h\|24h\|7d\|30d` |
| `transfers.css` | proposal for a new `hud.css` section; port as-is, class names final |
| `shots/` | `{state}-1440.png` ×7, `running-7d-1440.png`, `running-390.png`, `failing-390.png` |

**Mockup caveat.** All numbers come from a seeded PRNG against a frozen clock; the mockup pro-rates runs across time because it has no batch rows. The real API buckets from `transfer_batches`.
