import { describe, expect, it } from 'vitest';
import type { ReviewDocumentSummary } from '#lib/api.ts';
import { MAX_POLL_FAILURES, derive, failureHeadline, historyLabel, partialSections, pollDelay, transition, type Part } from './reviewState.ts';

const sum = (id: number, status: ReviewDocumentSummary['status'], extra: Partial<ReviewDocumentSummary> = {}): ReviewDocumentSummary => ({
	id, deck_id: 'd', status, stage: '', error: null, grade: null, headline: null, created_at: '2026-10-29T14:00:00Z', started_at: null, finished_at: null, ...extra
});
const okList = (documents: ReviewDocumentSummary[], active: ReviewDocumentSummary | null = null): Part<{ documents: ReviewDocumentSummary[]; active: ReviewDocumentSummary | null }> => ({ ok: true, value: { documents, active } });
const row = (s: ReviewDocumentSummary, doc: Record<string, unknown> | null = null): Part<any> => ({ ok: true, value: { ...s, doc } });
const bad = (reason = 'boom'): Part<any> => ({ ok: false, reason, status: 503 });
const none = { lastActive: null, pollFailures: 0 };

describe('derive', () => {
	it('S1: no reviews', () => expect(derive({ list: okList([]), row: null, feedback: null }, none).states).toEqual(['S1']));
	it('S4: running', () => {
		const a = sum(3, 'running');
		expect(derive({ list: okList([a], a), row: row(a), feedback: null }, none).states).toContain('S4');
	});
	it('S5 vs S10: list failure with and without a remembered active run', () => {
		expect(derive({ list: bad(), row: null, feedback: null }, none).states).toEqual(['S10']);
		expect(derive({ list: bad(), row: null, feedback: null }, { lastActive: sum(3, 'running'), pollFailures: 2 }).states).toEqual(['S5']);
	});
	it('S6: viewing a failed run', () => {
		const f = sum(5, 'failed', { error: 'x' });
		expect(derive({ list: okList([f]), row: row(f), feedback: null }, none).states).toEqual(['S6']);
	});
	it('S7: newest failed but an older ok run is shown', () => {
		const f = sum(6, 'failed');
		const ok = sum(5, 'ok');
		const d = derive({ list: okList([f, ok]), row: row(ok, {}), feedback: { ok: true, value: {} } }, none);
		expect(d.states).toEqual(['S7']);
		expect(d.newest?.id).toBe(6);
	});
	it('does not raise S7 when the failed run itself is open', () => {
		const f = sum(6, 'failed');
		expect(derive({ list: okList([f, sum(5, 'ok')]), row: row(f), feedback: null }, none).states).toEqual(['S6']);
	});
	it('S11: document failed to load, bar data still derived', () => {
		const ok = sum(5, 'ok');
		const d = derive({ list: okList([ok]), row: bad(), feedback: null }, none);
		expect(d.states).toEqual(['S11']);
		expect(d.documents).toHaveLength(1);
	});
	it('S12: feedback failed to load', () => {
		const ok = sum(5, 'ok');
		expect(derive({ list: okList([ok]), row: row(ok, {}), feedback: bad() }, none).states).toEqual(['S12']);
	});
	it('S13: degraded content', () => {
		const ok = sum(5, 'ok');
		const doc = { charts: { curve: { status: 'unavailable', reason: 'r' }, roles: [] }, forge: { status: 'unavailable' }, omitted: [{}] };
		expect(derive({ list: okList([ok]), row: row(ok, doc), feedback: { ok: true, value: {} } }, none).states).toEqual(['S13']);
	});
	it('list failure never throws and yields empty documents', () => {
		const d = derive({ list: bad(), row: null, feedback: null }, none);
		expect(d.documents).toEqual([]);
		expect(d.active).toBeNull();
	});
});

describe('partialSections', () => {
	it('lists unavailable charts, forge and omitted count', () => {
		const p = partialSections({ charts: { curve: { status: 'unavailable', reason: 'r' }, pips_vs_sources: [], roles: [], synergy_clusters: { status: 'unavailable', reason: 'r' } }, forge: { status: 'unavailable' }, omitted: [1, 2] });
		expect(p.sections.map((s) => s.label)).toEqual(['Mana curve', 'Synergy clusters', 'Game evidence']);
		expect(p.omitted).toBe(2);
	});
	it('is empty for a complete doc and for null', () => {
		expect(partialSections({ charts: { curve: {} }, forge: { status: 'available' }, omitted: [] }).sections).toEqual([]);
		expect(partialSections(null)).toEqual({ sections: [], omitted: 0 });
	});
});

describe('transition (S8/S9)', () => {
	const a = sum(7, 'running');
	it('reports failed and ready once the run leaves active', () => {
		expect(transition(a, okList([sum(7, 'failed')]))).toEqual({ kind: 'failed', id: 7 });
		expect(transition(a, okList([sum(7, 'ok')]))).toEqual({ kind: 'ready', id: 7 });
	});
	it('ignores a still-active run, a failed poll and no previous run', () => {
		expect(transition(a, okList([a], a))).toBeNull();
		expect(transition(a, bad())).toBeNull();
		expect(transition(null, okList([sum(7, 'ok')]))).toBeNull();
	});
});

describe('helpers', () => {
	it('categorises failures by prefix only', () => {
		expect(failureHeadline('The review was interrupted (the server restarted before it finished). Generate a new review.').headline).toBe('The review was interrupted');
		expect(failureHeadline('LLM timeout').headline).toBe("The review couldn't finish");
		expect(failureHeadline(null).headline).toBe("The review couldn't finish");
	});
	it('backs off 2s, 5s, 10s and caps retries at five', () => {
		expect([0, 1, 2, 4].map(pollDelay)).toEqual([2000, 5000, 10000, 10000]);
		expect(MAX_POLL_FAILURES).toBe(5);
	});
	it('leads failed history entries with the status word', () => {
		const w = (s: string | null) => s ?? '';
		expect(historyLabel(sum(12, 'failed', { created_at: '2026-10-29T14:02:00Z' }), (s) => w(s).slice(0, 10))).toBe('#12 · Failed · 2026-10-29');
		expect(historyLabel(sum(3, 'ok', { grade: 'B' }), (s) => w(s).slice(0, 10))).toBe('#3 · 2026-10-29 · grade B');
	});
});
