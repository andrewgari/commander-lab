import axe from 'axe-core';
import { expect } from 'vitest';

/**
 * Fail if axe finds any WCAG 2.0/2.1/2.2 A/AA violation in `container`.
 * `color-contrast` is off because jsdom cannot compute layout or styles; the browser runner
 * (src/web/tests/components_a11y.py) covers contrast against the real CSS.
 */
export async function expectNoAxeViolations(container: Element): Promise<void> {
	const results = await axe.run(container, {
		runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] },
		rules: { 'color-contrast': { enabled: false } }
	});
	const summary = results.violations.map(
		(v) => `${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(' ')).join(', ')})`
	);
	expect(summary).toEqual([]);
}
