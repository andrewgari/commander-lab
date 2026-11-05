import { describe, expect, it } from 'vitest';
import {
	abandonSentence,
	deckName,
	donorSentence,
	moveRows,
	summarize,
	type SummaryCard,
	type SummaryInput,
	type SummaryMove
} from './summary';

const card = (name: string, board: string, qty = 1, ci: string[] = []): SummaryCard => ({
	oracle_id: `o-${name}`,
	name,
	qty,
	board,
	color_identity: ci
});
const move = (over: Partial<SummaryMove> = {}): SummaryMove => ({
	instance_id: 'i1',
	action: 'assign',
	from_deck: null,
	oracle_id: 'o-sol',
	card_name: 'Sol Ring',
	status: 'in_collection',
	deck_id: null,
	...over
});
const input = (cards: SummaryCard[], moves: SummaryMove[] = []): SummaryInput => ({
	draft: { target_name: 'Elves' },
	cards,
	moves
});
const names = new Map([['d1', 'Sliver Swarm'], ['d2', 'Goblins']]);

describe('summarize cards', () => {
	it('counts boards in display order and omits empty ones', () => {
		const s = summarize(input([card('M', 'maybe', 4), card('A', 'main', 99), card('C', 'commander')]), names);
		expect(s.boardCounts.map((b) => [b.board, b.qty])).toEqual([['commander', 1], ['main', 99], ['maybe', 4]]);
		expect(s.totalCards).toBe(104);
		expect(s.cardsLine).toBe('104 cards: 1 commander · 99 main · 4 maybe');
		expect(s.newDeckName).toBe('Elves');
	});
	it('lists commanders and unions identity in WUBRG order', () => {
		const s = summarize(input([card('Atraxa', 'commander', 1, ['G', 'W', 'U']), card('X', 'main', 1, ['B', 'W'])]));
		expect(s.commanders).toEqual(['Atraxa']);
		expect(s.identity).toEqual(['W', 'U', 'B', 'G']);
	});
	it('colourless draft has empty identity', () => {
		expect(summarize(input([card('Sol Ring', 'main')])).identity).toEqual([]);
	});
});

describe('warnings', () => {
	it('warns when no commander and not 100 cards', () => {
		const s = summarize(input([card('A', 'main', 60)]));
		expect(s.warnings).toContain('No commander staged.');
		expect(s.warnings).toContain('Commander decks have 100 cards; this one will have 60.');
	});
	it('no size warning at exactly 100 commander+main', () => {
		const s = summarize(input([card('C', 'commander'), card('A', 'main', 99)]));
		expect(s.warnings).toEqual([]);
	});
	it('warns above 100 too, and ignores sideboard/maybe in the count', () => {
		const s = summarize(input([card('C', 'commander'), card('A', 'main', 100), card('M', 'maybe', 5)]));
		expect(s.warnings).toEqual(['Commander decks have 100 cards; this one will have 101.']);
		const ok = summarize(input([card('C', 'commander'), card('A', 'main', 99), card('M', 'maybe', 5)]));
		expect(ok.warnings).toEqual([]);
	});
	it('warns about donor decks by name', () => {
		const s = summarize(
			input([card('C', 'commander'), card('A', 'main', 99)], [move({ status: 'in_deck', deck_id: 'd1' })]),
			names
		);
		expect(s.warnings).toEqual(['Sliver Swarm will lose these copies: Sol Ring.']);
	});
});

describe('moves and status', () => {
	it('groups donors by deck name, biggest first', () => {
		const s = summarize(
			input(
				[card('A', 'main')],
				[
					move({ instance_id: '1', status: 'in_deck', deck_id: 'd2', card_name: 'Z' }),
					move({ instance_id: '2', status: 'in_deck', deck_id: 'd1', card_name: 'B' }),
					move({ instance_id: '3', status: 'in_deck', deck_id: 'd1', card_name: 'A' }),
					move({ instance_id: '4', status: 'in_collection' }),
					move({ instance_id: '5', action: 'unassign', status: 'in_deck', deck_id: 'd2' })
				]
			),
			names
		);
		expect(s.donors.map((d) => [d.deckName, d.count, d.cards])).toEqual([
			['Sliver Swarm', 2, ['A', 'B']],
			['Goblins', 1, ['Z']]
		]);
		expect(s.donorTotal).toBe(3);
		expect(s.assignCount).toBe(4);
		expect(s.unassignCount).toBe(1);
		expect(s.movesLine).toBe('4 copies move into the new deck · 1 returns to the collection');
		expect(donorSentence(s.donors)).toBe('Pulls 2 copies out of Sliver Swarm, 1 out of Goblins');
	});
	it('has no donors or moves line without moves', () => {
		const s = summarize(input([card('A', 'main')]));
		expect(s.movesLine).toBeNull();
		expect(donorSentence(s.donors)).toBeNull();
	});
	it('expected status is have with any assign, else virtual', () => {
		expect(summarize(input([card('A', 'main')], [move()])).expectedStatus).toBe('have');
		const v = summarize(input([card('A', 'main')], [move({ action: 'unassign' })]));
		expect(v.expectedStatus).toBe('virtual');
		expect(v.expectedStatusLabel).toBe('Virtual');
		expect(summarize(input([card('A', 'main')])).expectedStatus).toBe('virtual');
	});
});

describe('empty draft', () => {
	it('is blocked with a reason and no warnings', () => {
		const s = summarize(input([]));
		expect(s.blockedReason).toBe('Stage at least one card to commit.');
		expect(s.cardsLine).toBe('0 cards');
		expect(s.warnings).toEqual([]);
	});
	it('is not blocked once a card is staged', () => {
		expect(summarize(input([card('A', 'main')])).blockedReason).toBeNull();
	});
});

describe('deckName', () => {
	it('returns the mapped name', () => expect(deckName('d1', names)).toBe('Sliver Swarm'));
	it('never echoes the id when the map is null, missing the entry, or the id is empty', () => {
		expect(deckName('abc-123', null)).toBe('another deck');
		expect(deckName('abc-123', names)).toBe('another deck');
		expect(deckName(null, names)).toBe('another deck');
		expect(deckName('', names)).toBe('another deck');
	});
	it('summary output never contains an unknown deck id', () => {
		const s = summarize(input([card('A', 'main')], [move({ status: 'in_deck', deck_id: 'secret-id' })]), null);
		expect(JSON.stringify(s)).toContain('another deck');
		expect(s.warnings.join()).not.toContain('secret-id');
		expect(s.donors[0].deckName).toBe('another deck');
	});
});

describe('abandonSentence', () => {
	it('sums qty and counts moves', () => {
		expect(abandonSentence([{ qty: 3 }, { qty: 2 }], [1, 2])).toBe(
			'Deletes 5 staged cards and 2 staged moves. Your decks and collection are untouched.'
		);
	});
	it('singularises', () => {
		expect(abandonSentence([{ qty: 1 }], [1])).toBe(
			'Deletes 1 staged card and 1 staged move. Your decks and collection are untouched.'
		);
	});
});

describe('moveRows', () => {
	it('labels, sources and states without ids', () => {
		const rows = moveRows(
			[
				move({ instance_id: 'a' }),
				move({ instance_id: 'b', oracle_id: 'o2', card_name: 'Birds', action: 'assign', from_deck: 'd1', status: 'in_deck', deck_id: 'd1' }),
				move({ instance_id: 'c', oracle_id: 'o3', card_name: 'Mox', action: 'unassign', status: 'in_mail' }),
				move({ instance_id: 'd', oracle_id: 'o4', card_name: 'Lost', status: 'in_deck', deck_id: 'zzz' })
			],
			names
		);
		expect(rows.map((r) => [r.moveLabel, r.from, r.state])).toEqual([
			['Into new deck', 'Collection', 'In collection'],
			['Into new deck', 'Sliver Swarm', 'In Sliver Swarm'],
			['Back to collection', 'Collection', 'In the mail'],
			['Into new deck', 'Collection', 'In another deck']
		]);
		expect(rows[3].state).not.toContain('zzz');
	});
	it('adds (copy n) only when rows share card, source and action', () => {
		const rows = moveRows(
			[
				move({ instance_id: 'a' }),
				move({ instance_id: 'b' }),
				move({ instance_id: 'c', action: 'unassign' }),
				move({ instance_id: 'd', from_deck: 'd1' })
			],
			names
		);
		expect(rows.map((r) => r.displayName)).toEqual(['Sol Ring (copy 1)', 'Sol Ring (copy 2)', 'Sol Ring', 'Sol Ring']);
	});
});
