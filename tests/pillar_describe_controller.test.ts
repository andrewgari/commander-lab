import assert from "node:assert/strict";
import { test } from "node:test";
import { PillarDescribeController } from "../src/web/static/ts/pillarDescribeController.ts";
import type { ControllerState } from "../src/web/static/ts/pillarDescribeController.ts";
import { PillarDescribeError } from "../src/web/static/ts/pillarDescribeClient.ts";

// ---- Helpers ------------------------------------------------------------------

/** A fetch impl that resolves with the given JSON after `delay` ms. */
function jsonFetch(status: number, body: unknown, delay = 0): typeof fetch {
  return (async (_u: string, _i: RequestInit) => {
    await new Promise((r) => setTimeout(r, delay));
    return new Response(JSON.stringify(body), { status });
  }) as unknown as typeof fetch;
}

/** A fetch impl that never resolves but rejects on abort. */
const hang = ((_u: string, i: RequestInit) =>
  new Promise<Response>((_, rej) => {
    i.signal?.addEventListener("abort", () => rej(new Error("aborted")));
  })) as unknown as typeof fetch;

/** Collect state transitions from `onStateChange`. */
function collectTransitions(ctl: PillarDescribeController): ControllerState[] {
  const log: ControllerState[] = [ctl.state];
  ctl.setCallbacks({
    onStateChange: (s) => log.push(s),
    onSuccess: () => {},
    onError: () => {},
  });
  return log;
}

// ---- Tests --------------------------------------------------------------------

test("initial state is idle", () => {
  const ctl = new PillarDescribeController();
  assert.equal(ctl.state, "idle");
});

test("generate transitions idle → loading → success", async () => {
  const ctl = new PillarDescribeController({
    fetchImpl: jsonFetch(200, { success: true, description: "test desc", mode: "generate" }),
  });
  const log = collectTransitions(ctl);

  let captured: { description: string; mode: string } | null = null;
  ctl.setCallbacks({
    ...ctl["_callbacks"],
    onSuccess: (r) => { captured = r; },
  });

  ctl.generate("archidekt:1", { name: "Ramp" });
  assert.equal(ctl.state, "loading");
  assert.deepEqual(log, ["idle", "loading"]);

  // Wait for the async success
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(ctl.state, "success");
  assert.deepEqual(log, ["idle", "loading", "success"]);
  assert.equal(captured?.description, "test desc");
  assert.equal(captured?.mode, "generate");
});

test("generate transitions idle → loading → error on server error", async () => {
  const ctl = new PillarDescribeController({
    fetchImpl: jsonFetch(503, { success: false, error: "no model", error_kind: "no_llm" }),
  });
  const log = collectTransitions(ctl);

  let capturedErr: PillarDescribeError | null = null;
  ctl.setCallbacks({
    ...ctl["_callbacks"],
    onError: (e) => { capturedErr = e; },
  });

  ctl.generate("x", { name: "a" });
  assert.equal(ctl.state, "loading");

  await new Promise((r) => setTimeout(r, 20));
  assert.equal(ctl.state, "error");
  assert.deepEqual(log, ["idle", "loading", "error"]);
  assert.ok(capturedErr instanceof PillarDescribeError);
  assert.equal(capturedErr!.status, 503);
  assert.equal(capturedErr!.errorKind, "no_llm");
});

test("double generate is a no-op while loading", () => {
  const ctl = new PillarDescribeController({ fetchImpl: hang });
  ctl.generate("x", { name: "a" });
  assert.equal(ctl.state, "loading");
  // Second call should not change state or create another request
  ctl.generate("x", { name: "b" });
  assert.equal(ctl.state, "loading");
  ctl.cancel();
});

test("cancel aborts request and returns to idle", async () => {
  const ctl = new PillarDescribeController({ fetchImpl: hang });
  const log = collectTransitions(ctl);
  ctl.generate("x", { name: "a" });
  assert.equal(ctl.state, "loading");

  ctl.cancel();
  assert.equal(ctl.state, "idle");
  assert.deepEqual(log, ["idle", "loading", "idle"]);
});

test("cancel does not fire onError when aborting an in-flight request", async () => {
  const ctl = new PillarDescribeController({ fetchImpl: hang });
  let errorFired = false;
  ctl.setCallbacks({
    onStateChange: () => {},
    onSuccess: () => {},
    onError: () => { errorFired = true; },
  });
  ctl.generate("x", { name: "a" });
  ctl.cancel();
  // Let microtasks flush
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(errorFired, false, "onError should not fire after cancel");
});

test("reset returns to idle and fires onStateChange even if already idle", () => {
  const ctl = new PillarDescribeController();
  let lastState: ControllerState | null = null;
  ctl.setCallbacks({
    onStateChange: (s) => { lastState = s; },
    onSuccess: () => {},
    onError: () => {},
  });
  assert.equal(ctl.state, "idle");
  ctl.reset();
  assert.equal(ctl.state, "idle");
  assert.equal(lastState, "idle", "reset fires onStateChange even from idle");
});

test("timeout fires onError", async () => {
  const ctl = new PillarDescribeController({ fetchImpl: hang, timeoutMs: 20 });
  const log = collectTransitions(ctl);
  let capturedErr: PillarDescribeError | null = null;
  ctl.setCallbacks({
    ...ctl["_callbacks"],
    onError: (e) => { capturedErr = e; },
  });

  ctl.generate("x", { name: "a" });
  await new Promise((r) => setTimeout(r, 50));
  assert.equal(ctl.state, "error");
  assert.deepEqual(log, ["idle", "loading", "error"]);
  assert.ok(capturedErr instanceof PillarDescribeError);
  assert.equal(capturedErr!.timedOut, true);
});

test("setCallbacks replaces callbacks mid-lifecycle", async () => {
  const ctl = new PillarDescribeController({
    fetchImpl: jsonFetch(200, { success: true, description: "d", mode: "generate" }),
  });

  let firstSuccess = false;
  let replacedSuccess = false;
  ctl.setCallbacks({
    onStateChange: () => {},
    onSuccess: () => { firstSuccess = true; },
    onError: () => {},
  });

  ctl.generate("x", { name: "a" });
  // Replace while loading
  ctl.setCallbacks({
    onStateChange: () => {},
    onSuccess: () => { replacedSuccess = true; },
    onError: () => {},
  });

  await new Promise((r) => setTimeout(r, 20));
  assert.equal(firstSuccess, false, "old callback replaced");
  assert.equal(replacedSuccess, true, "new callback fired");
});