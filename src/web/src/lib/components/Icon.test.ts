import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/svelte';
import Icon from './Icon.svelte';
import { ICON_NAMES, ICON_PATHS, type IconName } from '#lib/icons.ts';
import { expectNoAxeViolations } from '#lib/test/axe.ts';

describe('Icon', () => {
	it('has a path for every IconName', () => {
		expect(ICON_NAMES).toHaveLength(13);
		for (const name of ICON_NAMES) expect(ICON_PATHS[name]).toMatch(/^M/);
	});

	it.each(ICON_NAMES)('renders %s as a decorative 16-viewBox stroke svg', (name) => {
		const { container } = render(Icon, { name });
		const svg = container.querySelector('svg');
		expect(svg).not.toBeNull();
		expect(svg!.getAttribute('aria-hidden')).toBe('true');
		expect(svg!.getAttribute('focusable')).toBe('false');
		expect(svg!.getAttribute('viewBox')).toBe('0 0 16 16');
		expect(svg!.getAttribute('stroke')).toBe('currentColor');
		expect(svg!.getAttribute('stroke-width')).toBe('1.5');
		expect(svg!.querySelector('path')!.getAttribute('d')).toBe(ICON_PATHS[name]);
	});

	it('defaults size to 1em and accepts size and class', () => {
		const a = render(Icon, { name: 'check' }).container.querySelector('svg')!;
		expect(a.getAttribute('width')).toBe('1em');
		expect(a.getAttribute('height')).toBe('1em');
		const b = render(Icon, { name: 'check', size: '24px', class: 'mine' }).container.querySelector('svg')!;
		expect(b.getAttribute('width')).toBe('24px');
		expect(b.classList.contains('mine')).toBe(true);
	});

	it('rejects unknown names at type level', () => {
		// @ts-expect-error 'nope' is not an IconName
		const bad: IconName = 'nope';
		expect(ICON_NAMES).not.toContain(bad);
		// @ts-expect-error 'nope' is not an IconName
		render(Icon, { name: 'nope' }).unmount?.();
	});

	it('has no axe violations (every icon, with adjacent text)', async () => {
		const { container } = render(Icon, { name: 'check' });
		await expectNoAxeViolations(container);
	});
});
