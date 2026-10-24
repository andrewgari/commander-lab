// Edge-case and integration-mock tests for the pillar describe flow.
// Complements pillar_describe_client.test.ts (4 core cases) and
// pillar_describe_controller.test.ts (9 lifecycle cases) with the gaps:
// URL builder, baseUrl prefixing, header contract, aborted-signal shortcut,
// network-error classification, HTTP-200-with-success-false, mode validation,
// non-Error rejection, and controller re-generate / reset-during-loading /
// callback-throw isolation.
//
// Run: node --test tests/pillar_describe_edge.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  describePillar,
  pillarDescribeUrl,
  serializeRequest,
  PillarDescribeError,
  DEFAULT_TIMEOUT_MS,
} from "../static/ts/pillarDescribeClient.ts";
import { PillarDescribeController } from "../static/ts/pillarDescribeController.ts";

const OK = { success: true, description: "d", mode: "generate" };

const jsonResp = (status: number, body: unknown) =>
  (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

const flush = (ms = 20) => new Promise((r) => setTimeout(r, ms));

/** fetch spy that records the last init and returns a canned response. */
function spy(status: number, body: unknown) {
  const calls: { url: string; init: RequestInit }[] = [];
  const f = (async (u: string | URL | Request, i: RequestInit = {}) => {
    calls.push({ url: String(u), init: i });
    return new Response(JSON.stringify(body), { status });
  }) as unknown as typeof fetch;
  return { f, calls };
}

// ---- URL builder --------------------------------------------------------------

test("pillarDescribeUrl encodes special chars and honors baseUrl", () => {
  assert.equal(pillarDescribeUrl("archidekt:417707"), "/api/decks/archidekt%3A417707/identity/pillars/describe");
  assert.equal(pillarDescribeUrl("a b/c?d"), "/api/decks/a%20b%2Fc%3Fd/identity/pillars/describe");
  assert.equal(
    pillarDescribeUrl("x", "[PERSON_NAME]"),
    "[PERSON_NAME]/api/decks/x/identity/pillars/describe",
  );
});

// ---- Request contract ---------------------------------------------------------

test("sends POST with JSON headers, Accept, and AbortSignal", async () => {
  const { f, calls } = spy(200, OK);
  await describePillar("deck1", { name: "Ramp" }, { fetchImpl: f });
  const init = calls[0].init;
  assert.equal(init.method, "POST");
  const headers = new Headers(init.headers);
  assert.equal(headers.get("content-type"), "application/json");
  assert.equal(headers.get("accept"), "application/json");
  assert.ok(init.signal instanceof AbortSignal, "fetch must receive an AbortSignal");
});

test("serializeRequest drops null/non-string fields but keeps empty strings", () => {
  assert.equal(serializeRequest({ name: "a", prompt: null as unknown as string, pillar_id: undefined }), '{"name":"a"}');
  assert.equal(serializeRequest({ name: "", description: "" }), '{"name":"","description":""}');
  assert.equal(serializeRequest({}), "{}");
});

// ---- Client edge cases --------------------------------------------------------

test("already-aborted signal short-circuits with aborted error, fetch never called", async () => {
  let called = false;
  const f = (async () => { called = true; return new Response("{}", { status: 200 }); }) as unknown as typeof fetch;
  const c = new AbortController();
  c.abort();
  await assert.rejects(
    describePillar("x", { name: "a" }, { fetchImpl: f, signal: c.signal }),
    (e: PillarDescribeError) => e.aborted && !e.timedOut && e.status === 0,
  );
  assert.equal(called, false, "pre-aborted signal must not dispatch the request");
});

test("network TypeError is classified as status 0 with its message", async () => {
  const f = (async () => { throw new TypeError("Failed to fetch"); }) as unknown as typeof fetch;
  await assert.rejects(
    describePillar("x", { name: "a" }, { fetchImpl: f }),
    (e: PillarDescribeError) => e.status === 0 && e.message === "Failed to fetch" && !e.timedOut && !e.aborted,
  );
});

test("non-Error rejection falls back to 'Network error'", async () => {
  const f = (async () => { throw "boom"; }) as unknown as typeof fetch;
  await assert.rejects(
    describePillar("x", { name: "a" }, { fetchImpl: f }),
    (e: PillarDescribeError) => e.status === 0 && e.message === "Network error",
  );
});

test("HTTP 200 with success:false is treated as an error", async () => {
  const f = jsonResp(200, { success: false, error: "model hiccup", error_kind: "llm_failed" });
  await assert.rejects(
    describePillar("x", { name: "a" }, { fetchImpl: f }),
    (e: PillarDescribeError) =>
      e.status === 200 && e.message === "model hiccup" && e.errorKind === "llm_failed",
  );
});

test("success payload with invalid mode is bad_response", async () => {
  const f = jsonResp(200, { success: true, description: "fine", mode: "rewrite" });
  await assert.rejects(
    describePillar("x", { name: "a" }, { fetchImpl: f }),
    (e: PillarDescribeError) => e.errorKind === "bad_response" && e.status === 200,
  );
});

test("improve mode round-trips provider/model metadata", async () => {
  const f = jsonResp(200, { success: true, description: "better", mode: "improve", provider: "ollama", model: "qwen" });
  const out = await describePillar("x", { description: "old" }, { fetchImpl: f });
  assert.equal(out.mode, "improve");
  assert.equal(out.provider, "ollama");
  assert.equal(out.model, "qwen");
});

test("timeoutMs:0 disables the internal timeout", async () => {
  let abortedDuringFetch = false;
  const f = ((_u: string, i: RequestInit) => {
    // signal must not fire on its own within the window
    i.signal?.addEventListener("abort", () => { abortedDuringFetch = true; });
    return new Promise<Response>((resolve) => setTimeout(() => resolve(new Response(JSON.stringify(OK), { status: 200 })), 30));
  }) as unknown as typeof fetch;
  const out = await describePillar("x", { name: "a" }, { fetchImpl: f, timeoutMs: 0 });
  assert.equal(out.description, "d");
  assert.equal(abortedDuringFetch, false);
});

test("DEFAULT_TIMEOUT_MS is 20s", () => {
  assert.equal(DEFAULT_TIMEOUT_MS, 20000);
});

// ---- Controller edge cases ----------------------------------------------------

test("controller generate after success works (idle guard only blocks loading)", async () => {
  let n = 0;
  const ctl = new PillarDescribeController({
    fetchImpl: (async () => new Response(JSON.stringify({ ...OK, description: `desc-${++n}` }), { status: 200 })) as unknown as typeof fetch,
  });
  const results: string[] = [];
  ctl.setCallbacks({ onSuccess: (r) => results.push(r.description) });

  ctl.generate("x", { name: "a" });
  await flush();
  assert.equal(ctl.state, "success");
  ctl.generate("x", { name: "b" });
  assert.equal(ctl.state, "loading");
  await flush();
  assert.equal(ctl.state, "success");
  assert.deepEqual(results, ["desc-1", "desc-2"]);
});

test("controller generate after error works", async () => {
  let first = true;
  const ctl = new PillarDescribeController({
    fetchImpl: (async () => {
      if (first) { first = false; return new Response(JSON.stringify({ success: false, error: "nope" }), { status: 502 }); }
      return new Response(JSON.stringify(OK), { status: 200 });
    }) as unknown as typeof fetch,
  });
  ctl.setCallbacks({ onError: () => {}, onSuccess: () => {} });
  ctl.generate("x", { name: "a" });
  await flush();
  assert.equal(ctl.state, "error");
  ctl.generate("x", { name: "a" });
  await flush();
  assert.equal(ctl.state, "success");
});

test("reset during loading cancels request and lands idle without firing error", async () => {
  const hang = ((_u: string, i: RequestInit) =>
    new Promise<Response>((_, rej) => i.signal?.addEventListener("abort", () => rej(new Error("aborted"))))) as unknown as typeof fetch;
  const ctl = new PillarDescribeController({ fetchImpl: hang });
  const states: string[] = [];
  let errored = false;
  ctl.setCallbacks({
    onStateChange: (s) => states.push(s),
    onError: () => { errored = true; },
  });
  ctl.generate("x", { name: "a" });
  ctl.reset();
  assert.equal(ctl.state, "idle");
  await flush();
  assert.equal(errored, false, "reset must not surface an error");
  assert.deepEqual(states, ["loading", "idle", "idle"]); // cancel->idle then reset's explicit idle
});

test("non-PillarDescribeError rejection from the client is wrapped with its raw message", async () => {
  // The controller's catch wraps anything that isn't already a PillarDescribeError.
  // That branch is unreachable through fetchImpl (the client classifies every fetch
  // failure itself), so load a copy of the controller source whose client import is
  // rewritten to an in-memory stub module, and make the stub reject with a foreign Error.
  const { readFile, writeFile } = await import("node:fs/promises");
  const STUB_SRC = `
    export class PillarDescribeError extends Error {
      constructor(message, status, errorKind, timedOut = false, aborted = false) {
        super(message); this.name = "PillarDescribeError";
        this.status = status; this.errorKind = errorKind;
        this.timedOut = timedOut; this.aborted = aborted;
      }
    }
    export const box = { impl: null };
    export function describePillar(a, b, c) { return box.impl(a, b, c); }
  `;
  const stubUrl = "data:text/javascript," + encodeURIComponent(STUB_SRC);
  const stub = (await import(stubUrl)) as unknown as { box: { impl: null | ((...args: unknown[]) => Promise<unknown>) }; PillarDescribeError: typeof PillarDescribeError };

  const controllerSrc = await readFile(new URL("../static/ts/pillarDescribeController.ts", import.meta.url), "utf8");
  const rewritten = controllerSrc.replaceAll("./pillarDescribeClient.ts", stubUrl);
  assert.notEqual(rewritten, controllerSrc, "controller must import the client via ./pillarDescribeClient.ts");
  // Serve the rewritten source as a .mts copy so Node applies type-stripping (data: URLs are not transpiled).
  // The stub exports its own PillarDescribeError class; the real one is structurally identical, so assert
  // on the stub's constructor identity — that is the class the controller actually instantiates.
  const StubError = stub.PillarDescribeError;
  const tmpPath = new URL(`../.pillar_controller_stubbed_${process.pid}.mts`, import.meta.url);
  await writeFile(tmpPath, rewritten);
  const { PillarDescribeController: StubbedController } = await import(tmpPath.href);
  try {
    stub.box.impl = async () => { throw new Error("kaboom"); };
    let captured: unknown = null;
    const ctl = new StubbedController();
    ctl.setCallbacks({ onError: (e: unknown) => { captured = e; } });
    ctl.generate("x", { name: "a" });
    await flush();
    assert.equal(ctl.state, "error");
    assert.ok(captured instanceof StubError, "controller must wrap foreign errors in PillarDescribeError");
    assert.equal((captured as PillarDescribeError).message, "kaboom");
    assert.equal((captured as PillarDescribeError).status, 0);

    // sanity: the stubbed wiring still delivers the success path unchanged
    stub.box.impl = async () => ({ description: "ok", mode: "generate" });
    let result: unknown = null;
    const ctl2 = new StubbedController();
    ctl2.setCallbacks({ onSuccess: (r: unknown) => { result = r; } });
    ctl2.generate("x", { name: "a" });
    await flush();
    assert.equal(ctl2.state, "success");
    assert.deepEqual(result, { description: "ok", mode: "generate" });
  } finally {
    await import("node:fs/promises").then((m) => m.rm(tmpPath, { force: true }));
  }
});

test("throwing onSuccess does not corrupt state or block later runs", async () => {
  const ctl = new PillarDescribeController({ fetchImpl: jsonResp(200, OK) });
  ctl.setCallbacks({ onSuccess: () => { throw new Error("ui blew up"); } });
  ctl.generate("x", { name: "a" });
  await flush();
  // state already transitioned to success before the callback ran
  assert.equal(ctl.state, "success");
  // recover: swap callbacks, run again cleanly
  let ok = false;
  ctl.setCallbacks({ onSuccess: () => { ok = true; } });
  ctl.generate("x", { name: "b" });
  await flush();
  assert.equal(ok, true);
});

test("baseUrl option flows through controller into client", async () => {
  const { f, calls } = spy(200, OK);
  const ctl = new PillarDescribeController({ fetchImpl: f, baseUrl: "[PERSON_NAME]" });
  ctl.generate("deck 9", { name: "a" });
  await flush();
  assert.equal(calls[0].url, "[PERSON_NAME]/api/decks/deck%209/identity/pillars/describe");
});
