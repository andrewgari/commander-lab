import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import CommitSummaryView from './CommitSummary.svelte';
import { summarize, type SummaryCard, type SummaryMove } from '#lib/drafts/summary.ts';

const DECK_A = '11111111-1111-4111-8111-111111111111';
const DECK_B = '22222222-2222-4222-8222-222222222222';
const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

const cards: SummaryCard[] = [
	{ oracle_id: 'o-cmd', name: 'Krenko, Mob Boss', qty: 1, board: 'commander', color_identity: ['R'] },
	{ oracle_id: 'o-main', name: 'Goblin Guide', qty: 90, board: 'main', color_identity: ['R', 'G'] }
];

function move(i: number, deck: string | null, status = 'in_deck'): SummaryMove {
	return { instance_id: `i${i}`, action: 'assign', from_deck: deck, oracle_id: `o${i}`, card_name: `Card ${String(i).padStart(2, '0')}`, status, deck_id: deck };
}

const names = new Map([[DECK_A, 'Sliver Swarm'], [DECK_B, 'Elves']]);
const draft = { target_name: 'Goblin Tribal' };

function build(moves: SummaryMove[], n: Map<string, string> | null = names) {
	return summarize({ draft, cards, moves }, n);
}

describe('CommitSummary', () => {
	it('renders every row', () => {
		const s = build([move(1, DECK_A), move(2, DECK_A), move(3, DECK_B)]);
		const { container } = render(CommitSummaryView, { summary: s });
		const text = container.textContent ?? '';
		expect(container.querySelector('dl.sum')).not.toBeNull();
		expect(container.querySelector('dd.hero')?.textContent).toBe('Creates Goblin Tribal');
		expect(screen.getByText('91 cards: 1 commander · 90 main')).toBeTruthy();
		expect(screen.getByText('Commander')).toBeTruthy();
		expect(screen.getByText('Krenko, Mob Boss')).toBeTruthy();
		expect(screen.getByRole('img', { name: 'Colour identity: Red, Green' })).toBeTruthy();
		expect(screen.getByText('3 copies move into the new deck')).toBeTruthy();
		expect(text).toContain('Pulls 2 copies out of Sliver Swarm, 1 out of Elves');
		const pill = container.querySelector('[data-status]');
		expect(pill?.textContent).toContain('Built (have)');
		expect(screen.getByText('expected')).toBeTruthy();
		expect(text).toContain(s.irreversibleNote);
		const last = container.lastElementChild;
		expect(last?.classList.contains('note')).toBe(true);
	});

	it('lists warnings', () => {
		const s = build([move(1, DECK_A)]);
		const { container } = render(CommitSummaryView, { summary: s });
		const items = [...container.querySelectorAll('ul.warns > li')].map((li) => li.textContent?.trim());
		expect(items).toHaveLength(s.warnings.length);
		expect(items.length).toBeGreaterThanOrEqual(2);
		for (const w of s.warnings) expect(items.some((t) => t?.includes(w))).toBe(true);
	});

	it('shows virtual status and no warnings list when clean', () => {
		const clean = summarize({ draft, cards: [{ ...cards[0] }, { ...cards[1], qty: 99 }], moves: [] }, names);
		const { container } = render(CommitSummaryView, { summary: clean });
		expect(container.querySelector('ul.warns')).toBeNull();
		expect(container.querySelector('[data-status]')?.textContent).toContain('Virtual');
		expect(container.textContent).toContain('No copy moves staged');
		expect(container.textContent).not.toContain('Donor pulls');
	});

	it('keeps up to 6 donor cards inline and collapses more than 6', () => {
		const six = render(CommitSummaryView, { summary: build([1, 2, 3, 4, 5, 6].map((i) => move(i, DECK_A))) });
		expect(six.container.querySelector('details')).toBeNull();
		expect(six.container.querySelectorAll('ul.donors li')).toHaveLength(6);
		six.unmount();

		const { container } = render(CommitSummaryView, { summary: build([1, 2, 3, 4, 5, 6, 7].map((i) => move(i, DECK_A))) });
		const details = container.querySelector('details');
		expect(details).not.toBeNull();
		expect(details?.open).toBe(false);
		expect(details?.querySelector('summary')?.textContent).toBe('Show all 7 cards pulled');
		expect(details?.querySelectorAll('ul.donors li')).toHaveLength(7);
	});

	it('never shows a uuid when deck names are missing', () => {
		const s = build([move(1, DECK_A), move(2, DECK_B)], null);
		const { container } = render(CommitSummaryView, { summary: s, namesMissing: true });
		expect(container.textContent).toContain('Deck names unavailable');
		expect(container.innerHTML).not.toMatch(UUID_RE);
		expect(container.textContent).toContain('another deck');
	});

	it('omits the names note when names are available', () => {
		const { container } = render(CommitSummaryView, { summary: build([move(1, DECK_A)]) });
		expect(container.textContent).not.toContain('Deck names unavailable');
	});
});
