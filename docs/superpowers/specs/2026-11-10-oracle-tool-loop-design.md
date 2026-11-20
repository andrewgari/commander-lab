# Oracle tool loop on OpenRouter: design

- Card: t_d571bd53 (architect). Status: design, ready for review.
- Amends: `Plans/commander-lab/2026-11-05-spec-remove-ollama-openrouter-reviewer.md` (t_d29ed3c9), LLM-6 and LLM-7.
- Scope: one in-repo `oracle` abstraction that runs an agentic tool loop on OpenRouter. Written deck reviews are the core feature. Builds use the same loop. Play gets an interface only.

## 0. Decisions (Andrew, final) and what they mean for code

| # | Decision | Consequence |
|---|---|---|
| D1 | Every review and build goes through the tool loop from day one. No single-prompt fallback. | Migrated consumers call `oracle.run(...)` only. No consumer keeps a `client.chat(...)`/`client.ask(...)` path. If the loop fails, the consumer shows its existing deterministic offline/invalid state (AGENT_RULES rule 4). It never retries as a plain prompt. |
| D2 | A review is one comprehensive pass: written analysis whose verdict is backed by tool-output data (curve, bracket, power). | Review tasks declare **required evidence** (§2.5). The loop refuses to accept a final answer until `deck_stats` has returned curve, bracket and power. Numbers in the prose are checked against the union of all tool outputs (§2.6). |
| D3 | Play = reserved slot/interface only. Spot-check reviews are out of scope. | `domain/oracle/play.py` holds a Protocol and a function that raises `NotImplementedError`. No tool, route, UI or task entry for play. |
| D4 | Keep the name "oracle". | The new package is `src/server/domain/oracle/`. LLM-6 still deletes the **Hermes** client (`analytics/oracle_client.py`, `infra/llm/client.py`, `ORACLE_*` env) but no longer deletes the name. See §7. |

Facts this design rests on (verified on `origin/main` @ ab833a7):

- `infra/llm/openrouter_chat.py` `OpenRouterChat.chat()` already forwards `tools`/`tool_choice` (`ALLOWED_EXTRAS`), drops `tools=[]`, and sets `provider.require_parameters=true` when tools are present. `_parse()` reads only `message.content` and raises `LLMResponseError("empty reply")` when content is blank. That is exactly what a tool-call turn looks like, so tool calls can't work today.
- `infra/llm/factory.py build_chat_client(task)` and `infra/llm/tasks.py TASKS` exist. `domain/knowledge.build_messages(task, ...)` exists and returns cache-controlled system blocks.
- Consumers still on the Hermes `OracleClient` (via `main._oracle_client` or `OracleClient.from_env`): `analytics/review_doc.py`, `analytics/sim_service.py` (+ `sim_diagnosis.py`), `analytics/card_review/llm.py OracleLLM`, `infra/llm/review_writer.py` (review_service v1 writer), `draft/reconstruct_agent.py` (+ `reconstruct_builder`, `brew_builder`), `draft/brew_review.py`. `analytics/review_pipeline` (v2 passes A–E) runs on `infra/llm/provider.RoutingClient` (`LLMClient.complete(LLMRequest)`), not on `OracleClient`.
- LLM-4a/4b/4c/5/6 cards were never created on the board. The OL cards below **replace LLM-4b/4c/5** for the consumers they touch. Theory, personality read, genre autofill and deck write-up remain LLM-4a/4b territory (§6.3).

## 1. Layout

```
src/server/domain/llm/port.py            + ToolCall, ChatTurn, ToolChatClient   (domain types, no I/O)
src/server/infra/llm/openrouter_chat.py  + chat_turn()                           (transport, parses tool_calls)
src/server/infra/llm/tasks.py            + LoopSpec per task                     (caps table)
src/server/infra/llm/factory.py          + build_oracle(task, env) -> Oracle     (wiring)
src/server/domain/oracle/
    __init__.py      exports Oracle, OracleResult, OracleRunError + subclasses, Tool, ToolRegistry, ToolContext, LoopCaps
    types.py         LoopCaps, OracleResult, ToolOutcome, OracleTrace (dataclasses)
    errors.py        OracleRunError, OracleBudgetExceeded, OracleEvidenceMissing, OracleInvalidAnswer
    registry.py      Tool, ToolRegistry (specs + dispatch + arg validation + result capping)
    loop.py          Oracle.run(...)  (the call -> run -> feed-back loop)
    numbers.py       allowed_numbers_from(outputs) (wraps review_doc.allowed_numbers)
    tools/context.py ToolContext (injected deps; the only way tools reach data)
    tools/deck.py    get_deck
    tools/cards.py   card_lookup
    tools/stats.py   deck_stats (curve / bracket / power / composition)
    tools/sim.py     sim_run
    play.py          reserved interface (D3)
```

Rules: everything under `domain/oracle/` is pure domain. It imports no `infra`, `app`, FastAPI or SQLAlchemy (AGENT_RULES rule 2; no new `ignore_imports` entries). I/O reaches tools only through callables on `ToolContext`, which the job layer / `main.py` fills.

## 2. Loop contract

### 2.1 Transport turn (OL-1)

`domain/llm/port.py` adds:

```python
@dataclass(frozen=True)
class ToolCall:
    id: str                  # provider id; synthesized "call_{turn}_{i}" if missing/blank
    name: str
    arguments: dict | None   # parsed JSON object, None when it did not parse
    arguments_raw: str       # the raw string as sent
    parse_error: str | None  # "invalid JSON: ..." / "arguments must be an object"

@dataclass(frozen=True)
class ChatTurn:
    content: str             # "" when the model only called tools
    tool_calls: tuple[ToolCall, ...]
    finish_reason: str       # "stop" | "tool_calls" | "length" | other provider string | ""
    usage: Usage             # tokens_in, tokens_out, cached_tokens, cost_usd (None = not reported)
    model: str               # data["model"] or the requested model
    raw_message: dict        # choices[0].message exactly as received (re-sent verbatim, see 2.2 step 4)

class ToolChatClient(Protocol):
    model: str
    def chat_turn(self, messages: list[dict], *, tools: list[dict] | None = None,
                  tool_choice: str | dict | None = None, response_format: dict | None = None,
                  temperature: float | None = None) -> ChatTurn: ...
```

`Usage` here is `LLMUsage` from `openrouter_chat.py`, moved to `port.py` and re-exported from `openrouter_chat` under the old name. `OpenRouterChat.chat_turn()`:

1. Builds the body with the existing `_body()`. Uses the same `_post`, error mapping and `_redact` as `chat()`.
2. Parses `choices[0].message`. `content` is `None`/str/list-of-parts, normalized like `_parse` does today. `tool_calls` is a list of `{id, type:"function", function:{name, arguments}}`. Entries with a `type` other than `"function"`, or with no `function.name`, are dropped and logged at WARNING.
3. Detects tool calls by **presence** of `tool_calls`, not by `finish_reason`. Some providers return `"stop"` with tool calls.
4. `finish_reason == "length"` → `LLMResponseError("reply truncated at max_tokens")`, same as today.
5. Empty content **and** no tool calls → `LLMResponseError("empty reply")`. Empty content **with** tool calls is valid.
6. Sets `self.last_usage`.

`chat()` stays byte-for-byte compatible. All current `test_llm_openrouter_chat.py` tests pass unchanged.

### 2.2 `Oracle.run`

```python
class Oracle:
    def __init__(self, client: ToolChatClient, caps: LoopCaps, *, clock=time.monotonic): ...
    def run(self, *, task: str, messages: list[dict], registry: ToolRegistry, ctx: ToolContext,
            validate: Callable[[str, "OracleRunState"], list[str]],
            required_evidence: Mapping[str, frozenset[str]] = {},
            response_format: dict | None = None) -> OracleResult: ...
```

- `messages` comes from `knowledge.build_messages(task, ...)`: system blocks plus one user message. The loop never edits the system message.
- `validate(final_text, state)` returns a list of error strings (empty = accepted). It is the consumer's existing parser/validator (`parse_reply` + `validate_prose` for review_doc, and so on) wrapped to return errors instead of raising.
- `OracleResult`: `text` (the accepted final answer), `parsed` (whatever the consumer's validator stashed on `state.parsed`), `outputs` (list of `ToolOutcome`: `call_id, tool, arguments, ok, result` (the dict sent back), `ms`, `truncated`), `trace` (`OracleTrace`), `stop_reason` (`"answered"`, or `"answered_after_finalize"` when the finalize turn produced the answer).

Algorithm. `turn` counts model calls, starting at 1.

1. **Budget check** before every model call (§3). A hard cap raises `OracleBudgetExceeded`. A soft cap switches to the finalize turn (step 7).
2. Call `client.chat_turn(messages, tools=registry.specs(), tool_choice="auto", response_format=None)`. `response_format` is sent **only on the finalize turn**, and only if the consumer passed one. Many providers reject `response_format` together with tools.
3. Add `turn.usage` to the run totals.
4. **Tool calls present:** append `{"role": "assistant", "content": turn.content or None, "tool_calls": turn.raw_message["tool_calls"]}` (the provider's own list, verbatim). If the turn has more than `caps.max_calls_per_turn` calls, the extra calls are answered with `{"ok": false, "error": {"kind": "too_many_calls", ...}}` and not run. The kept calls run **sequentially, in order**. No threads: tools share `ctx` memoization and sim_run is exclusive. Each call appends `{"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)}`. Go to 1.
5. **No tool calls (a candidate final answer):**
   a. **Evidence check (§2.5).** If required evidence is missing and `nudges_left > 0`: append a user message `EVIDENCE_NUDGE.format(missing=...)`, decrement, go to 1. If missing and no nudges are left: raise `OracleEvidenceMissing(missing)`.
   b. **Validate.** `errs = validate(turn.content, state)`. If empty, return the result. If not, and `repairs_left > 0`: append the assistant message and then a user message `REPAIR_PROMPT.format(errors="\n".join(errs[:5]))`, decrement, go to 1. If no repairs are left: raise `OracleInvalidAnswer(errs)`.
6. The run never ends on a tool-call turn. It ends only on an accepted answer or an exception.
7. **Finalize turn.** This fires when a soft cap trips: `max_turns - 1` turns used, the tool-call budget spent, or cost ≥ `soft_cost_usd`. Append a user message `FINALIZE_PROMPT` ("Tool budget used up. Answer now from the tool results above; do not call tools.") and call with `tool_choice="none"` plus `response_format` if given. Step 5 applies to the result, except that a further repair is allowed only if `max_turns` is not exceeded. If the finalize turn still returns tool calls, raise `OracleBudgetExceeded("model kept calling tools after finalize")`. The finalize turn happens at most once.

Prompt constants live in `loop.py`. Each consumer's task file under `domain/knowledge/tasks/` gains one "### Tools" paragraph that names the tools and the evidence rule (OL-6..OL-11 own those edits). Remove "Single turn: do not call tools" wording wherever a migrated consumer's system text has it.

### 2.3 Tool registry interface

```python
@dataclass(frozen=True)
class Tool:
    name: str                          # ^[a-z][a-z0-9_]{1,40}$
    description: str                   # <= 600 chars, says what it returns and when to call it
    parameters: Mapping[str, Any]      # JSON Schema object, "additionalProperties": false
    handler: Callable[[dict, ToolContext], dict]   # returns a JSON-able dict; raises ToolError or anything
    max_calls: int | None = None       # per run; None = only the global cap applies
    max_result_chars: int = 12_000

class ToolError(Exception):
    def __init__(self, kind: str, message: str): ...   # kind: snake_case, shown to the model

class ToolRegistry:
    def __init__(self, tools: Iterable[Tool]): ...      # duplicate names -> ValueError
    def specs(self) -> list[dict]                        # [{"type":"function","function":{name, description, parameters}}], registration order
    def dispatch(self, call: ToolCall, ctx: ToolContext) -> ToolOutcome
    def names(self) -> frozenset[str]
```

`dispatch` order. The first failing step returns `{"ok": false, "error": {"kind", "message"}}`:

1. Unknown name → `unknown_tool` (message lists the valid names).
2. `call.parse_error` → `bad_arguments`.
3. jsonschema `Draft202012Validator(parameters)` errors → `bad_arguments` (first 3 messages).
4. Tool disabled (§4) → `disabled`.
5. Per-tool `max_calls` reached → `call_limit`.
6. Run the handler. `ToolError` → its kind. Any other exception → `tool_failed` with message `f"{type(exc).__name__}"` only; no exception text, which can carry URLs or keys. The exception is logged with traceback.
7. Success → `{"ok": true, "data": <handler dict>}`. If the serialized result is longer than `max_result_chars`, the handler's own `shrink(data)` runs if the tool defines one. Otherwise lists are cut from the end until it fits, and `"truncated": true` is added. A result never exceeds the cap.

The same registry instance is not shared between runs. Consumers build one per run with `build_registry(names, ctx)` from `tools/__init__.py`.

### 2.4 ToolContext

```python
@dataclass
class ToolContext:
    deck: Mapping[str, Any]                          # the deck or draft under review/build ({name, commanders, cards[{name, quantity}], registry_id?})
    journal: Mapping[str, Any] | None = None         # journal_mod.public(...) or None
    metrics: Mapping[str, Any] | None = None         # review_metrics.compute_review output, if the consumer already has it
    game_changers: Sequence[str] | None = None       # fetched by the job layer (as in LLM-4b), None = unavailable
    scryfall_batch: Callable[[list[str]], dict] | None = None    # fact_sources.default_fetchers()["scryfall_batch"]
    compute_metrics: Callable[[Mapping], Mapping] | None = None  # lambda deck: review_metrics.review_deck(r, dict(deck))
    sim: "SimRunner | None" = None                   # §2.7; None = sim_run not offered
    allow_hypothetical: bool = False                 # build tasks: deck_stats may take a `cards` override
    memo: dict = field(default_factory=dict)         # per-run cache (metrics, bracket eval, lookups)
```

### 2.5 v1 tools

All outputs are deterministic code (AGENT_RULES rule 5). Card facts come only from Scryfall or the DB (rule 3). Missing inputs come back as `{"status": "unavailable", "reason": ...}`, never as zeros (rule 4).

| Tool | Parameters | Returns (`data`) | Source |
|---|---|---|---|
| `get_deck` | `{}` | `{name, registry_id, commanders, cards: [{name, qty}], total, fingerprint, journal}` | `ctx.deck`, `review_metrics.fingerprint`, `ctx.journal` |
| `card_lookup` | `{"names": [str] (1..20, unique)}` | `{"cards": [{query, found, name, mana_cost, cmc, type_line, oracle_text, color_identity, commander_legal, game_changer}] }`; not found → `{query, found: false, reason: "not on Scryfall"}` | `ctx.scryfall_batch` (24 h cache, ≥550 ms spacing already built in); `game_changer` from `brackets.is_game_changer(name, ctx.game_changers)`, or `null` when `game_changers` is None. `max_calls=4`, at most 60 names per run (`call_limit` after that). `scryfall_batch` None → `ToolError("unavailable", "card lookup is not configured")`. |
| `deck_stats` | `{"sections": ["curve","bracket","power","composition"] (min 1, unique), "cards"?: [{name, qty}] }` | one key per section, below | memoized per run per deck fingerprint |
| `sim_run` | `{"games": 5 \| 20}` | §2.7 | `ctx.sim` |

`deck_stats` sections:

- `curve` → `metrics["curve_tempo"]` (commander_on_curve, mana, active_share, durdle_tax, flex, library_size, unknown_cards). `metrics = ctx.metrics` if given, else `ctx.compute_metrics(deck)`.
- `composition` → `metrics["composition"]["rows"]` + `total_cards` + `unknown_cards`.
- `bracket` → `bracket_eval.evaluate_bracket(deck, ctx.journal, {"game_changers": ctx.game_changers})`, sent as `model_dump(include={estimated_bracket, estimated_bracket_name, estimated_bracket_reason, minimum_bracket, target_bracket, aligned, conflicts, explanation})` plus `findings.floors`. When `ctx.game_changers is None`, add `"game_changers": "unavailable"` and let the evaluator use its snapshot default (the existing behaviour).
- `power` → the same evaluation's `{estimated_power_level, power_source, target_power_level, power_delta, power_aligned}`. When `ctx.allow_hypothetical`, also `brew_power.estimate_power_level(cards)["power"]` and its `breakdown`, under key `brew_estimate`.
- `cards` (override): accepted only when `ctx.allow_hypothetical`, else `ToolError("not_allowed", ...)`. 1..120 entries. Stats are computed for `{**ctx.deck, "cards": override}`. The memo key is the fingerprint of that deck.

**Required evidence (D2).** `required_evidence` maps a tool name to the set of argument values that must have appeared in **successful** calls. Deck review, sim review, card review, the review pipeline adapter and the brew reviewer all pass `{"deck_stats": {"curve", "bracket", "power"}}`. The check is the union of `sections` over successful `deck_stats` outcomes. Reconstruct/brew builds pass `{"deck_stats": {"curve", "power"}}` (their candidate decks are checked with the `cards` override). The nudge names the missing sections. `nudges_left = 1`.

### 2.6 Numbers guard

`numbers.allowed_numbers_from(outputs, extra)` returns `review_doc.allowed_numbers({"tools": [o.result for o in outputs if o.ok], **extra})`. Migrated review consumers use it in place of the metrics-only set. The prose may quote any number a tool returned, plus the numbers already allowed today. `strip_invented_numbers` and the "N sentences removed" UI note are unchanged.

### 2.7 `sim_run`

`SimRunner` lives in `tools/sim.py` and wraps `sim_service`:

```python
class SimRunner:
    def __init__(self, r, deck, *, client=None, max_wait_s=900.0, sleep=time.sleep): ...
    def run(self, games: int) -> dict
```

1. If `sim_store.load_latest(r, deck, persist_stale=False)` has a report that is not stale, with `summary.games >= games`, return `{"source": "saved", "saved_at", "games", "comparison": sim_service._slim_comparison(build_sim_comparison(deck, report, static))}`. Do not start a new run.
2. Otherwise `sim_service.begin_run(r, deck, {"opponent_mode": "standard", "games": games, "notes": "oracle tool run"})`, then `sim_service.run_job(r, job, deck, client, oracle_factory=_NO_REVIEW, max_wait=max_wait_s, sleep=sleep)`. `_NO_REVIEW` makes `write_review` skip the LLM (OL-4 adds a `skip_review=True` keyword to `run_job`/`_execute` that sets `oracle_review = {"source": "skipped"}` and skips `sim_diagnosis`'s LLM summary). This prevents recursion: a sim started by a review must not start another oracle run. Return `{"source": "new_run", "job_id", "games", "comparison": ...}`.
3. Map errors to `ToolError`: `SimParamError` → `sim_not_configured` (e.g. no Standard pod), `SimRunBusy` → `sim_busy`, `SimUnavailable` → `sim_unavailable`, `TimeoutError` → `sim_timeout`.
4. `max_calls=1`. Offered only to deck review (`review_doc`) and builds that set `ctx.sim`. Never offered to `sim_service.write_review`, which already has the report.

## 3. Caps

`domain/oracle/types.py`:

```python
@dataclass(frozen=True)
class LoopCaps:
    max_turns: int            # model calls, including nudge, repair and finalize turns
    max_tool_calls: int       # total over the run
    soft_cost_usd: float      # finalize when reached
    hard_cost_usd: float      # abort (OracleBudgetExceeded) when reached before a call
    max_tokens_in: int        # fallback meter when the provider reports no cost: sum of prompt tokens
    deadline_s: float         # wall clock for the whole run (tool time included)
    max_calls_per_turn: int = 8
    repairs: int = 1
    nudges: int = 1
```

Defaults go in `infra/llm/tasks.py` as `LOOPS: dict[str, LoopCaps-shaped tuple]`. `build_oracle` turns them into `LoopCaps`.

| task | max_turns | max_tool_calls | soft $ | hard $ | max_tokens_in | deadline_s |
|---|---|---|---|---|---|---|
| deck_review | 10 | 16 | 0.60 | 1.00 | 400_000 | 1500 (sim_run may take 900) |
| sim_review | 6 | 8 | 0.30 | 0.50 | 200_000 | 300 |
| sim_diagnosis | 4 | 4 | 0.05 | 0.10 | 60_000 | 120 |
| card_review | 6 per debate call | 8 | 0.20 | 0.35 | 150_000 | 180 |
| review_writer | 6 | 8 | 0.40 | 0.70 | 250_000 | 300 |
| review_pipeline (per pass) | 6 | 8 | 0.50 | 0.90 | 300_000 | 600 |
| reconstruct | 14 | 30 | 1.50 | 2.50 | 900_000 | 1200 |
| brew_review | 6 | 8 | 0.20 | 0.35 | 150_000 | 240 |

- The cost meter is the sum of `usage.cost_usd`. If any turn reports `cost_usd=None`, the run switches to the token meter: `soft = 0.8 × max_tokens_in`, `hard = max_tokens_in`. Cost is never guessed.
- Env overrides, per task, read in `build_oracle`: `LLM_LOOP_MAX_TURNS_<TASK>`, `LLM_LOOP_HARD_USD_<TASK>`. Invalid values are ignored, the same as `LLM_TIMEOUT_S`. **Do not use an `ORACLE_` prefix.** The LLM-6 CI guard greps for `ORACLE_`.
- The deadline is checked before each model call and before each tool call. When it passes: finalize if no finalize turn has happened yet, else `OracleBudgetExceeded("deadline")`.

## 4. Error handling

| Failure | Where | Behaviour |
|---|---|---|
| `LLMUnavailable` / `LLMNotConfigured` / `LLMAuthError` from `chat_turn` | loop | Propagates unchanged (no retry beyond `_post`'s 2). Consumers keep their existing mapping (review_doc `offline`, sim `fallback` source with note, card_review job error, brew `unavailable`). |
| `LLMResponseError` (truncated, empty, malformed) | loop | Propagates. Consumers treat it as `invalid` (existing branch). |
| Tool raised / bad args / unknown tool | registry | Fed back to the model as `{"ok": false, "error": ...}`. The run continues. |
| Same tool fails 3 times in a row in one run | registry | Tool disabled for the rest of the run (`disabled`). The failure counter is per tool and resets on success. |
| Model never supplies evidence | loop | `OracleEvidenceMissing` → consumer `invalid`, note `"oracle answered without checking curve/bracket/power"`. |
| Final answer fails validation after repairs | loop | `OracleInvalidAnswer(errors)` → consumer `invalid` with the first error (≤300 chars). |
| Turn, tool, cost or deadline cap | loop | Finalize once. Then `OracleBudgetExceeded(reason)` → consumer `invalid`, note `"oracle hit its <reason> budget"`. |
| `OracleRunError` hierarchy | `errors.py` | `OracleRunError(LLMError)`, with subclasses `OracleBudgetExceeded`, `OracleEvidenceMissing` and `OracleInvalidAnswer`. Each carries `.trace`. Because `OracleRunError` is an `LLMError`, existing `except LLMError` branches still catch it. |

The API key never appears in a tool result, trace or error: all transport text goes through `_redact`. Tool results never contain secrets because tools only read deck, Scryfall and sim data.

## 5. Trace and job metadata

`OracleTrace` holds `task, model, pack_id, turns: [{n, kind: "tool"|"answer"|"nudge"|"repair"|"finalize", usage, calls: [{id, tool, args_hash (sha256[:12] of sorted-key JSON), ok, error_kind, ms, result_chars, truncated}]}], totals: {tokens_in, tokens_out, cost_usd|None, tool_calls}, stop_reason, error|None`.

It exposes `trace.to_job_meta()` → `{"task", "model", "pack", "turns", "tool_calls", "cost_usd", "stop_reason"}`. Every migrated job stores that dict under `job["llm"]` (and on the saved doc where the consumer already has a meta block: `review_doc` → `oracle_summary.data.llm`, sim → `oracle_review.llm`). Full tool results are not persisted. The `evidence` list (`[{tool, sections}]` for successful `deck_stats` calls) goes in `oracle_summary.data.evidence` so the UI and tests can show that the verdict is backed by data. UI changes are out of scope: the template already ignores unknown keys.

## 6. Migration order

Each step is one PR. Steps 1–5 add unused code (no behaviour change). Steps 6–11 move one consumer each. `main.py` is a hotspot, so OL-5 and OL-6..OL-11 run **serially**, never in parallel.

### 6.1 Foundation
1. **OL-1** transport turn: `ToolCall`/`ChatTurn`/`ToolChatClient` in port, `chat_turn()` in `OpenRouterChat`.
2. **OL-2** loop + registry + caps + errors + trace + play stub (pure domain, fake client).
3. **OL-3** tools `get_deck`, `card_lookup`, `deck_stats` + `ToolContext` + `build_registry`.
4. **OL-4** `sim_run` tool + `skip_review` in `sim_service.run_job`.
5. **OL-5** `infra/llm/tasks.py LOOPS` + `factory.build_oracle(task)` + `main.py` helpers `_oracle(task)` (returns `Oracle`) and `_oracle_tool_ctx(r, deck, **kw)` (builds `ToolContext` with `scryfall_batch`, `compute_metrics`, game changers via `brackets.get_game_changers` run in the job thread, journal). No route uses them yet.

### 6.2 Consumers (review first, the core feature)
6. **OL-6 review_doc**: `build_review(r, deck, oracle_factory, metrics)` runs `Oracle.run(task="deck_review", ..., tools={get_deck, card_lookup, deck_stats, sim_run}, required_evidence={"deck_stats": {"curve","bracket","power"}})`. `ctx.metrics = metrics`. The validator is `parse_reply` + `validate_prose(fingerprint)`. `clean_prose` uses `allowed_numbers_from(outputs, metrics)`. `main.api_deck_review_rebuild` passes `lambda: _oracle("deck_review")` and a ctx factory. Doc stores `llm` + `evidence`.
7. **OL-7 sim_service + sim_diagnosis**: `write_review` and `_oracle_summary` run the loop with `{get_deck, card_lookup, deck_stats}` (no `sim_run`). The comparison JSON stays in the user message, and the guard allows its numbers plus tool numbers. Remove the `tools=[], tool_choice="none"` extras and the `OracleClient.from_env` defaults. The deterministic `fallback_review` stays as the offline state (it is not an LLM prompt, so D1 holds).
8. **OL-8 card_review**: replace `OracleLLM` with `OracleDebateLLM(oracle_factory, ctx_factory)`, which implements `DebateLLM.complete(role, system, user)` via one loop run per call. Tools: `get_deck`, `card_lookup`, `deck_stats`. Required evidence applies **only to the `judge` role**. `remove`/`keep` debaters pass `required_evidence={}`. `main._card_review_llm` is switched.
9. **OL-9 review_writer (v1 review_service)**: `ReviewWriter.__call__(payload)` runs the loop (`task="review_writer"`). It needs a deck in `ToolContext`: `review_service.build_review` already loads `deck_row`/`deck_cards`, so pass `deck={"name","commanders","cards"}` built from them via a new optional `ctx` argument on the writer call (`writer(payload, ctx=...)`; `pipeline_mod` keeps calling `writer(payload)` when ctx is absent → `get_deck` returns `ToolError("unavailable")` and no evidence is required). `default_writer()` returns None when OpenRouter isn't configured (the offline path, as today).
10. **OL-10 review_pipeline v2**: new `domain/oracle/llm_adapter.py OracleLLMClient(oracle, registry_factory)` that implements `review_pipeline.llm.LLMClient.complete(LLMRequest) -> LLMResponse`. The adapter converts `system_blocks`/`messages` to chat messages and runs the loop with tools `{get_deck, card_lookup, deck_stats}`. On the finalize turn it sends `response_format = json_schema(request.schema_name, request.schema, strict=True)`. The validator is `review_pipeline.structured.validate_schema`. It returns the accepted text and the summed usage. `LiveReviewRunner._llm` wraps the routed client **only for `openrouter:` model specs**, keeping the other providers as they are, since the loop is OpenRouter-only (spec title). Evidence is required on passes B and C only (`request.label` starts with `"B"`/`"C"`).
11. **OL-11 builds: reconstruct_agent + brew_review**: `call_agent` runs the loop (`task="reconstruct"`, `ctx.allow_hypothetical=True`, tools `{get_deck, card_lookup, deck_stats}`, required `{"deck_stats": {"curve","power"}}`). `response_format()` is sent on the finalize turn. The validator is `parse_reply(text, num_decks)`. `default_client` → `build_oracle("reconstruct")`. Remove `{"tools": [], "tool_choice": "none"}`. `brew_review._oracle_reviewer` runs the loop (`task="brew_review"`, add `"brew_review": TaskSpec(SONNET, 240, 2000)` to `TASKS` and the knowledge `TASKS` with sections `("core", "brackets")`) and keeps the `{verdict, reasons}` validator. The deterministic check stays in front of it.

### 6.3 Not in this plan
- Theory, personality read, genre autofill and deck write-up keep their current client until LLM-4a/4b. Theory is a build. When its card is written, it must use `Oracle.run` (D1), not `build_chat_client().chat()`. A follow-up card "OL-12 theory on the oracle loop" is listed and **not** created by this spec.
- LLM-7 (optional Scryfall tool loop) is absorbed by OL-1..OL-3 and is closed as superseded.

## 7. LLM-6 amendment

LLM-6 still deletes `analytics/oracle_client.py`, the `infra/llm/client.py` re-exports, `ORACLE_URL/KEY/TIMEOUT_S/MODEL` in both compose files, the GITOPS Hermes-oracle section and `docs/oracle/`. It runs after OL-11 and LLM-4a/4b. Its CI guard pattern stays `ollama|11434|ORACLE_|oracle_client|OracleClient`. That pattern does not match `domain/oracle/`, `Oracle` or `oracle_summary`, so the name survives. The guard must not be widened to bare `oracle`.

## 8. Play (reserved, D3)

`domain/oracle/play.py`:

```python
class PlaySession(Protocol):
    """Reserved: an interactive game-state loop (oracle as a player/advisor). Not implemented."""
    def step(self, observation: Mapping[str, Any]) -> Mapping[str, Any]: ...

PLAY_TOOLS: tuple[str, ...] = ()   # reserved registry slot; no tools in v1

def start_play(*_a, **_k) -> PlaySession:
    raise NotImplementedError("oracle play is reserved; see docs/superpowers/specs/2026-11-10-oracle-tool-loop-design.md §8")
```

Nothing imports it except its test. No task entry, route or UI.

## 9. Test strategy

There are no live LLM calls in CI. Every OL card ships its own tests.

1. **Transport (OL-1), `httpx.MockTransport`** in `tests/test_llm_openrouter_chat_turn.py`:
   - one tool call → `ChatTurn.tool_calls[0]` has id, name and parsed args; content `""`; no exception;
   - parallel tool calls keep their order;
   - `finish_reason:"stop"` with tool calls still yields the calls;
   - `arguments` not JSON → `parse_error` set, `arguments` None;
   - missing id → `call_1_0`;
   - non-function entry dropped;
   - empty content and no calls → `LLMResponseError`;
   - `length` → `LLMResponseError`;
   - request body carries `tools`, `tool_choice`, `provider.require_parameters` and `data_collection:"deny"`;
   - the key never appears in any raised message (inject the key into the upstream error body);
   - the existing `chat()` tests are untouched and green.
2. **Loop (OL-2), scripted fake** `ScriptedToolChat(turns=[ChatTurn, ...])` in `tests/oracle_fakes.py`. It records every `messages` list it receives.
   - happy path: tool → tool → answer; messages contain the assistant `tool_calls` then `tool` messages with matching `tool_call_id`;
   - evidence nudge then success; evidence missing twice → `OracleEvidenceMissing`;
   - invalid answer → repair → accepted; invalid twice → `OracleInvalidAnswer`;
   - `max_turns` → finalize with `tool_choice="none"` and `response_format`; tools after finalize → `OracleBudgetExceeded`;
   - cost soft → finalize, cost hard → abort before the call; `cost_usd=None` → token meter;
   - deadline with an injected clock;
   - more than `max_calls_per_turn` calls → `too_many_calls` results;
   - unknown tool, bad args (schema), handler exception (`tool_failed`, no exception text), three failures → `disabled`, `max_calls` → `call_limit`;
   - result over `max_result_chars` → truncated, under the cap;
   - trace totals and `to_job_meta()` shape;
   - `play.start_play` raises `NotImplementedError`.
3. **Loop + transport integration (OL-2)**: `Oracle` over a real `OpenRouterChat` whose `MockTransport` serves a 3-response script (tool call, tool call, final JSON). Assert the second and third HTTP bodies contain the tool messages. This is the one end-to-end CI test of the call → run → feed-back loop.
4. **Tools (OL-3/OL-4)**: pure tests with fake `scryfall_batch`, fixture deck dicts and a `FakeRedis` (pattern from `tests/test_review_doc.py`). Cover: card_lookup not-found and the 60-name run limit; deck_stats sections, memoization (`compute_metrics` called once), game changers unavailable, `cards` override rejected unless `allow_hypothetical`; sim_run saved/new_run/busy/timeout with a fake `SimClient`, and `skip_review` never calls an LLM factory.
5. **Consumers (OL-6..OL-11)**: the existing consumer tests switch their fake client to `ScriptedToolChat`. Each adds: (a) a run with `deck_stats` evidence → doc saved, `llm` and `evidence` present; (b) a scripted answer quoting a number that only a tool returned → kept; a number from nowhere → stripped; (c) `LLMUnavailable` → existing offline state; (d) evidence missing → `invalid` with the note; (e) `grep`-style assertion that the module no longer imports `oracle_client`.
6. **Live smoke (tester card after OL-6)**: one real deck review on prod or staging with the OpenRouter key. Attach the saved doc JSON (`llm` meta, evidence list, cost). The key is never shown.
7. Gates on every PR: `pytest`, `lint-imports` (no new ignores), and `python3 scripts/changelog.py validate`.

## 10. Changelog

OL-1..OL-5: no user-facing change (tick the box). OL-6: `### Changed` "Deep Review: the written verdict now checks the deck's curve, bracket and power level before it's written, and shows which checks it used." OL-7..OL-11 each add one `### Changed` line in user terms for their feature (sim review, card review, review documents, Brew/Reconstruct).

## 11. Non-goals

No play implementation. No spot-check reviews. No single-prompt fallback path. No git-history scrub. No UI redesign (new metadata is additive). No provider other than OpenRouter for the loop. No change to theory, personality, autofill or write-up in this plan.
