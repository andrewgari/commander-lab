import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/svelte';
import QuickAdd from './QuickAdd.svelte';
import { expectNoAxeViolations } from '#lib/test/axe.ts';
import type { CardHit, SearchResponse } from '#lib/card-search/types.ts';

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
const SOL = hit('sol', 'Sol Ring');
const body = (results: CardHit[], degraded = false): SearchResponse => ({
	results,
	page: 1,
	has_more: false,
	total: results.length,
	query: '',
	source: 'scryfall',
	degraded
});
const json = (b: SearchResponse) => ({ ok: true, status: 200, json: async () => b }) as Response;

const BOARDS = [
	{ value: 'main' as const, label: 'Main' },
	{ value: 'sideboard' as const, label: 'Sideboard' }
];

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
	vi.useFakeTimers();
	fetchMock = vi.fn(async () => json(body([SOL])));
	vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => {
	vi.useRealTimers();
	vi.unstubAllGlobals();
});

function setup(onadd = vi.fn(async () => 'Added Sol Ring'), extra: Record<string, unknown> = {}) {
	const r = render(QuickAdd, { boards: BOARDS, inList: new Map(), onadd, ...extra } as never);
	return { ...r, onadd, input: screen.getByRole('combobox', { name: /Add cards/ }) as HTMLInputElement };
}
async function type(input: HTMLInputElement, v: string) {
	input.focus();
	input.value = v;
	await fireEvent.input(input);
}
const settle = (ms = 250) => vi.advanceTimersByTimeAsync(ms);
const listText = () => document.querySelector('[role=listbox]')?.textContent ?? '';

describe('QuickAdd', () => {
	it('searches owned only, after 200ms and 2+ chars', async () => {
		const { input } = setup();
		await type(input, 's');
		await settle();
		expect(fetchMock).not.toHaveBeenCalled();
		await type(input, 'sol');
		await settle(199);
		expect(fetchMock).not.toHaveBeenCalled();
		await settle(5);
		const url = new URL(fetchMock.mock.calls[0][0] as string, 'http://x');
		expect(url.searchParams.get('owned')).toBe('owned');
		expect(url.searchParams.get('page_size')).toBe('8');
		expect(url.searchParams.get('name')).toBe('sol');
		expect(listText()).toContain('Sol Ring');
	});

	it('Enter adds the top row with the typed quantity, clears and keeps focus', async () => {
		const { input, onadd } = setup();
		await type(input, '2x sol');
		await settle();
		await fireEvent.keyDown(input, { key: 'Enter' });
		await settle(0);
		expect(onadd).toHaveBeenCalledTimes(1);
		expect(onadd).toHaveBeenCalledWith({ card: SOL, qty: 2, board: 'main' });
		expect(input.value).toBe('');
		expect(document.activeElement).toBe(input);
		expect(screen.getByRole('status').textContent).toContain('Added Sol Ring');
	});

	it('a bare name adds qty 1', async () => {
		const { input, onadd } = setup();
		await type(input, 'sol');
		await settle();
		await fireEvent.keyDown(input, { key: 'Enter' });
		await settle(0);
		expect(onadd).toHaveBeenCalledWith({ card: SOL, qty: 1, board: 'main' });
	});

	it('ArrowDown highlights via aria-activedescendant and wraps', async () => {
		fetchMock.mockResolvedValue(json(body([SOL, hit('arc', 'Arcane Signet')])));
		const { input } = setup();
		await type(input, 'sol');
		await settle();
		await fireEvent.keyDown(input, { key: 'ArrowDown' });
		const first = input.getAttribute('aria-activedescendant');
		expect(document.getElementById(first!)?.textContent).toContain('Sol Ring');
		await fireEvent.keyDown(input, { key: 'ArrowDown' });
		await fireEvent.keyDown(input, { key: 'ArrowDown' });
		expect(input.getAttribute('aria-activedescendant')).toBe(first);
	});

	it('drops a stale response', async () => {
		let resolveFirst!: (r: Response) => void;
		fetchMock
			.mockImplementationOnce(() => new Promise<Response>((r) => (resolveFirst = r)))
			.mockImplementationOnce(async () => json(body([hit('arc', 'Arcane Signet')])));
		const { input } = setup();
		await type(input, 'so');
		await settle();
		await type(input, 'arc');
		await settle();
		expect(listText()).toContain('Arcane Signet');
		resolveFirst(json(body([SOL])));
		await settle(0);
		expect(listText()).toContain('Arcane Signet');
		expect(listText()).not.toContain('Sol Ring');
	});

	it('Searching… appears only after 300ms', async () => {
		fetchMock.mockImplementation(() => new Promise(() => {}));
		const { input } = setup();
		await type(input, 'sol');
		await settle(200);
		await settle(250);
		expect(listText()).not.toContain('Searching');
		await settle(100);
		expect(listText()).toContain('Searching…');
	});

	it('empty state names the term and explains owned-only', async () => {
		fetchMock.mockResolvedValue(json(body([])));
		const { input } = setup();
		await type(input, 'zzz');
		await settle();
		expect(listText()).toContain('No owned cards match “zzz”');
		expect(listText()).toContain('Draft staging only offers cards you own.');
	});

	it('error state is not the empty state, and Enter retries', async () => {
		fetchMock.mockResolvedValueOnce({ ok: false, status: 502, json: async () => ({}) } as Response);
		const { input } = setup();
		await type(input, 'sol');
		await settle();
		expect(listText()).toContain('Search failed — press Enter to retry');
		expect(listText()).not.toContain('No owned cards match');
		await fireEvent.keyDown(input, { key: 'Enter' });
		await settle(0);
		expect(fetchMock).toHaveBeenCalledTimes(2);
		expect(listText()).toContain('Sol Ring');
	});

	it('network failure is also an error row', async () => {
		fetchMock.mockRejectedValueOnce(new TypeError('offline'));
		const { input } = setup();
		await type(input, 'sol');
		await settle();
		expect(listText()).toContain('Search failed');
		expect(listText()).not.toContain('No owned cards match');
	});

	it('degraded shows the offline header row above results', async () => {
		fetchMock.mockResolvedValue(json(body([SOL], true)));
		const { input } = setup();
		await type(input, 'sol');
		await settle();
		expect(listText()).toContain('Card search is offline');
		expect(listText()).toContain('Sol Ring');
	});

	it('shows an In draft ×N badge', async () => {
		const { input } = setup(undefined, { inList: new Map([['sol', 3]]) });
		await type(input, 'sol');
		await settle();
		expect(listText()).toContain('In draft ×3');
	});

	it('a thrown onadd shows inline danger status, keeps the text, never "no match"', async () => {
		const onadd = vi.fn(async () => {
			throw new Error('Draft is locked');
		});
		const { input } = setup(onadd);
		await type(input, 'sol');
		await settle();
		await fireEvent.keyDown(input, { key: 'Enter' });
		await settle(0);
		const status = screen.getByRole('status');
		expect(status.textContent).toContain('Draft is locked');
		expect(status.classList.contains('is-error')).toBe(true);
		expect(input.value).toBe('sol');
		expect(document.body.textContent).not.toContain('No owned cards match');
	});

	it('Tab completes the name without adding', async () => {
		const { input, onadd } = setup();
		await type(input, '2x so');
		await settle();
		await fireEvent.keyDown(input, { key: 'Tab' });
		expect(input.value).toBe('2x Sol Ring');
		expect(onadd).not.toHaveBeenCalled();
	});

	it('Esc closes the list, a second Esc clears the input', async () => {
		const { input } = setup();
		await type(input, 'sol');
		await settle();
		expect(input.getAttribute('aria-expanded')).toBe('true');
		await fireEvent.keyDown(input, { key: 'Escape' });
		expect(input.getAttribute('aria-expanded')).toBe('false');
		expect(input.value).toBe('sol');
		await fireEvent.keyDown(input, { key: 'Escape' });
		expect(input.value).toBe('');
	});

	it('Ctrl+\' focuses the input', async () => {
		const { input } = setup();
		expect(document.activeElement).not.toBe(input);
		await fireEvent.keyDown(window, { key: "'", ctrlKey: true });
		expect(document.activeElement).toBe(input);
	});

	it('a multi-line paste adds nothing and points to Import', async () => {
		const { input, onadd } = setup();
		await fireEvent.paste(input, { clipboardData: { getData: () => '1 Sol Ring\n1 Command Tower' } });
		expect(screen.getByRole('status').textContent).toContain('Paste a list via Import');
		expect(onadd).not.toHaveBeenCalled();
		expect(fetchMock).not.toHaveBeenCalled();
	});

	it('uses the chosen board', async () => {
		const { input, onadd } = setup();
		await fireEvent.change(screen.getByLabelText('Board'), { target: { value: 'sideboard' } });
		await type(input, 'sol');
		await settle();
		await fireEvent.keyDown(input, { key: 'Enter' });
		await settle(0);
		expect(onadd).toHaveBeenCalledWith({ card: SOL, qty: 1, board: 'sideboard' });
	});
});

describe('QuickAdd a11y', () => {
	it('has no axe violations with the list open', async () => {
		vi.useRealTimers();
		const { input } = setup();
		await type(input, 'sol');
		await new Promise((r) => setTimeout(r, 300));
		expect(listText()).toContain('Sol Ring');
		await expectNoAxeViolations(document.body);
	});
});
