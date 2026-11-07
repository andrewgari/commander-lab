import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/svelte';
import Combobox from './Combobox.svelte';
import { expectNoAxeViolations } from '#lib/test/axe.ts';
import type { ComboOption } from './model.ts';

const OPTS: ComboOption<string>[] = [
	{ key: 'a', label: 'Sol Ring', detail: 'Artifact', value: 'id-a' },
	{ key: 'b', label: 'Solemn Simulacrum', detail: 'Artifact Creature', value: 'id-b' },
	{ key: 'c', label: 'Sol Talisman', detail: 'Artifact', value: 'id-c' }
];

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

function setup(search = vi.fn(async () => OPTS), extra: Record<string, unknown> = {}) {
	const r = render(Combobox, { label: "Card", search, ...extra } as never);
	const input = screen.getByRole('combobox') as HTMLInputElement;
	return { ...r, input, search };
}

async function type(input: HTMLInputElement, value: string) {
	input.focus();
	input.value = value;
	await fireEvent.input(input);
}

/** Visible state message (aria-hidden; the live region carries the spoken copy). */
const stateText = () => document.querySelector('.state')?.textContent ?? '';

async function settle(ms = 250) {
	await vi.advanceTimersByTimeAsync(ms);
}

describe('Combobox ARIA', () => {
	it('exposes combobox/listbox wiring', async () => {
		const { input } = setup();
		expect(input.getAttribute('aria-expanded')).toBe('false');
		const listId = input.getAttribute('aria-controls')!;
		expect(document.getElementById(listId)?.getAttribute('role')).toBe('listbox');
		await type(input, 'sol');
		await settle();
		expect(input.getAttribute('aria-expanded')).toBe('true');
		expect(screen.getAllByRole('option')).toHaveLength(3);
		await fireEvent.keyDown(input, { key: 'ArrowDown' });
		const opts = screen.getAllByRole('option');
		expect(input.getAttribute('aria-activedescendant')).toBe(opts[0].id);
		expect(opts[0].getAttribute('aria-selected')).toBe('true');
		expect(opts[1].getAttribute('aria-selected')).toBe('false');
	});

	it('has no axe violations open with results', async () => {
		vi.useRealTimers();
		const { input, container } = setup(vi.fn(async () => OPTS), { debounceMs: 0 });
		await type(input, 'sol');
		await screen.findAllByRole('option');
		await expectNoAxeViolations(container);
	});
});

describe('Combobox debounce and cancellation', () => {
	it('debounces to one request after 250ms of quiet', async () => {
		const { input, search } = setup();
		await type(input, 'so');
		await settle(100);
		await type(input, 'sol');
		await settle(100);
		expect(search).not.toHaveBeenCalled();
		await settle(150);
		expect(search).toHaveBeenCalledTimes(1);
		expect((search.mock.calls[0] as unknown[])[0]).toBe('sol');
	});

	it('aborts the in-flight request and ignores its late answer', async () => {
		const signals: AbortSignal[] = [];
		let first!: (v: ComboOption<string>[]) => void;
		const search = vi.fn((term: string, signal: AbortSignal) => {
			signals.push(signal);
			return term === 'sol'
				? new Promise<ComboOption<string>[]>((res) => (first = res))
				: Promise.resolve([OPTS[1]]);
		});
		const { input } = setup(search as never);
		await type(input, 'sol');
		await settle();
		await type(input, 'solem');
		expect(signals[0].aborted).toBe(true);
		await settle();
		first(OPTS); // stale answer arrives late
		await settle(0);
		const shown = screen.getAllByRole('option');
		expect(shown).toHaveLength(1);
		expect(shown[0].textContent).toContain('Solemn Simulacrum');
	});
});

describe('Combobox states', () => {
	it('empty query shows nothing, short query hints, then loading, no results, error', async () => {
		const search = vi.fn(async () => [] as ComboOption<string>[]);
		const { input } = setup(search);
		await fireEvent.focus(input);
		expect(stateText()).toBe('');
		expect(screen.getByRole('status').textContent).toBe('');
		await type(input, 's');
		expect(stateText()).toBe('Type at least 2 letters to search');
		expect(search).not.toHaveBeenCalled();
		await type(input, 'zz');
		expect(stateText()).toBe('Searching…');
		expect(screen.getByRole('status').textContent).toBe('Searching…');
		await settle();
		expect(stateText()).toBe('No cards match “zz”');
		expect(screen.queryByRole('option')).toBeNull();
	});

	it('shows an error state distinct from no results', async () => {
		const search = vi.fn(async () => {
			throw new Error('HTTP 502');
		});
		const { input } = setup(search as never);
		await type(input, 'sol');
		await settle();
		expect(stateText()).toBe('Search failed: HTTP 502');
		expect(stateText()).not.toMatch(/No cards match/);
	});
});

describe('Combobox keyboard', () => {
	async function open() {
		const ctx = setup();
		await type(ctx.input, 'sol');
		await settle();
		return ctx;
	}
	const active = (input: HTMLElement) =>
		document.getElementById(input.getAttribute('aria-activedescendant') ?? '')?.textContent ?? '';

	it('ArrowDown/Up wrap, Home/End jump', async () => {
		const { input } = await open();
		await fireEvent.keyDown(input, { key: 'ArrowDown' });
		await fireEvent.keyDown(input, { key: 'ArrowDown' });
		expect(active(input)).toContain('Solemn');
		await fireEvent.keyDown(input, { key: 'End' });
		expect(active(input)).toContain('Sol Talisman');
		await fireEvent.keyDown(input, { key: 'ArrowDown' });
		expect(active(input)).toContain('Sol Ring');
		await fireEvent.keyDown(input, { key: 'ArrowUp' });
		expect(active(input)).toContain('Sol Talisman');
		await fireEvent.keyDown(input, { key: 'Home' });
		expect(active(input)).toContain('Sol Ring');
	});

	it('Enter selects the active option, fills the label and closes', async () => {
		const { input } = setup();
		await type(input, 'sol');
		await settle();
		await fireEvent.keyDown(input, { key: 'ArrowDown' });
		await fireEvent.keyDown(input, { key: 'ArrowDown' });
		await fireEvent.keyDown(input, { key: 'Enter' });
		expect(input.value).toBe('Solemn Simulacrum');
		expect(input.getAttribute('aria-expanded')).toBe('false');
		expect(screen.queryByRole('option')).toBeNull();
	});

	it('Enter with nothing active does not select but is swallowed while open', async () => {
		const { input } = await open();
		const notPrevented = await fireEvent.keyDown(input, { key: 'Enter' });
		expect(notPrevented).toBe(false);
		expect(input.value).toBe('sol');
	});

	it('Escape closes the list first, then clears the text', async () => {
		const { input } = await open();
		await fireEvent.keyDown(input, { key: 'Escape' });
		expect(input.getAttribute('aria-expanded')).toBe('false');
		expect(input.value).toBe('sol');
		await fireEvent.keyDown(input, { key: 'Escape' });
		expect(input.value).toBe('');
	});

	it('Tab closes without selecting', async () => {
		const { input } = await open();
		await fireEvent.keyDown(input, { key: 'ArrowDown' });
		await fireEvent.keyDown(input, { key: 'Tab' });
		expect(input.getAttribute('aria-expanded')).toBe('false');
		expect(input.value).toBe('sol');
	});

	it('clicking an option selects it', async () => {
		const { input } = await open();
		await fireEvent.click(screen.getAllByRole('option')[2]);
		expect(input.value).toBe('Sol Talisman');
	});
});

describe('Combobox selection and validity', () => {
	it('required: invalid until an option is chosen, valid after, invalid again after editing', async () => {
		const { input } = setup(undefined, { required: true });
		expect(input.validity.valid).toBe(false);
		await type(input, 'sol');
		await settle();
		await fireEvent.keyDown(input, { key: 'ArrowDown' });
		await fireEvent.keyDown(input, { key: 'Enter' });
		await vi.advanceTimersByTimeAsync(0);
		expect(input.validity.valid).toBe(true);
		await type(input, 'sola');
		await vi.advanceTimersByTimeAsync(0);
		expect(input.validity.valid).toBe(false);
	});

	it('never renders the internal value in the DOM', async () => {
		const { input, container } = setup();
		await type(input, 'sol');
		await settle();
		await fireEvent.keyDown(input, { key: 'ArrowDown' });
		await fireEvent.keyDown(input, { key: 'Enter' });
		expect(container.innerHTML).not.toContain('id-a');
	});
});
