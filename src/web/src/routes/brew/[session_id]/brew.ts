// Pure helpers for the brew construct page (docs/ui/card-search.md §6).
import type { BrewLine } from './+page.server.ts';

export interface TierGroup {
	tier: number;
	title: string;
	chip: string;
	count: number;
	lines: BrewLine[];
}

// tier 1 = named donor decks, 2 = free inventory, 3 = to buy (domain/draft/reconstruct_pools.py TIER_LABELS).
const TIERS: Record<number, { title: string; chip: string }> = {
	1: { title: 'Owned — donors', chip: 'owned · donor' },
	2: { title: 'Owned — inventory', chip: 'owned · inventory' },
	3: { title: 'Buy', chip: 'buy' }
};

/** Group by tier (the draft lines carry no type line), sorted by name inside a group; empty tiers are dropped. */
export function groupByTier(cards: readonly BrewLine[]): TierGroup[] {
	const out: TierGroup[] = [];
	for (const tier of [1, 2, 3]) {
		const lines = cards.filter((c) => c.tier === tier).sort((a, b) => a.name.localeCompare(b.name));
		if (lines.length) out.push({ tier, ...TIERS[tier], count: lines.reduce((n, c) => n + c.quantity, 0), lines });
	}
	const other = cards.filter((c) => !TIERS[c.tier]);
	if (other.length) out.push({ tier: 0, title: 'Other', chip: 'unknown', count: other.reduce((n, c) => n + c.quantity, 0), lines: other });
	return out;
}

/** oracle_id -> qty for `inList`; lines without an oracle id (builder-written) cannot be matched and are skipped. */
export function inListByOracle(cards: readonly BrewLine[]): Map<string, number> {
	const m = new Map<string, number>();
	for (const c of cards) if (c.oracle_id) m.set(c.oracle_id, (m.get(c.oracle_id) ?? 0) + c.quantity);
	return m;
}

export function isCommander(line: BrewLine, commanders: readonly string[]): boolean {
	return commanders.some((n) => n.toLowerCase() === line.name.toLowerCase());
}
