import { describe, expect, it } from 'vitest';
import { createRawSnippet } from 'svelte';
import { fireEvent, render } from '@testing-library/svelte';
import SubmitButton from './SubmitButton.svelte';
import { expectNoAxeViolations } from '#lib/test/axe.ts';

const label = (text: string) => createRawSnippet(() => ({ render: () => `<span>${text}</span>` }));

function mount(props: Record<string, unknown> = {}) {
	const result = render(SubmitButton, {
		pending: false,
		pendingLabel: 'Saving…',
		children: label('Save'),
		...props
	});
	const button = result.container.querySelector('button') as HTMLButtonElement;
	return { ...result, button, status: result.container.querySelector('[role="status"]') as HTMLElement };
}

/** Accessible name per accname: aria-label, else the rendered text minus aria-hidden / visibility:hidden subtrees. */
function accessibleName(el: Element): string {
	const aria = el.getAttribute('aria-label');
	if (aria) return aria;
	const walk = (n: Node): string => {
		if (n.nodeType === Node.TEXT_NODE) return n.textContent ?? '';
		if (!(n instanceof HTMLElement)) return '';
		if (n.getAttribute('aria-hidden') === 'true' || n.hidden) return '';
		return [...n.childNodes].map(walk).join('');
	};
	return walk(el).replace(/\s+/g, ' ').trim();
}

describe('SubmitButton', () => {
	it('idle: shows the idle label, not busy', () => {
		const { button, status } = mount();
		expect(button.textContent).toContain('Save');
		expect(accessibleName(button)).toBe('Save');
		expect(status.textContent).toBe('');
		expect(button.getAttribute('aria-disabled')).toBeNull();
	});

	it('busy: the accessible name is the pending label, once', () => {
		const { button, status } = mount({ pending: true });
		expect(accessibleName(button)).toBe('Saving…');
		expect(status.textContent).toBe('Saving…');
		// the status text is outside the button, so the name cannot pick it up
		expect(button.contains(status)).toBe(false);
	});

	it('the status span is a sibling placed right after the button', () => {
		const { button, status } = mount({ pending: true });
		expect(button.nextElementSibling).toBe(status);
		expect(status.parentElement).toBe(button.parentElement);
		expect(status.classList.contains('visually-hidden')).toBe(true);
	});

	it('aria-disabled is present only while pending, and the disabled attribute is not used for it', () => {
		const idle = mount();
		expect(idle.button.hasAttribute('aria-disabled')).toBe(false);
		expect(idle.button.disabled).toBe(false);
		idle.unmount();

		const busy = mount({ pending: true });
		expect(busy.button.getAttribute('aria-disabled')).toBe('true');
		expect(busy.button.disabled).toBe(false);
		expect(busy.button.tabIndex).toBe(0);
	});

	it('keeps focus while pending', async () => {
		const { button, rerender } = mount();
		button.focus();
		await rerender({ pending: true });
		expect(document.activeElement).toBe(button);
	});

	it('a click while pending is defaultPrevented; an idle click is not', async () => {
		const idle = mount();
		const idleEvent = new MouseEvent('click', { bubbles: true, cancelable: true });
		idle.button.dispatchEvent(idleEvent);
		expect(idleEvent.defaultPrevented).toBe(false);
		idle.unmount();

		const busy = mount({ pending: true });
		const event = new MouseEvent('click', { bubbles: true, cancelable: true });
		busy.button.dispatchEvent(event);
		expect(event.defaultPrevented).toBe(true);
	});

	it('does not submit its form while pending', async () => {
		const form = document.createElement('form');
		let submits = 0;
		form.addEventListener('submit', (e) => {
			e.preventDefault();
			submits++;
		});
		document.body.append(form);
		const { button } = mount({ pending: true }, );
		form.append(button);
		await fireEvent.click(button);
		expect(submits).toBe(0);
		form.remove();
	});

	it('disabled prop sets the disabled attribute', () => {
		const { button } = mount({ disabled: true });
		expect(button.disabled).toBe(true);
		expect(button.hasAttribute('aria-disabled')).toBe(false);
	});

	it('busy defaults to pending', () => {
		const a = mount({ pending: true });
		expect(a.status.textContent).toBe('Saving…');
		a.unmount();
		const b = mount({ pending: false });
		expect(b.status.textContent).toBe('');
	});

	it('busy overrides pending: locked but not showing the pending label', () => {
		const locked = mount({ pending: true, busy: false });
		expect(locked.button.getAttribute('aria-disabled')).toBe('true');
		expect(locked.status.textContent).toBe('');
		expect(accessibleName(locked.button)).toBe('Save');
		locked.unmount();

		const pressed = mount({ pending: true, busy: true });
		expect(pressed.status.textContent).toBe('Saving…');
		expect(accessibleName(pressed.button)).toBe('Saving…');
	});

	it('both labels stay in the DOM (stacked in one grid cell) so the width never changes', () => {
		const { button } = mount({ pending: true });
		expect(button.textContent).toContain('Save');
		expect(button.textContent).toContain('Saving…');
		const hidden = button.querySelectorAll('.sb__hidden');
		expect(hidden).toHaveLength(1);
		expect(hidden[0].textContent).toContain('Save');
	});

	it('aria-label passes through', () => {
		const { button } = mount({ 'aria-label': 'Remove Sol Ring' });
		expect(button.getAttribute('aria-label')).toBe('Remove Sol Ring');
		expect(accessibleName(button)).toBe('Remove Sol Ring');
	});

	it('type defaults to submit and can be button; formaction and class pass through', () => {
		expect(mount().button.type).toBe('submit');
		const b = mount({ type: 'button', formaction: '?/clear', class: 'mine' });
		expect(b.button.type).toBe('button');
		expect(b.button.getAttribute('formaction')).toBe('?/clear');
		expect(b.button.classList.contains('mine')).toBe(true);
		expect(b.button.classList.contains('btn')).toBe(true);
	});

	it('variant maps to .btn--*, default secondary', () => {
		expect(mount().button.classList.contains('btn--secondary')).toBe(true);
		for (const v of ['primary', 'secondary', 'success', 'danger', 'link-danger']) {
			const { button, unmount } = mount({ variant: v });
			expect(button.classList.contains(`btn--${v}`)).toBe(true);
			unmount();
		}
	});

	it('has no axe violations idle, pending and busy-only', async () => {
		for (const props of [{}, { pending: true }, { pending: true, busy: false }, { disabled: true }]) {
			const { container, unmount } = mount(props);
			await expectNoAxeViolations(container);
			unmount();
		}
	});
});
