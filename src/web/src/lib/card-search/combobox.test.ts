import { describe, expect, it } from 'vitest';
import {
	buildRows,
	completeName,
	countAnnouncement,
	enterTarget,
	moveHighlight,
	nameSegments,
	navigableIndexes,
	ownedLabel,
	ownedSuggestUrl,
	typedQty,
	type ComboRow,
	type RowsInput
} from './combobox.ts';
import type { CardHit } from './types.ts';

const hit = (id: string, name: string): CardHit => ({
	oracle_id: id,
	name,
	mana_cost: '{1}',
	cmc: 1,
	type_line: 'Artifact',
	color_identity: [],
	rarity: 'uncommon',
	oracle_text: '',
	image: name,
	owned_qty: 4,
	free_qty: 1,
	can_be_commander: false,
	pairing: null
});

const base: RowsInput = { phase: 'ready', term: 'sol', results: [], degraded: false, showSearching: false, errorDetail: '' };

describe('ownedSuggestUrl', () => {
	it('is locked to owned with page_size 8', () => {
		const u = new URL(ownedSuggestUrl('  sol ring '), 'http://x');
		expect(u.pathname).toBe('/api/card-search');
		expect(u.searchParams.get('owned')).toBe('owned');
		expect(u.searchParams.get('name')).toBe('sol ring');
		expect(u.searchParams.get('page_size')).toBe('8');
	});
});

describe('qty prefix parsing', () => {
	it.each([
		['Sol Ring', 1],
		['2x Sol Ring', 2],
		['2 Sol Ring', 2],
		['3X Sol Ring', 3],
		['0 Sol Ring', null]
	])('typedQty(%s)', (input, qty) => expect(typedQty(input)).toBe(qty));

	it('completeName keeps a typed quantity', () => {
		expect(completeName('2x sol', 'Sol Ring')).toBe('2x Sol Ring');
		expect(completeName('sol', 'Sol Ring')).toBe('Sol Ring');
	});
});

describe('buildRows', () => {
	it('shows Searching only after the flicker guard', () => {
		expect(buildRows({ ...base, phase: 'loading' })).toEqual([]);
		expect(buildRows({ ...base, phase: 'loading', showSearching: true })[0].kind).toBe('searching');
	});
	it('empty is distinct from error', () => {
		expect(buildRows(base)).toEqual([{ kind: 'empty', key: 'empty', term: 'sol' }]);
		const err = buildRows({ ...base, phase: 'error', errorDetail: '500' });
		expect(err.map((r) => r.kind)).toEqual(['error']);
	});
	it('puts the degraded header before cards', () => {
		const rows = buildRows({ ...base, degraded: true, results: [hit('a', 'Sol Ring')] });
		expect(rows.map((r) => r.kind)).toEqual(['degraded', 'card']);
	});
	it('keeps stale results visible while loading', () => {
		const rows = buildRows({ ...base, phase: 'loading', results: [hit('a', 'Sol Ring')] });
		expect(rows.map((r) => r.kind)).toEqual(['card']);
	});
});

describe('navigation', () => {
	const rows: ComboRow[] = buildRows({ ...base, degraded: true, results: [hit('a', 'A'), hit('b', 'B')] });
	it('skips non-navigable rows', () => expect(navigableIndexes(rows)).toEqual([1, 2]));
	it('wraps both ways', () => {
		expect(moveHighlight(rows, -1, 1)).toBe(1);
		expect(moveHighlight(rows, -1, -1)).toBe(2);
		expect(moveHighlight(rows, 2, 1)).toBe(1);
		expect(moveHighlight(rows, 1, -1)).toBe(2);
	});
	it('returns -1 with nothing navigable', () => expect(moveHighlight(buildRows(base), -1, 1)).toBe(-1));
	it('enterTarget prefers highlight, else top row, else null', () => {
		expect((enterTarget(rows, 2) as { hit: CardHit }).hit.name).toBe('B');
		expect((enterTarget(rows, -1) as { hit: CardHit }).hit.name).toBe('A');
		expect(enterTarget(buildRows(base), -1)).toBeNull();
	});
	it('enterTarget lands on the retry row after an error', () => {
		expect(enterTarget(buildRows({ ...base, phase: 'error' }), -1)?.kind).toBe('error');
	});
});

describe('labels', () => {
	it('nameSegments emphasises the match and marks face separators', () => {
		expect(nameSegments('Sol Ring', 'ring')).toEqual([
			{ text: 'Sol ', match: false },
			{ text: 'Ring', match: true }
		]);
		const segs = nameSegments('Fire // Ice', 'ice');
		expect(segs.some((s) => s.sep)).toBe(true);
		expect(segs.find((s) => s.match)?.text).toBe('Ice');
	});
	it('ownedLabel and countAnnouncement', () => {
		expect(ownedLabel({ owned_qty: 4, free_qty: 1 })).toBe('Own 4 · 1 free');
		expect(countAnnouncement(0)).toBe('No owned matches');
		expect(countAnnouncement(1)).toBe('1 owned match');
		expect(countAnnouncement(5)).toBe('5 owned matches');
	});
});
