import { describe, expect, it } from 'vitest';
import { BOARD_ORDER, boardLabel, groupByBoard, nextQty, stagedQty, stagedQtyByOracle, toApiBoard } from './boards';

describe('toApiBoard', () => {
	it('round-trips all 5 boards', () => {
		for (const b of BOARD_ORDER) expect(toApiBoard(b)).toBe(b);
	});
	it('maps maybeboard to maybe, case/space tolerant', () => {
		expect(toApiBoard('maybeboard')).toBe('maybe');
		expect(toApiBoard(' Main ')).toBe('main');
	});
	it('returns null for garbage', () => {
		expect(toApiBoard('nope')).toBeNull();
		expect(toApiBoard('')).toBeNull();
	});
});

describe('boardLabel', () => {
	it('labels known and unknown boards', () => {
		expect(boardLabel('maybe')).toBe('Maybeboard');
		expect(boardLabel('maybeboard')).toBe('Maybeboard');
		expect(boardLabel('foo')).toBe('Foo');
		expect(boardLabel('')).toBe('Unknown');
	});
});

describe('qty helpers', () => {
	const cards = [
		{ oracle_id: 'a', board: 'main', qty: 2 },
		{ oracle_id: 'a', board: 'maybe', qty: 1 },
		{ oracle_id: 'b', board: 'main', qty: 4 }
	];
	it('nextQty adds existing + requested', () => {
		expect(nextQty(2, 1)).toBe(3);
		expect(nextQty(0, 0)).toBe(1);
		expect(nextQty(-3, 2)).toBe(2);
	});
	it('stagedQty finds by board incl. maybeboard alias', () => {
		expect(stagedQty(cards, 'a', 'main')).toBe(2);
		expect(stagedQty(cards, 'a', 'maybeboard')).toBe(1);
		expect(stagedQty(cards, 'b', 'sideboard')).toBe(0);
	});
	it('stagedQtyByOracle totals across boards', () => {
		const m = stagedQtyByOracle(cards);
		expect(m.get('a')).toBe(3);
		expect(m.get('b')).toBe(4);
	});
});

describe('groupByBoard', () => {
	it('orders boards, omits empty, unknown last', () => {
		const rows = [
			{ board: 'weird', qty: 1 },
			{ board: 'considering', qty: 1 },
			{ board: 'main', qty: 2 },
			{ board: 'main', qty: 3 },
			{ board: 'commander', qty: 1 }
		];
		const g = groupByBoard(rows);
		expect(g.map((x) => x.board)).toEqual(['commander', 'main', 'considering', 'weird']);
		expect(g[1].qty).toBe(5);
		expect(g[1].label).toBe('Main');
	});
	it('returns [] for no cards', () => {
		expect(groupByBoard([])).toEqual([]);
	});
});
