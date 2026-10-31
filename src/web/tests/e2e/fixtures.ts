import { test as base, expect, type APIRequestContext, type Page } from '@playwright/test';
import { createRequire } from 'node:module';

export const MOCK = `http://127.0.0.1:${process.env.MOCK_API_PORT ?? 8799}`;

export interface MockDoc {
	id: number;
	status: 'queued' | 'running' | 'ok' | 'failed';
	stage?: string;
	error?: string | null;
	created_at: string;
}
export interface MockConfig {
	down: boolean;
	profileMissing: boolean;
	putStatus: number;
	putDetail: string;
	listStatus: number;
	rowStatus: number;
	feedbackStatus: number;
	generateStatus: number;
	pdfStatus: number;
	documents: MockDoc[];
	activeId: number | null;
	commitStatus: number;
	commitDetail: string;
	commitDelayMs: number;
	abandonStatus: number;
	abandonDelayMs: number;
	searchStatus: number;
	searchDelayMs: number;
	searchEmpty: boolean;
	searchDegraded: boolean;
}
export interface RecordedRequest { method: string; path: string; body: unknown; query?: string }

export class Mock {
	constructor(private api: APIRequestContext) {}
	reset() { return this.api.post(`${MOCK}/__mock/reset`); }
	async set(patch: Partial<MockConfig>) { await this.api.post(`${MOCK}/__mock/config`, { data: patch }); }
	async requests(): Promise<RecordedRequest[]> { return (await this.api.get(`${MOCK}/__mock/requests`)).json(); }
	async count(method: string, pathRe: RegExp) {
		return (await this.requests()).filter((r) => r.method === method && pathRe.test(r.path)).length;
	}
	async state(): Promise<{ profile: Record<string, unknown>; provenance: Record<string, { source: string; at: string }> }> {
		return (await this.api.get(`${MOCK}/__mock/state`)).json();
	}
}

/** Every test starts from a freshly reset mock (the suite runs with one worker, so state is not shared). */
export const test = base.extend<{ mock: Mock }>({
	mock: [async ({ playwright }, use) => {
		const api = await playwright.request.newContext();
		const mock = new Mock(api);
		await mock.reset();
		await use(mock);
		await api.dispose();
	}, { auto: true }]
});
export { expect };

export const ok = (id: number, at = '2026-10-29T14:00:00Z'): MockDoc => ({ id, status: 'ok', created_at: at });
export const failed = (id: number, error = 'The model timed out', at = '2026-10-29T15:00:00Z'): MockDoc => ({ id, status: 'failed', error, created_at: at });
export const running = (id: number, stage = 'analysing', at = new Date().toISOString()): MockDoc => ({ id, status: 'running', stage, created_at: at });

// --- axe ------------------------------------------------------------------------------------------
const axePath = createRequire(import.meta.url).resolve('axe-core/axe.min.js');
export const VIEWPORTS = [{ width: 375, height: 800 }, { width: 1440, height: 900 }] as const;

/** Runs axe-core (bundled from node_modules, no CDN) and fails with a readable list of violations. */
export async function expectNoAxeViolations(page: Page, opts: { disableRules?: string[] } = {}) {
	if (!(await page.evaluate(() => 'axe' in window))) await page.addScriptTag({ path: axePath });
	const violations = await page.evaluate(async (disabled) => {
		const rules = Object.fromEntries(disabled.map((id) => [id, { enabled: false }]));
		// @ts-expect-error injected by the script tag above
		const r = await window.axe.run(document, { rules, runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] } });
		return r.violations.map((v: { id: string; impact: string; nodes: { target: unknown[] }[] }) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`);
	}, opts.disableRules ?? []);
	expect(violations, 'axe violations').toEqual([]);
}

// --- drafts ---------------------------------------------------------------------------------------
export const DRAFT = '/drafts/draft-1';
/** Requests of one kind the mock saw, e.g. `draftPosts(mock, 'cards')`. */
export async function draftPosts(mock: Mock, sub: 'cards' | 'commit' | 'abandon') {
	return (await mock.requests()).filter((r) => r.method === 'POST' && new RegExp(`/api/v2/drafts/draft-1/${sub}$`).test(r.path));
}
