import { describe, expect, it } from 'vitest';
import { cardOption, copyOption, copyStatusesFor, nextIndex, statusText } from './model.ts';

describe('nextIndex', () => {
	it('wraps down and up, and jumps with Home/End', () => {
		expect(nextIndex('ArrowDown', -1, 3)).toBe(0);
		expect(nextIndex('ArrowDown', 2, 3)).toBe(0);
		expect(nextIndex('ArrowUp', -1, 3)).toBe(2);
		expect(nextIndex('ArrowUp', 0, 3)).toBe(2);
		expect(nextIndex('Home', 2, 3)).toBe(0);
		expect(nextIndex('End', 0, 3)).toBe(2);
		expect(nextIndex('ArrowDown', 0, 0)).toBe(-1);
	});
});

describe('statusText', () => {
	it('has distinct text per state', () => {
		const t = ['short', 'loading', 'empty', 'error'].map((s) =>
			statusText(s as never, ' sol ', 2)
		);
		expect(new Set(t).size).toBe(4);
		expect(statusText('empty', ' sol ', 2)).toContain('“sol”');
		expect(statusText('results', 'x', 2)).toBeNull();
	});
});

describe('option mapping never exposes ids as text', () => {
	it('card option', () => {
		const o = cardOption({ oracle_id: 'abc-123', name: 'Sol Ring', type_line: 'Artifact', mana_cost: '{1}', owned_qty: 2 });
		expect(o.label).toBe('Sol Ring');
		expect(o.detail).toBe('Artifact · {1} · 2 owned');
		expect(o.detail).not.toContain('abc-123');
		expect(o.value).toEqual({ oracleId: 'abc-123', name: 'Sol Ring' });
	});
	it('copy option', () => {
		const o = copyOption({
			id: 'uuid-9', card_name: 'Sol Ring', set: 'cmd', set_name: 'Commander 2011', collector_number: '5',
			finish: 'foil', condition: 'NM', ownership_status: 'in_deck', deck_name: 'Gruul'
		});
		expect(o.detail).toBe('Commander 2011 #5 · foil · NM · in Gruul');
		expect(`${o.label} ${o.detail}`).not.toContain('uuid-9');
		expect(o.value).toBe('uuid-9');
	});
	it('copy statuses follow the action', () => {
		expect(copyStatusesFor('unassign')).toEqual(['in_deck']);
		expect(copyStatusesFor('assign')).toContain('in_collection');
	});
});
