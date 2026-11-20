import { afterEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet } from 'svelte';
import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import EmptyState from './EmptyState.svelte';
import { expectNoAxeViolations } from '#lib/test/axe.ts';

const snippet = (html: string) => createRawSnippet(() => ({ render: () => html }));

afterEach(() => vi.restoreAllMocks());

describe('EmptyState headings', () => {
	it('page defaults to h2, inline to h3', () => {
		const page = render(EmptyState, { title: 'No decks yet' });
		expect(page.container.querySelector('h2.empty-state__title')?.textContent).toBe('No decks yet');
		page.unmount();
		const inline = render(EmptyState, { title: 'No decks yet', variant: 'inline' });
		expect(inline.container.querySelector('h3.empty-state__title')).not.toBeNull();
	});

	it.each([2, 3, 4] as const)('honours headingLevel %i', (level) => {
		const { container } = render(EmptyState, { title: 'T', headingLevel: level });
		expect(container.querySelector(`h${level}`)).not.toBeNull();
		expect(container.querySelector('h1')).toBeNull();
	});

	it('compact inline renders a <p> and no heading', () => {
		const { container } = render(EmptyState, { title: 'No tags yet', variant: 'inline', compact: true });
		const title = container.querySelector('.empty-state__title')!;
		expect(title.tagName).toBe('P');
		expect(container.querySelector('h1,h2,h3,h4,h5,h6')).toBeNull();
	});

	it('compact is ignored for the page variant', () => {
		const { container } = render(EmptyState, { title: 'T', compact: true });
		expect(container.querySelector('h2')).not.toBeNull();
	});
});

describe('EmptyState root', () => {
	it.each(['page', 'inline'] as const)('is a plain div with no landmark for %s', (variant) => {
		const { container } = render(EmptyState, { title: 'T', variant });
		const root = container.firstElementChild!;
		expect(root.tagName).toBe('DIV');
		expect(root.classList.contains('empty-state')).toBe(true);
		expect(root.classList.contains(`empty-state--${variant}`)).toBe(true);
		expect(root.hasAttribute('aria-labelledby')).toBe(false);
		expect(root.hasAttribute('role')).toBe(false);
		expect(container.querySelector('section')).toBeNull();
	});

	it('filtered adds role=status', () => {
		const { container } = render(EmptyState, { title: 'No cards match', variant: 'inline', filtered: true });
		expect(container.firstElementChild!.getAttribute('role')).toBe('status');
		expect(screen.getByRole('status')).toBeTruthy();
	});

	it('applies a caller class', () => {
		const { container } = render(EmptyState, { title: 'T', class: 'mt' });
		expect(container.firstElementChild!.classList.contains('mt')).toBe(true);
	});

	it('does not move focus on mount', () => {
		const before = document.activeElement;
		render(EmptyState, { title: 'T', ctaLabel: 'Go', ctaHref: '/x' });
		expect(document.activeElement).toBe(before);
	});

	it('renders the description as a paragraph', () => {
		const { container } = render(EmptyState, { title: 'T', description: 'Do the thing.' });
		expect(container.querySelector('p.empty-state__desc')?.textContent).toBe('Do the thing.');
	});
});

describe('EmptyState CTA', () => {
	it('renders a link with the primary class on page', () => {
		render(EmptyState, { title: 'T', ctaLabel: 'Import a deck', ctaHref: '/decks' });
		const link = screen.getByRole('link', { name: 'Import a deck' });
		expect(link.getAttribute('href')).toBe('/decks');
		expect(link.classList.contains('btn')).toBe(true);
		expect(link.classList.contains('btn--primary')).toBe(true);
	});

	it('defaults to secondary on inline and honours ctaVariant', () => {
		const a = render(EmptyState, { title: 'T', variant: 'inline', ctaLabel: 'Go', ctaHref: '/x' });
		expect(screen.getByRole('link').classList.contains('btn--secondary')).toBe(true);
		a.unmount();
		render(EmptyState, { title: 'T', variant: 'inline', ctaLabel: 'Go', ctaHref: '/x', ctaVariant: 'primary' });
		expect(screen.getByRole('link').classList.contains('btn--primary')).toBe(true);
	});

	it('renders a type=button button; onCta fires once on click', async () => {
		const onCta = vi.fn();
		const user = userEvent.setup();
		render(EmptyState, { title: 'T', ctaLabel: 'Clear search', onCta });
		const btn = screen.getByRole('button', { name: 'Clear search' });
		expect(btn.getAttribute('type')).toBe('button');
		expect(btn.classList.contains('btn')).toBe(true);
		await user.click(btn);
		expect(onCta).toHaveBeenCalledTimes(1);
	});

	it('onCta fires once on Enter', async () => {
		const onCta = vi.fn();
		const user = userEvent.setup();
		render(EmptyState, { title: 'T', ctaLabel: 'Clear search', onCta });
		await user.tab();
		expect(document.activeElement).toBe(screen.getByRole('button'));
		await user.keyboard('{Enter}');
		expect(onCta).toHaveBeenCalledTimes(1);
	});

	it('ctaHref wins over onCta', async () => {
		const onCta = vi.fn();
		render(EmptyState, { title: 'T', ctaLabel: 'Go', ctaHref: '/x', onCta });
		expect(screen.getByRole('link', { name: 'Go' })).toBeTruthy();
		expect(screen.queryByRole('button')).toBeNull();
		expect(onCta).not.toHaveBeenCalled();
	});

	it('renders no CTA without a label or without a target', () => {
		const a = render(EmptyState, { title: 'T', description: 'd' });
		expect(screen.queryByRole('link')).toBeNull();
		expect(screen.queryByRole('button')).toBeNull();
		a.unmount();
		vi.spyOn(console, 'warn').mockImplementation(() => {});
		render(EmptyState, { title: 'T', ctaLabel: 'Orphan' });
		expect(screen.queryByRole('link')).toBeNull();
		expect(screen.queryByRole('button')).toBeNull();
	});

	it('warns in dev when ctaLabel has no ctaHref or onCta', () => {
		const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
		render(EmptyState, { title: 'T', ctaLabel: 'Orphan' });
		expect(warn).toHaveBeenCalledTimes(1);
		expect(String(warn.mock.calls[0][0])).toContain('Orphan');
	});

	it('does not warn when a target is present', () => {
		const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
		render(EmptyState, { title: 'T', ctaLabel: 'Go', ctaHref: '/x' });
		expect(warn).not.toHaveBeenCalled();
	});
});

describe('EmptyState icon and snippets', () => {
	it('wraps the icon in an aria-hidden span sized by variant', () => {
		const page = render(EmptyState, { title: 'T', icon: 'info' });
		const wrap = page.container.querySelector('.empty-state__icon')!;
		expect(wrap.getAttribute('aria-hidden')).toBe('true');
		expect(wrap.querySelector('svg')!.getAttribute('width')).toBe('48px');
		page.unmount();
		const inline = render(EmptyState, { title: 'T', variant: 'inline', icon: 'info' });
		expect(inline.container.querySelector('.empty-state__icon svg')!.getAttribute('width')).toBe('24px');
	});

	it('renders no icon wrapper without an icon', () => {
		const { container } = render(EmptyState, { title: 'T' });
		expect(container.querySelector('.empty-state__icon')).toBeNull();
	});

	it('children replace the description', () => {
		const { container } = render(EmptyState, {
			title: 'T',
			description: 'plain',
			children: snippet('<span data-testid="rich">rich <a href="/x">link</a></span>')
		});
		expect(screen.getByTestId('rich')).toBeTruthy();
		expect(container.textContent).not.toContain('plain');
	});

	it('actions replace the built-in CTA', () => {
		render(EmptyState, {
			title: 'T',
			ctaLabel: 'Built-in',
			ctaHref: '/x',
			actions: snippet('<a class="btn btn--primary" href="/a">First</a>')
		});
		expect(screen.getByRole('link', { name: 'First' })).toBeTruthy();
		expect(screen.queryByRole('link', { name: 'Built-in' })).toBeNull();
	});
});

describe('EmptyState axe', () => {
	const cases = [
		['page with link CTA', { title: 'No decks yet', description: 'Import one.', ctaLabel: 'Import a deck', ctaHref: '/decks', icon: 'info' }],
		['page with button CTA', { title: 'No drafts yet', ctaLabel: 'Start', onCta: () => {} }],
		['inline', { title: 'No games yet', variant: 'inline', description: 'Import a log.', icon: 'help' }],
		['inline compact', { title: 'No tags yet', variant: 'inline', compact: true }],
		['filtered', { title: 'No cards match', variant: 'inline', filtered: true, ctaLabel: 'Clear search', onCta: () => {} }]
	] as const;

	it.each(cases)('%s has no violations', async (_name, props) => {
		const { container } = render(EmptyState, props as never);
		await expectNoAxeViolations(container);
	});
});
