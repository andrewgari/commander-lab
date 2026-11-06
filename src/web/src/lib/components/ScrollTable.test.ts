import { afterEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet } from 'svelte';
import { render, screen } from '@testing-library/svelte';
import ScrollTable from './ScrollTable.svelte';
import type { Column } from './ScrollTable.svelte';
import { expectNoAxeViolations } from '#lib/test/axe.ts';

type Row = { name: string; have: number | null; buy?: number | null };
const columns = [
	{ key: 'name', header: 'Card' },
	{ key: 'have', header: 'Have', numeric: true },
	{ key: 'buy', header: 'Buy', numeric: true }
];
const rows: Row[] = [
	{ name: 'Sol Ring', have: 0, buy: null },
	{ name: 'Command Tower', have: 3 }
];
const base = { caption: 'Inventory', columns, rows, rowHeader: 'name' };
const snippet = (html: string) => createRawSnippet(() => ({ render: () => html }));

afterEach(() => vi.restoreAllMocks());

describe('ScrollTable caption and headers', () => {
	it('renders a visible caption and names the region with it', () => {
		const { container } = render(ScrollTable, base);
		const caps = container.querySelectorAll('caption');
		expect(caps).toHaveLength(1);
		expect(caps[0].textContent).toBe('Inventory');
		expect(caps[0].classList.contains('visually-hidden')).toBe(false);
		expect(screen.getByRole('region', { name: 'Inventory' })).toBeTruthy();
	});

	it('captionHidden adds visually-hidden but keeps the caption text', () => {
		const { container } = render(ScrollTable, { ...base, captionHidden: true });
		const cap = container.querySelector('caption')!;
		expect(cap.classList.contains('visually-hidden')).toBe(true);
		expect(cap.textContent).toBe('Inventory');
	});

	it('every column header has scope=col', () => {
		const { container } = render(ScrollTable, base);
		const heads = container.querySelectorAll('thead th');
		expect(heads).toHaveLength(3);
		heads.forEach((th) => expect(th.getAttribute('scope')).toBe('col'));
	});

	it('the rowHeader column renders th[scope=row]; other cells are td', () => {
		const { container } = render(ScrollTable, base);
		const first = container.querySelectorAll('tbody tr')[0];
		expect(first.querySelectorAll('th[scope="row"]')).toHaveLength(1);
		expect(first.querySelector('th[scope="row"]')?.textContent?.trim()).toBe('Sol Ring');
		expect(first.querySelectorAll('td')).toHaveLength(2);
	});

	it('no rowHeader means no row headers', () => {
		const { container } = render(ScrollTable, { ...base, rowHeader: undefined });
		expect(container.querySelector('tbody th')).toBeNull();
	});

	it('headerHidden renders sr-only text, never an empty th', () => {
		const { container } = render(ScrollTable, {
			...base,
			columns: [...columns, { key: 'x', header: 'Actions', headerHidden: true }]
		});
		const th = container.querySelectorAll('thead th')[3];
		expect(th.getAttribute('scope')).toBe('col');
		expect(th.querySelector('span.visually-hidden')?.textContent).toBe('Actions');
		container.querySelectorAll('thead th').forEach((h) => expect(h.textContent?.trim()).not.toBe(''));
	});
});

describe('ScrollTable cells', () => {
	it('renders 0 as "0" and null/undefined as an em dash with sr text', () => {
		const { container } = render(ScrollTable, base);
		const r0 = container.querySelectorAll('tbody tr')[0].querySelectorAll('td');
		expect(r0[0].textContent?.trim()).toBe('0');
		expect(r0[1].textContent).toContain('—');
		expect(r0[1].querySelector('.visually-hidden')?.textContent).toBe('no value');
		const r1 = container.querySelectorAll('tbody tr')[1].querySelectorAll('td');
		expect(r1[1].textContent).toContain('—');
		expect(r1[1].querySelector('.visually-hidden')?.textContent).toBe('no value');
	});

	it('numeric columns carry the st-num class (end aligned, tabular)', () => {
		const { container } = render(ScrollTable, base);
		expect(container.querySelectorAll('thead th.st-num')).toHaveLength(2);
		expect(container.querySelectorAll('tbody td.st-num')).toHaveLength(4);
	});

	it('the cell snippet overrides default content', () => {
		const cell = createRawSnippet<[unknown, Column]>((row, col) => ({
			render: () => `<span class="pill">${col().key}:${(row() as Row).name}</span>`
		}));
		const { container } = render(ScrollTable, { ...base, cell });
		expect(container.querySelectorAll('.pill')).toHaveLength(6);
		expect(container.querySelector('.pill')?.textContent).toBe('name:Sol Ring');
	});

	it('uses rowKey when given without errors', () => {
		const { container } = render(ScrollTable, { ...base, rowKey: (r: unknown) => (r as Row).name });
		expect(container.querySelectorAll('tbody tr')).toHaveLength(2);
	});
});

describe('ScrollTable density and sticky', () => {
	it('compact density adds st--compact', () => {
		const { container } = render(ScrollTable, { ...base, density: 'compact' });
		expect(container.querySelector('.st--compact')).not.toBeNull();
	});

	it('stickyFirstCol applies when rowHeader is the first column', () => {
		const { container } = render(ScrollTable, { ...base, stickyFirstCol: true });
		expect(container.querySelector('.st--sticky')).not.toBeNull();
	});

	it('stickyFirstCol without a first-column rowHeader warns and does not stick', async () => {
		const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
		const { container } = render(ScrollTable, { ...base, rowHeader: 'have', stickyFirstCol: true });
		await Promise.resolve();
		expect(container.querySelector('.st--sticky')).toBeNull();
		expect(warn).toHaveBeenCalled();
	});
});

describe('ScrollTable empty', () => {
	it('rows=[] renders no table and no region, but the EmptyState title and a hidden caption', () => {
		const { container } = render(ScrollTable, { ...base, rows: [], emptyTitle: 'No cards' });
		expect(container.querySelector('table')).toBeNull();
		expect(container.querySelector('[role="region"]')).toBeNull();
		expect(container.querySelector('[role="status"], [aria-live]')).toBeNull();
		expect(screen.getByText('No cards')).toBeTruthy();
		expect(container.querySelector('.st-empty > p.visually-hidden')?.textContent).toBe('Inventory');
	});

	it('defaults the empty title', () => {
		render(ScrollTable, { ...base, rows: [] });
		expect(screen.getByText('Nothing to show')).toBeTruthy();
	});

	it('forwards emptyDescription', () => {
		render(ScrollTable, { ...base, rows: [], emptyDescription: 'Import a deck first' });
		expect(screen.getByText('Import a deck first')).toBeTruthy();
	});

	it('the empty snippet replaces the default', () => {
		render(ScrollTable, { ...base, rows: [], empty: snippet('<p>Custom empty</p>') });
		expect(screen.getByText('Custom empty')).toBeTruthy();
		expect(screen.queryByText('Nothing to show')).toBeNull();
	});
});

describe('ScrollTable instances and axe', () => {
	it('two instances have distinct region names and no duplicate ids', () => {
		const a = render(ScrollTable, { ...base, caption: 'Have' });
		const b = render(ScrollTable, { ...base, caption: 'Buy' });
		const names = [...document.querySelectorAll('[role="region"]')].map((e) => e.getAttribute('aria-label'));
		expect(names).toEqual(['Have', 'Buy']);
		const ids = [...document.querySelectorAll('[id]')].map((e) => e.id);
		expect(new Set(ids).size).toBe(ids.length);
		a.unmount();
		b.unmount();
	});

	it('populated table has no axe violations', async () => {
		const { container } = render(ScrollTable, { ...base, stickyFirstCol: true });
		await expectNoAxeViolations(container);
	});

	it('empty table has no axe violations', async () => {
		const { container } = render(ScrollTable, { ...base, rows: [] });
		await expectNoAxeViolations(container);
	});
});
