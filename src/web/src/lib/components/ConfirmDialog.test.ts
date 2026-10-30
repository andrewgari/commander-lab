import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet } from 'svelte';
import { render, screen } from '@testing-library/svelte';
import ConfirmDialog from './ConfirmDialog.svelte';

// jsdom has no showModal()/close(); stub them with the open-attribute + close-event behaviour.
beforeEach(() => {
	HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
		this.setAttribute('open', '');
	};
	HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
		if (!this.hasAttribute('open')) return;
		this.removeAttribute('open');
		this.dispatchEvent(new Event('close'));
	};
});
afterEach(() => {
	delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).showModal;
	delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).close;
});

const body = createRawSnippet(() => ({ render: () => '<p>Body text</p>' }));
const actions = createRawSnippet(() => ({ render: () => '<button type="submit">Commit</button>' }));
const alertSnip = createRawSnippet(() => ({ render: () => '<p>Failed</p>' }));

function setup(props: Record<string, unknown> = {}) {
	const opener = document.createElement('button');
	document.body.appendChild(opener);
	opener.focus();
	const r = render(ConfirmDialog, { title: 'Commit drafts', children: body, actions, ...props });
	const dialog = document.querySelector('dialog') as HTMLDialogElement;
	return { ...r, opener, dialog, comp: r.component as unknown as { show(o?: HTMLElement): void; close(): void } };
}

describe('ConfirmDialog', () => {
	it('show() opens modally and focuses Cancel', () => {
		const { comp, dialog } = setup();
		const spy = vi.spyOn(HTMLDialogElement.prototype, 'showModal');
		comp.show();
		expect(spy).toHaveBeenCalled();
		expect(dialog.open).toBe(true);
		expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Cancel', hidden: true }));
	});

	it('Cancel closes the dialog and fires onclose', () => {
		const onclose = vi.fn();
		const { comp, dialog } = setup({ onclose });
		comp.show();
		screen.getByRole('button', { name: 'Cancel' }).click();
		expect(dialog.open).toBe(false);
		expect(onclose).toHaveBeenCalledOnce();
	});

	it('Esc (cancel event) is prevented while pending, allowed otherwise', () => {
		const { comp, dialog, rerender } = setup({ pending: true });
		comp.show();
		const blocked = new Event('cancel', { cancelable: true });
		dialog.dispatchEvent(blocked);
		expect(blocked.defaultPrevented).toBe(true);
		const cancel = screen.getByRole('button', { name: 'Cancel' });
		expect(cancel.getAttribute('aria-disabled')).toBe('true');
		cancel.click();
		expect(dialog.open).toBe(true);

		return rerender({ pending: false }).then(() => {
			const free = new Event('cancel', { cancelable: true });
			dialog.dispatchEvent(free);
			expect(free.defaultPrevented).toBe(false);
			expect(cancel.getAttribute('aria-disabled')).toBeNull();
		});
	});

	it('a backdrop click does not close the dialog', () => {
		const { comp, dialog } = setup();
		comp.show();
		dialog.click();
		expect(dialog.open).toBe(true);
	});

	it('returns focus to the opener on close (explicit and implicit)', () => {
		const { comp, opener } = setup();
		const other = document.createElement('button');
		document.body.appendChild(other);
		comp.show(other);
		comp.close();
		expect(document.activeElement).toBe(other);

		opener.focus();
		comp.show();
		screen.getByRole('button', { name: 'Cancel' }).click();
		expect(document.activeElement).toBe(opener);
	});

	it('exposes role and labelled-by / described-by', () => {
		const { dialog } = setup({ role: 'alertdialog', eyebrow: 'Deck 1', alert: alertSnip });
		expect(dialog.getAttribute('role')).toBe('alertdialog');
		const h2 = screen.getByRole('heading', { level: 2, name: 'Commit drafts', hidden: true });
		expect(dialog.getAttribute('aria-labelledby')).toBe(h2.id);
		const desc = document.getElementById(dialog.getAttribute('aria-describedby')!);
		expect(desc?.textContent).toContain('Body text');
		expect(dialog.textContent).toContain('Failed');
		expect(dialog.textContent).toContain('Deck 1');
	});

	it('defaults to role=dialog and the commit variant', () => {
		const { dialog } = setup();
		expect(dialog.getAttribute('role')).toBe('dialog');
		expect(dialog.classList.contains('dlg--commit')).toBe(true);
	});
});
