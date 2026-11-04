import { describe, expect, it } from 'vitest';
import { groupByTier, inListByOracle, isCommander } from './brew.ts';

const L = (name: string, tier: number, quantity = 1, oracle_id: string | null = `o-${name}`) => ({ name, tier, tier_label: '', quantity, oracle_id });

describe('brew helpers', () => {
	it('groups by tier in order, sorted by name, dropping empty tiers', () => {
		const g = groupByTier([L('Zed', 3), L('Bolt', 1), L('Axe', 3), L('Sol Ring', 2)]);
		expect(g.map((x) => x.tier)).toEqual([1, 2, 3]);
		expect(g[2].lines.map((l) => l.name)).toEqual(['Axe', 'Zed']);
		expect(groupByTier([L('Axe', 3)]).map((x) => x.tier)).toEqual([3]);
	});
	it('keeps an unknown tier visible instead of dropping the card', () => {
		expect(groupByTier([L('X', 9)])[0].title).toBe('Other');
	});
	it('sums quantities for inList and skips lines without an oracle id', () => {
		const m = inListByOracle([L('Forest', 3, 4), L('Forest', 3, 2), L('Old', 1, 1, null)]);
		expect(m.get('o-Forest')).toBe(6);
		expect(m.size).toBe(1);
	});
	it('recognises the commander case-insensitively', () => {
		expect(isCommander(L('Atraxa', 1), ['atraxa'])).toBe(true);
		expect(isCommander(L('Sol Ring', 2), ['Atraxa'])).toBe(false);
	});
});
