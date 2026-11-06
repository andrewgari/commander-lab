/** Typed client for POST /api/decks/{deck_id}/identity/pillars/describe.
 *  Nothing is saved by the call; it returns a suggested description.
 *  Errors: PillarDescribeError carries status, server message, error_kind, and `timedOut`/`aborted` flags. */

export type PillarDescribeMode = "generate" | "improve";
export type PillarDescribeErrorKind = "no_llm" | "llm_failed" | "bad_response";

/** At least one of name / prompt / description must be non-blank (server returns 422 otherwise). */
export interface PillarDescribeRequest {
  name?: string;
  prompt?: string;
  description?: string;
  /** Pillar being edited; excluded from the "other pillars" context. */
  pillar_id?: string;
}

export interface PillarDescribeResponse {
  success: true;
  description: string;
  mode: PillarDescribeMode;
  provider?: string;
  model?: string;
}

export interface PillarDescribeOptions {
  signal?: AbortSignal;
  /** Default 20000 ms. 0 disables. */
  timeoutMs?: number;
  /** Injectable for tests; defaults to global fetch. */
  fetchImpl?: typeof fetch;
  /** Prefix for the URL, e.g. "" (default). */
  baseUrl?: string;
}

export class PillarDescribeError extends Error {
  readonly status: number;
  readonly errorKind?: PillarDescribeErrorKind | string;
  readonly timedOut: boolean;
  readonly aborted: boolean;
  constructor(message: string, status: number, errorKind?: string, timedOut = false, aborted = false) {
    super(message);
    this.name = "PillarDescribeError";
    this.status = status;
    this.errorKind = errorKind;
    this.timedOut = timedOut;
    this.aborted = aborted;
  }
}

export const DEFAULT_TIMEOUT_MS = 20000;

export function pillarDescribeUrl(deckId: string, baseUrl = ""): string {
  return `${baseUrl}/api/decks/${encodeURIComponent(deckId)}/identity/pillars/describe`;
}

/** Drops undefined fields so the JSON body only has what the caller set. */
export function serializeRequest(req: PillarDescribeRequest): string {
  const body: Record<string, string> = {};
  for (const k of ["name", "prompt", "description", "pillar_id"] as const) {
    const v = req[k];
    if (typeof v === "string") body[k] = v;
  }
  return JSON.stringify(body);
}

export async function describePillar(
  deckId: string,
  req: PillarDescribeRequest,
  opts: PillarDescribeOptions = {},
): Promise<PillarDescribeResponse> {
  const doFetch = opts.fetchImpl ?? fetch;
  const ctl = new AbortController();
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  let timedOut = false;
  const timer = timeoutMs > 0 ? setTimeout(() => { timedOut = true; ctl.abort(); }, timeoutMs) : undefined;
  const onAbort = () => ctl.abort();
  if (opts.signal) {
    if (opts.signal.aborted) ctl.abort();
    else opts.signal.addEventListener("abort", onAbort, { once: true });
  }

  try {
    // Short-circuit before the request goes out when the caller already aborted.
    if (opts.signal?.aborted) throw new PillarDescribeError("Request aborted", 0, undefined, false, true);
    let res: Response;
    try {
      res = await doFetch(pillarDescribeUrl(deckId, opts.baseUrl), {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: serializeRequest(req),
        signal: ctl.signal,
      });
    } catch (e) {
      if (timedOut) throw new PillarDescribeError("Request timed out", 0, undefined, true, false);
      if (ctl.signal.aborted) throw new PillarDescribeError("Request aborted", 0, undefined, false, true);
      throw new PillarDescribeError(e instanceof Error ? e.message : "Network error", 0);
    }

    let data: unknown = null;
    try {
      data = await res.json();
    } catch {
      /* non-JSON body: handled below */
    }
    const obj = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;

    if (!res.ok || obj.success === false) {
      const msg = typeof obj.error === "string" && obj.error ? obj.error : `Request failed (${res.status})`;
      throw new PillarDescribeError(msg, res.status, typeof obj.error_kind === "string" ? obj.error_kind : undefined);
    }
    if (typeof obj.description !== "string" || (obj.mode !== "generate" && obj.mode !== "improve")) {
      throw new PillarDescribeError("Malformed response from server", res.status, "bad_response");
    }
    return obj as unknown as PillarDescribeResponse;
  } finally {
    if (timer) clearTimeout(timer);
    opts.signal?.removeEventListener("abort", onAbort);
  }
}
