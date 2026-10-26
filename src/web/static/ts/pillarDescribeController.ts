/**
 * Framework-agnostic state-machine controller for the pillar describe flow.
 * Wraps the typed client (pillarDescribeClient.ts) and exposes a simple
 * {idle|loading|success|error} state plus onSuccess/onError callbacks so any
 * UI — vanilla JS, Jinja2 templates, or a future framework — can wire in
 * without managing fetch, abort, or error classification itself.
 */

import type { PillarDescribeRequest, PillarDescribeOptions, PillarDescribeMode } from "./pillarDescribeClient.ts";
import { PillarDescribeError, describePillar } from "./pillarDescribeClient.ts";

export type ControllerState = "idle" | "loading" | "success" | "error";

export interface ControllerResult {
  description: string;
  mode: PillarDescribeMode;
}

export interface ControllerCallbacks {
  onStateChange?: (state: ControllerState) => void;
  onSuccess?: (result: ControllerResult) => void;
  onError?: (error: PillarDescribeError) => void;
}

export interface ControllerOptions extends PillarDescribeOptions {
  callbacks?: ControllerCallbacks;
}

export class PillarDescribeController {
  private _state: ControllerState = "idle";
  private _ctl: AbortController | null = null;
  private _callbacks: ControllerCallbacks;
  private _opts: PillarDescribeOptions;

  constructor(opts: ControllerOptions = {}) {
    const { callbacks, ...clientOpts } = opts;
    this._callbacks = callbacks ?? {};
    this._opts = clientOpts;
  }

  get state(): ControllerState {
    return this._state;
  }

  generate(deckId: string, req: PillarDescribeRequest): void {
    if (this._state === "loading") return;

    this.cancel();
    this._ctl = new AbortController();

    const signal = this._ctl.signal;
    const opts: PillarDescribeOptions = {
      ...this._opts,
      signal,
    };

    this.transition("loading");

    describePillar(deckId, req, opts)
      .then((res) => {
        if (signal.aborted) return;
        this._ctl = null;
        this.transition("success");
        this._callbacks.onSuccess?.({ description: res.description, mode: res.mode as PillarDescribeMode });
      })
      .catch((err) => {
        // _ctl is cleared by cancel(); if it's still set the error came from the request itself
        if (!this._ctl) return;
        this._ctl = null;
        const e = err instanceof PillarDescribeError ? err : new PillarDescribeError(err instanceof Error ? err.message : String(err), 0);
        this.transition("error");
        this._callbacks.onError?.(e);
      });
  }

  cancel(): void {
    if (this._ctl) {
      this._ctl.abort();
      this._ctl = null;
      this.transition("idle");
    }
  }

  reset(): void {
    this.cancel();
    this._state = "idle";
    this._callbacks.onStateChange?.("idle");
  }

  setCallbacks(cb: ControllerCallbacks): void {
    this._callbacks = cb;
  }

  private transition(next: ControllerState): void {
    if (this._state !== next) {
      this._state = next;
      this._callbacks.onStateChange?.(next);
    }
  }
}
