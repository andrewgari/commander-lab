// Pure helpers behind QuickAdd's combobox (page-specs §2.4): rows model, highlight movement,
// match emphasis, owned-search URL. No Svelte or DOM imports, so it runs under `node --test`.

import { searchUrl, QUICK_ADD } from './query.ts';
import type { CardHit } from './types.ts';

/** Quick-add suggestions that can only ever offer cards the user owns (draft staging, gap G3). */
export function ownedSuggestUrl(term: string, limit: number = QUICK_ADD.limit): string {
	return searchUrl({ name: term.trim(), owned: 'owned' }, { page_size: limit });
}

export type SearchPhase = 'idle' | 'loading' | 'ready' | 'error';

export type ComboRow =
	| { kind: 'degraded'; key: string }
	| { kind: 'searching'; key: string }
	| { kind: 'empty'; key: string; term: string }
	| { kind: 'error'; key: string; detail: string }
	| { kind: 'card'; key: string; hit: CardHit };

export interface RowsInput {
	phase: SearchPhase;
	term: string;
	results: readonly CardHit[];
	degraded: boolean;
	/** Show "Searching…" (only set after the 300 ms flicker guard). */
	showSearching: boolean;
	errorDetail: string;
}

/**
 * The four non-result rows are distinct: an error never renders as "no matches".
 * Stale results stay visible while the next request is in flight.
 */
export function buildRows(i: RowsInput): ComboRow[] {
	if (i.phase === 'error') return [{ kind: 'error', key: 'error', detail: i.errorDetail }];
	const rows: ComboRow[] = [];
	if (i.phase === 'ready' && i.degraded) rows.push({ kind: 'degraded', key: 'degraded' });
	if (i.results.length > 0) {
		for (const hit of i.results) rows.push({ kind: 'card', key: hit.oracle_id, hit });
		return rows;
	}
	if (i.phase === 'loading') return i.showSearching ? [{ kind: 'searching', key: 'searching' }] : [];
	if (i.phase === 'ready') rows.push({ kind: 'empty', key: 'empty', term: i.term });
	return rows;
}

/** Rows the keyboard can land on: cards, plus the retry row on error. */
export function navigableIndexes(rows: readonly ComboRow[]): number[] {
	const out: number[] = [];
	rows.forEach((r, idx) => {
		if (r.kind === 'card' || r.kind === 'error') out.push(idx);
	});
	return out;
}

/** Move the highlight (wraps). `current` is a row index or -1; the first ↓ from -1 lands on the first row. */
export function moveHighlight(rows: readonly ComboRow[], current: number, dir: 1 | -1): number {
	const nav = navigableIndexes(rows);
	if (nav.length === 0) return -1;
	const at = nav.indexOf(current);
	if (at === -1) return dir === 1 ? nav[0] : nav[nav.length - 1];
	return nav[(at + dir + nav.length) % nav.length];
}

/** Row the Enter key acts on: the highlighted one, else the top navigable row. */
export function enterTarget(rows: readonly ComboRow[], highlighted: number): ComboRow | null {
	const nav = navigableIndexes(rows);
	if (nav.length === 0) return null;
	return rows[nav.includes(highlighted) ? highlighted : nav[0]] ?? null;
}

export interface Segment {
	text: string;
	match: boolean;
	/** The " // " face separator of a split or double-faced card. */
	sep?: boolean;
}

/** Split a name into segments: the first case-insensitive occurrence of `term` is emphasised. */
export function nameSegments(name: string, term: string): Segment[] {
	const out: Segment[] = [];
	const push = (text: string, match: boolean) => {
		if (!text) return;
		const faces = text.split(' // ');
		faces.forEach((face, idx) => {
			if (idx > 0) out.push({ text: '//', match: false, sep: true });
			if (face) out.push({ text: face, match });
		});
	};
	const t = term.trim();
	const at = t ? name.toLowerCase().indexOf(t.toLowerCase()) : -1;
	if (at < 0) {
		push(name, false);
		return out;
	}
	push(name.slice(0, at), false);
	push(name.slice(at, at + t.length), true);
	push(name.slice(at + t.length), false);
	return out;
}

/** `Own 4 · 1 free` */
export function ownedLabel(hit: Pick<CardHit, 'owned_qty' | 'free_qty'>): string {
	return `Own ${hit.owned_qty} · ${hit.free_qty} free`;
}

/** Text of the settled-request announcement ("5 owned matches"). */
export function countAnnouncement(n: number, scope: string = 'owned'): string {
	return n === 0 ? `No ${scope} matches` : `${n} ${scope} ${n === 1 ? 'match' : 'matches'}`;
}
