import assert from "node:assert/strict";
import { test } from "node:test";
import { describePillar, PillarDescribeError, serializeRequest } from "../src/web/static/ts/pillarDescribeClient.ts";

const json = (status: number, body: unknown) =>
  (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

test("serializes only set fields and encodes deck id", async () => {
  let url = "", init: RequestInit = {};
  const f = (async (u: string, i: RequestInit) => { url = u; init = i; return new Response(JSON.stringify({ success: true, description: "d", mode: "generate" })); }) as unknown as typeof fetch;
  const out = await describePillar("archidekt:1", { name: "Ramp", description: undefined }, { fetchImpl: f });
  assert.equal(url, "/api/decks/archidekt%3A1/identity/pillars/describe");
  assert.equal(init.body, '{"name":"Ramp"}');
  assert.equal(init.method, "POST");
  assert.equal(out.mode, "generate");
  assert.equal(serializeRequest({ prompt: "" }), '{"prompt":""}');
});

test("422/503 propagate status, message, kind", async () => {
  await assert.rejects(describePillar("x", {}, { fetchImpl: json(422, { success: false, error: "need input" }) }),
    (e: PillarDescribeError) => e.status === 422 && e.message === "need input");
  await assert.rejects(describePillar("x", { name: "a" }, { fetchImpl: json(503, { success: false, error: "no model", error_kind: "no_llm" }) }),
    (e: PillarDescribeError) => e.status === 503 && e.errorKind === "no_llm");
});

test("non-JSON error and malformed success", async () => {
  const html = (async () => new Response("<html>", { status: 502 })) as unknown as typeof fetch;
  await assert.rejects(describePillar("x", { name: "a" }, { fetchImpl: html }), (e: PillarDescribeError) => e.status === 502);
  await assert.rejects(describePillar("x", { name: "a" }, { fetchImpl: json(200, { success: true }) }),
    (e: PillarDescribeError) => e.errorKind === "bad_response");
});

test("timeout and caller abort", async () => {
  const hang = ((_u: string, i: RequestInit) => new Promise((_, rej) => i.signal!.addEventListener("abort", () => rej(new Error("x"))))) as unknown as typeof fetch;
  await assert.rejects(describePillar("x", { name: "a" }, { fetchImpl: hang, timeoutMs: 20 }), (e: PillarDescribeError) => e.timedOut);
  const c = new AbortController(); setTimeout(() => c.abort(), 10);
  await assert.rejects(describePillar("x", { name: "a" }, { fetchImpl: hang, signal: c.signal }), (e: PillarDescribeError) => e.aborted && !e.timedOut);
});
