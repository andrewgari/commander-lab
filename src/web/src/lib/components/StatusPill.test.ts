import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/svelte';
import StatusPill from './StatusPill.svelte';
import { STATUS_CATALOG, type StatusKey } from '#lib/status.ts';
import { expectNoAxeViolations } from '#lib/test/axe.ts';

const KEYS = Object.keys(STATUS_CATALOG) as StatusKey[];
// Name-from-content as a parent link/cell/heading would compute it: skip aria-hidden subtrees.
// (A bare span has no role of its own, so this is what an assistive tech reads for the pill.)
function nameFromContent(node: Node): string {
	if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? '';
	if (!(node instanceof Element) || node.getAttribute('aria-hidden') === 'true') return '';
	return [...node.childNodes].map(nameFromContent).join('');
}
const pill = (c: HTMLElement) => c.querySelector<HTMLElement>('[data-status]')!;

describe('StatusPill', () => {
	it.each(KEYS)('%s renders its label and an aria-hidden icon', (status) => {
		const { container } = render(StatusPill, { status });
		const el = pill(container);
		const e = STATUS_CATALOG[status];
		expect(el.dataset.status).toBe(status);
		expect(el.querySelector('.pill__label')!.textContent).toBe(e.label);
		expect(el.classList.contains(`pill--${e.role}`)).toBe(true);
		expect(el.classList.contains(`pill--${e.variant}`)).toBe(true);
		expect(el.classList.contains('pill--sm')).toBe(true);
		const svg = el.querySelector('svg')!;
		expect(svg.getAttribute('aria-hidden')).toBe('true');
		expect(svg.dataset.icon).toBe(e.icon);
	});

	it('icon={null} removes the icon', () => {
		const { container } = render(StatusPill, { status: 'game-win', icon: null });
		expect(pill(container).querySelector('svg')).toBeNull();
		expect(pill(container).textContent?.trim()).toBe('Win');
	});

	it('icon override swaps the glyph', () => {
		const { container } = render(StatusPill, { status: 'neutral', icon: 'help' });
		expect(pill(container).querySelector('svg')!.dataset.icon).toBe('help');
	});

	it('label override (fallback raw values)', () => {
		const { container } = render(StatusPill, { status: 'neutral', label: 'archived' });
		expect(pill(container).querySelector('.pill__label')!.textContent).toBe('archived');
	});

	it('variant and size override the defaults', () => {
		const { container } = render(StatusPill, { status: 'draft-committed', variant: 'text', size: 'md' });
		const el = pill(container);
		expect(el.classList.contains('pill--text')).toBe(true);
		expect(el.classList.contains('pill--solid')).toBe(false);
		expect(el.classList.contains('pill--md')).toBe(true);
	});

	it('passes title, describedby and class through', () => {
		const { container } = render(StatusPill, { status: 'unavailable', title: 'No data', describedby: 'why', class: 'mine' });
		const el = pill(container);
		expect(el.getAttribute('title')).toBe('No data');
		expect(el.getAttribute('aria-describedby')).toBe('why');
		expect(el.classList.contains('mine')).toBe(true);
	});

	it.each(KEYS)('%s: accessible name equals the visible label, with no ARIA overrides', (status) => {
		const { container } = render(StatusPill, { status });
		const el = pill(container);
		const label = STATUS_CATALOG[status].label;
		for (const attr of ['aria-label', 'aria-labelledby', 'role', 'tabindex']) {
			expect(el.hasAttribute(attr), attr).toBe(false);
		}
		// The icon contributes nothing; only the visible label names the pill.
		expect(nameFromContent(el).replace(/\s+/g, ' ').trim()).toBe(label);
	});

	it('has no focusable element', () => {
		const { container } = render(StatusPill, { status: 'short' });
		const focusable = container.querySelectorAll(
			'a[href], button, input, select, textarea, [tabindex], [contenteditable]'
		);
		expect(focusable).toHaveLength(0);
		expect(container.querySelector('svg')!.getAttribute('focusable')).toBe('false');
	});

	it('has no axe violations over the whole catalog, both variants and sizes', async () => {
		const { container } = render(StatusPill, { status: 'neutral' });
		container.innerHTML = '';
		const host = document.createElement('ul');
		container.append(host);
		for (const status of KEYS) {
			for (const variant of ['solid', 'text'] as const) {
				for (const size of ['sm', 'md'] as const) {
					const li = document.createElement('li');
					host.append(li);
					render(StatusPill, { target: li, props: { status, variant, size } });
				}
			}
		}
		expect(host.querySelectorAll('[data-status]')).toHaveLength(KEYS.length * 4);
		await expectNoAxeViolations(container);
	});
});
