import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import StatTile from './StatTile.svelte';
import DeckRow from './DeckRow.svelte';
import ManaPips from '../ManaPips.svelte';
import type { DeckSummary } from '#lib/api.ts';
import { computeTiles, type HomeSources } from '#lib/home/stats.ts';
import { expectNoAxeViolations } from '#lib/test/axe.ts';

const S: HomeSources = {
	decks: { ok: true, value: [{ id: 'a', name: 'Atraxa', status: 'have', source: 'x', archidekt_id: null, color_identity: ['W', 'U', 'B', 'G'], commanders: ['Atraxa'], card_qty: 100, have_buy: { need: 100, have: 96, pending: 0, buy: 4, complete: false, have_pct: 96 } }] },
	inventory: { ok: true, value: { cards: 1, owned: 1, in_collection: 1, in_deck: 0, in_mail: 0 } },
	games: { ok: true, value: [] },
	drafts: { ok: true, value: [{ id: 'd1', name: 'Newest', closed_at: null, created_at: null, result_deck_id: null }] },
	committed: { ok: true, value: [] },
	profile: { ok: false, reason: 'HTTP 503' }
};
const tiles = computeTiles(S, Date.now());
const ul = (t: (typeof tiles)[number]) => ({ tile: t });

describe('StatTile', () => {
	it('ok tile is one link with the full accessible name', () => {
		render(StatTile, ul(tiles[0]));
		expect(screen.getByRole('link', { name: /^Decks:\s*1,\s*1 built.*0 virtual$/ }).getAttribute('href')).toBe('/decks');
	});
	it('drafts tile renders two sibling links, never nested', () => {
		const { container } = render(StatTile, ul(tiles[4]));
		const links = container.querySelectorAll('a');
		expect(links).toHaveLength(2);
		expect([...links].some((a) => a.querySelector('a'))).toBe(false);
		expect([...links].map((a) => a.getAttribute('href'))).toEqual(['/drafts', '/drafts/d1']);
	});
	it('error tile never shows a numeral and carries the reason as title', () => {
		const { container } = render(StatTile, ul(tiles[5]));
		expect(container.textContent).toContain("Couldn't load");
		expect(container.querySelector('a')?.getAttribute('title')).toBe('HTTP 503');
		expect(container.textContent).not.toMatch(/\d/);
	});
	it('empty tile links to its CTA target', () => {
		render(StatTile, ul(tiles[3]));
		expect(screen.getByRole('link').getAttribute('href')).toBe('/games?import=1');
	});
	it('has no axe violations (ok, empty, error)', async () => {
		for (const t of [tiles[0], tiles[3], tiles[4], tiles[5]]) {
			const target = document.createElement('ul');
			document.body.appendChild(target);
			render(StatTile, { target, props: ul(t) });
			await expectNoAxeViolations(target);
		}
	});
});

describe('DeckRow', () => {
	const d = (S.decks as { ok: true; value: DeckSummary[] }).value[0];
	it('shows name link, commanders, pills, Buy n and an accessible Review link', () => {
		render(DeckRow, { deck: d, reason: { label: 'played', when: { text: '3 d ago', datetime: '2026-10-26', title: '2026-10-26' } } });
		expect(screen.getByRole('link', { name: 'Atraxa' }).getAttribute('href')).toBe('/decks/a');
		expect(screen.getByText('Buy 4')).toBeTruthy();
		expect(screen.getByText('96/100')).toBeTruthy();
		expect(screen.getByRole('link', { name: 'Review Atraxa' }).getAttribute('href')).toBe('/decks/a/review');
		expect(screen.getByRole('img', { name: 'Colour identity: White, Blue, Black, Green' })).toBeTruthy();
	});
	it('have_buy null reads Not computed; complete reads Complete', () => {
		render(DeckRow, { deck: { ...d, have_buy: null }, reason: null });
		expect(screen.getByText('Not computed')).toBeTruthy();
		render(DeckRow, { deck: { ...d, have_buy: { need: 1, have: 1, pending: 0, buy: 0, complete: true, have_pct: 100 } }, reason: null });
		expect(screen.getByText('Complete')).toBeTruthy();
	});
});

describe('ManaPips', () => {
	it('colourless shows a single C pip', () => {
		const { container } = render(ManaPips, { identity: [] });
		expect(container.textContent).toBe('C');
		expect(screen.getByRole('img').getAttribute('aria-label')).toBe('Colour identity: Colorless');
	});
	it('orders WUBRG regardless of input order', () => {
		const { container } = render(ManaPips, { identity: ['G', 'W'] });
		expect(container.textContent).toBe('WG');
	});
});
