// Pure state detection for the review page (page spec §5.4). No DOM, no network.
import type { ReviewDocumentList, ReviewDocumentResponse, ReviewDocumentSummary, SwapFeedback } from '#lib/api.ts';

export type Part<T> = { ok: true; value: T } | { ok: false; reason: string; status?: number };

export interface ReviewData {
	list: Part<ReviewDocumentList>;
	row: Part<ReviewDocumentResponse> | null;
	feedback: Part<Record<string, SwapFeedback>> | null;
}

export type StateId = 'S1' | 'S6' | 'S7' | 'S10' | 'S11' | 'S12' | 'S13' | 'S4' | 'S5';

export interface Ctx {
	/** The active (queued/running) summary the client last saw, kept across failed polls. */
	lastActive: ReviewDocumentSummary | null;
	/** Consecutive failed polls while `lastActive` is set. */
	pollFailures: number;
}

export interface Derived {
	states: StateId[];
	active: ReviewDocumentSummary | null;
	newest: ReviewDocumentSummary | null;
	viewing: ReviewDocumentResponse | null;
	documents: ReviewDocumentSummary[];
}

export const MAX_POLL_FAILURES = 5;

export function derive(data: ReviewData, ctx: Ctx): Derived {
	const states: StateId[] = [];
	const documents = data.list.ok ? data.list.value.documents : [];
	const active = data.list.ok ? data.list.value.active : null;
	const newest = documents[0] ?? null;
	const viewing = data.row?.ok ? data.row.value : null;

	if (!data.list.ok) {
		if (ctx.lastActive) states.push('S5');
		else states.push('S10');
	} else {
		if (documents.length === 0 && !active) states.push('S1');
		if (active) states.push('S4');
	}
	if (data.row && !data.row.ok) states.push('S11');
	if (viewing?.status === 'failed') states.push('S6');
	if (data.list.ok && newest?.status === 'failed' && viewing && viewing.id !== newest.id && viewing.status === 'ok') states.push('S7');
	if (data.feedback && !data.feedback.ok) states.push('S12');
	if (viewing?.status === 'ok' && partialSections(viewing.doc).sections.length + (partialSections(viewing.doc).omitted ? 1 : 0) > 0) states.push('S13');
	return { states, active, newest, viewing, documents };
}

/** Transition between two list snapshots: what to announce and which doc to switch to (S8/S9). */
export function transition(prevActive: ReviewDocumentSummary | null, list: Part<ReviewDocumentList>): { kind: 'failed' | 'ready'; id: number } | null {
	if (!prevActive || !list.ok || list.value.active?.id === prevActive.id) return null;
	const now = list.value.documents.find((d) => d.id === prevActive.id);
	if (now?.status === 'failed') return { kind: 'failed', id: now.id };
	if (now?.status === 'ok') return { kind: 'ready', id: now.id };
	return null;
}

export interface Partial {
	sections: { key: string; label: string; anchor: string }[];
	omitted: number;
}

const CHARTS: Record<string, { label: string; anchor: string }> = {
	curve: { label: 'Mana curve', anchor: 'sec-numbers' },
	pips_vs_sources: { label: 'Pips vs sources', anchor: 'sec-numbers' },
	roles: { label: 'Role breakdown', anchor: 'sec-numbers' },
	synergy_clusters: { label: 'Synergy clusters', anchor: 'sec-numbers' }
};

export function partialSections(doc: Record<string, unknown> | null | undefined): Partial {
	const out: Partial = { sections: [], omitted: 0 };
	if (!doc) return out;
	const charts = (doc.charts ?? {}) as Record<string, { status?: string } | unknown[]>;
	for (const [key, meta] of Object.entries(CHARTS)) {
		const c = charts[key];
		if (c && !Array.isArray(c) && (c as { status?: string }).status === 'unavailable') out.sections.push({ key, ...meta });
	}
	const forge = doc.forge as { status?: string } | undefined;
	if (forge && forge.status !== 'available') out.sections.push({ key: 'forge', label: 'Game evidence', anchor: 'sec-forge' });
	out.omitted = Array.isArray(doc.omitted) ? doc.omitted.length : 0;
	return out;
}

export function failureHeadline(error: string | null | undefined): { headline: string; body: string } {
	if (error?.startsWith('The review was interrupted')) {
		return { headline: 'The review was interrupted', body: 'The server restarted before it finished.' };
	}
	return { headline: "The review couldn't finish", body: 'Nothing was shown from this run.' };
}

/** Poll delay: 2 s, then 5 s, then 10 s as consecutive failures grow. */
export function pollDelay(failures: number): number {
	return failures <= 0 ? 2000 : failures === 1 ? 5000 : 10000;
}

export const historyLabel = (d: ReviewDocumentSummary, when: (iso: string | null) => string) =>
	d.status === 'ok'
		? `#${d.id} · ${when(d.created_at)} · grade ${d.grade ?? '?'}`
		: d.status === 'failed'
			? `#${d.id} · Failed · ${when(d.created_at)}`
			: `#${d.id} · ${d.status[0].toUpperCase()}${d.status.slice(1)} · ${when(d.created_at)}`;

export const reasonOf = (e: unknown, fallback: string) => (e instanceof Error && e.message ? e.message : fallback);
