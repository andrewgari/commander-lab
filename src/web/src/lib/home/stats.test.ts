import { describe, expect, it } from 'vitest';
import type { DeckSummary } from '#lib/api.ts';
import { computeTiles, failedSources, recentDecks, relativeDate, type DraftLite, type GameLite, type HomeSources } from './stats.ts';

const NOW = Date.parse('2026-10-29T12:00:00Z');
const hb = (o: Partial<NonNullable<DeckSummary['have_buy']>> = {}) => ({ need: 100, have: 100, pending: 0, buy: 0, complete: true, have_pct: 100, ...o });
const deck = (id: string, o: Partial<DeckSummary> = {}): DeckSummary => ({
	id, name: id, status: 'have', source: 'archidekt', archidekt_id: null, color_identity: ['G'], commanders: ['X'], card_qty: 100, have_buy: hb(), ...o
});
const game = (id: number, deck_id: string, o: Partial<GameLite> = {}): GameLite => ({ id, deck_id, played_at: null, imported_at: '2026-10-20T00:00:00Z', result: 'win', ...o });
const draft = (id: string, o: Partial<DraftLite> = {}): DraftLite => ({ id, name: id, closed_at: null, created_at: null, result_deck_id: null, ...o });

function sources(o: Partial<HomeSources> = {}): HomeSources {
	return {
		decks: { ok: true, value: [deck('a'), deck('b', { status: 'virtual' })] },
		inventory: { ok: true, value: { cards: 10, owned: 12, in_collection: 5, in_deck: 7, in_mail: 0 } },
		games: { ok: true, value: [game(1, 'a')] },
		drafts: { ok: true, value: [draft('d1', { name: 'Newest' })] },
		committed: { ok: true, value: [] },
		profile: { ok: true, value: { power_level: 7, estimated_bracket: 3, bracket_name: 'Upgraded', snapshot_date: '2026-10-01' } },
		...o
	};
}
const tile = (s: HomeSources, key: string) => computeTiles(s, NOW).find((t) => t.key === key)!;

describe('computeTiles', () => {
	it('returns the six tiles in the fixed order', () => {
		expect(computeTiles(sources(), NOW).map((t) => t.key)).toEqual(['decks', 'ready', 'collection', 'games', 'drafts', 'power']);
	});
	it('counts decks by status and names the tile', () => {
		const t = tile(sources(), 'decks');
		expect(t.value).toBe('2');
		expect(t.chunks.map((c) => c.text)).toEqual(['1 built', '1 virtual']);
		expect(t.name).toBe('Decks: 2, 1 built, 1 virtual');
	});
	it('adds pending/possible only when > 0', () => {
		const s = sources({ decks: { ok: true, value: [deck('a'), deck('p', { status: 'pending' }), deck('q', { status: 'possible' })] } });
		expect(tile(s, 'decks').chunks.map((c) => c.text)).toContain('2 pending/possible');
	});
	it('Ready: complete/built, buy sum across all decks, excludes have_buy null with a footnote', () => {
		const s = sources({
			decks: { ok: true, value: [deck('a'), deck('b', { have_buy: hb({ complete: false, buy: 4 }) }), deck('c', { have_buy: null }), deck('v', { status: 'virtual', have_buy: hb({ buy: 3, complete: false }) })] }
		});
		const t = tile(s, 'ready');
		expect(t.value).toBe('1');
		expect(t.denominator).toBe('/3');
		expect(t.chunks.map((c) => c.text)).toEqual(['7 cards to buy', '1 deck not computed']);
	});
	it('Ready is empty when no deck is built', () => {
		const s = sources({ decks: { ok: true, value: [deck('v', { status: 'virtual' })] } });
		expect(tile(s, 'ready').state).toBe('empty');
	});
	it('no decks: T1 and T2 are empty with an import CTA', () => {
		const s = sources({ decks: { ok: true, value: [] } });
		for (const k of ['decks', 'ready']) {
			expect(tile(s, k).state).toBe('empty');
			expect(tile(s, k).emptyText).toBe('No decks yet');
		}
	});
	it('collection reads owned copies and drops the mail part at 0', () => {
		expect(tile(sources(), 'collection').chunks.map((c) => c.text)).toEqual(['10 cards']);
		const s = sources({ inventory: { ok: true, value: { cards: 1, owned: 2, in_collection: 0, in_deck: 0, in_mail: 3 } } });
		expect(tile(s, 'collection').chunks.map((c) => c.text)).toEqual(['1 card', '3 in the mail']);
	});
	it('collection empty at 0 cards', () => {
		const s = sources({ inventory: { ok: true, value: { cards: 0, owned: 0, in_collection: 0, in_deck: 0, in_mail: 0 } } });
		expect(tile(s, 'collection').emptyText).toBe('No cards yet');
	});
	it('games: W-L excludes null results', () => {
		const s = sources({ games: { ok: true, value: [game(1, 'a'), game(2, 'a', { result: 'loss' }), game(3, 'a', { result: null }), game(4, 'a', { result: 'draw' })] } });
		const t = tile(s, 'games');
		expect(t.value).toBe('4');
		expect(t.chunks[0].text).toBe('1–1');
	});
	it('games: exactly 200 shows 200+ with a title', () => {
		const many = Array.from({ length: 200 }, (_, i) => game(i, 'a'));
		const t = tile(sources({ games: { ok: true, value: many } }), 'games');
		expect(t.value).toBe('200+');
		expect(t.valueTitle).toBe('Showing the most recent 200 games');
		expect(tile(sources({ games: { ok: true, value: many.slice(0, 199) } }), 'games').value).toBe('199');
	});
	it('games: last date is the newest played_at ?? imported_at', () => {
		const s = sources({ games: { ok: true, value: [game(1, 'a', { played_at: '2026-10-27T00:00:00Z' }), game(2, 'a', { imported_at: '2026-10-01T00:00:00Z' })] } });
		const last = tile(s, 'games').chunks[1];
		expect(last.text).toBe('last 2 d ago');
		expect(last.title).toBe('2026-10-27');
	});
	it('games empty links to the import disclosure', () => {
		const t = tile(sources({ games: { ok: true, value: [] } }), 'games');
		expect(t.state).toBe('empty');
		expect(t.emptyHref).toBe('/games?import=1');
	});
	it('drafts: two separate targets, newest name as the sublink', () => {
		const t = tile(sources(), 'drafts');
		expect(t.href).toBe('/drafts');
		expect(t.sublink).toEqual({ href: '/drafts/d1', text: 'Newest' });
		expect(tile(sources({ drafts: { ok: true, value: [] } }), 'drafts').emptyText).toBe('None open');
	});
	it('power: value, bracket and snapshot; empty when null', () => {
		const t = tile(sources(), 'power');
		expect(t.value).toBe('7');
		expect(t.denominator).toBe('/10');
		expect(t.chunks.map((c) => c.text)).toEqual(['Bracket 3 (Upgraded)', 'snapshot 2026-10-01']);
		expect(tile(sources({ profile: { ok: true, value: null } }), 'power').state).toBe('empty');
	});
	it.each([
		['decks', ['decks', 'ready']],
		['inventory', ['collection']],
		['games', ['games']],
		['drafts', ['drafts']],
		['profile', ['power']]
	] as const)('a failed %s source errors only its tiles', (src, errored) => {
		const t = computeTiles(sources({ [src]: { ok: false, reason: 'HTTP 503' } } as Partial<HomeSources>), NOW);
		expect(t.filter((x) => x.state === 'error').map((x) => x.key)).toEqual(errored);
		expect(t.find((x) => x.state === 'error')!.errorReason).toBe('HTTP 503');
		expect(t.filter((x) => x.state === 'error').every((x) => x.value === '')).toBe(true);
	});
	it('failedSources lists reasons', () => {
		expect(failedSources(sources({ games: { ok: false, reason: 'x' } }))).toEqual([{ key: 'games', reason: 'x' }]);
	});
});

describe('recentDecks', () => {
	const decks = [deck('a'), deck('b'), deck('c'), deck('d'), deck('e'), deck('f'), deck('z-built', { name: 'Zed' })];
	it('lists ≤5 distinct decks in newest-game order', () => {
		const games = [
			game(1, 'c', { played_at: '2026-10-20T00:00:00Z' }),
			game(2, 'a', { played_at: '2026-10-28T00:00:00Z' }),
			game(3, 'c', { played_at: '2026-10-29T00:00:00Z' }),
			...['b', 'd', 'e', 'f'].map((id, i) => game(10 + i, id, { played_at: `2026-10-1${i}T00:00:00Z` }))
		];
		const r = recentDecks(decks, games, [], NOW);
		expect(r.heading).toBe('Recently played');
		expect(r.rows.map((x) => x.deck.id)).toEqual(['c', 'a', 'f', 'e', 'd']);
		expect(r.rows[0].reason?.label).toBe('played');
	});
	it('skips games whose deck is not in the list', () => {
		expect(recentDecks(decks, [game(1, 'gone'), game(2, 'a')], [], NOW).rows.map((x) => x.deck.id)).toEqual(['a']);
	});
	it('fills from committed drafts, not repeating decks', () => {
		const r = recentDecks(decks, [game(1, 'a')], [draft('d1', { result_deck_id: 'a', closed_at: '2026-10-28T00:00:00Z' }), draft('d2', { result_deck_id: 'b', closed_at: '2026-10-27T00:00:00Z' })], NOW);
		expect(r.rows.map((x) => [x.deck.id, x.reason?.label])).toEqual([['a', 'played'], ['b', 'built from draft']]);
	});
	it('with no games, uses draft builds', () => {
		const r = recentDecks(decks, [], [draft('d', { result_deck_id: 'c', closed_at: '2026-10-28T00:00:00Z' })], NOW);
		expect(r.heading).toBe('Recently played');
		expect(r.rows[0].deck.id).toBe('c');
	});
	it('with neither, is "Your decks": first 5 by name, no reason', () => {
		const r = recentDecks([deck('b', { name: 'Beta' }), deck('a', { name: 'Alpha' })], [], [], NOW);
		expect(r.heading).toBe('Your decks');
		expect(r.rows.map((x) => x.deck.name)).toEqual(['Alpha', 'Beta']);
		expect(r.rows[0].reason).toBeNull();
		expect(recentDecks(decks, [], [], NOW).rows).toHaveLength(5);
	});
});

describe('relativeDate', () => {
	it('is relative within 60 days and absolute after, always with the absolute date as title', () => {
		expect(relativeDate('2026-10-26T12:00:00Z', NOW)?.text).toBe('3 d ago');
		expect(relativeDate('2026-06-01T12:00:00Z', NOW)?.text).toBe('2026-06-01');
		expect(relativeDate('2026-10-26T12:00:00Z', NOW)?.title).toBe('2026-10-26');
		expect(relativeDate(null, NOW)).toBeNull();
	});
});
