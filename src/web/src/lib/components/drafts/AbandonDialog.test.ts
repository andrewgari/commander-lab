import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import { tick } from 'svelte';

type Submit = (input: unknown) => Promise<((o: { result: unknown; update: () => Promise<void> }) => Promise<void>) | undefined>;
const hoisted = vi.hoisted(() => ({ submit: null as unknown }));

vi.mock('$app/forms', () => ({
	enhance: (_form: HTMLFormElement, fn: unknown) => {
		hoisted.submit = fn;
		return { destroy() {} };
	}
}));

import AbandonDialog from './AbandonDialog.svelte';

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

function setup() {
	const r = render(AbandonDialog, { draftName: 'Goblin Tribal', cards: [{ qty: 99 }, { qty: 1 }], moves: [{}, {}] });
	return { comp: r.component as unknown as { show(o?: HTMLElement): void }, dialog: document.querySelector('dialog') as HTMLDialogElement };
}

async function submit() {
	const cb = await (hoisted.submit as Submit)({
		action: new URL('http://x/drafts/1?/abandon'),
		formData: new FormData(),
		formElement: document.querySelector('form'),
		controller: new AbortController(),
		submitter: null,
		cancel: vi.fn()
	});
	const update = vi.fn(async () => {});
	return { update, finish: (result: unknown) => cb!({ result, update }) };
}

describe('AbandonDialog', () => {
	it('is an alertdialog with the consequence sentence', () => {
		const { comp, dialog } = setup();
		comp.show();
		expect(dialog.getAttribute('role')).toBe('alertdialog');
		expect(dialog.classList.contains('dlg--abandon')).toBe(true);
		expect(screen.getByRole('heading', { name: 'Abandon draft?', hidden: true })).toBeTruthy();
		expect(dialog.textContent).toContain('Draft · Goblin Tribal');
		expect(dialog.textContent).toContain('Deletes 100 staged cards and 2 staged moves. Your decks and collection are untouched.');
	});

	it('posts to ?/abandon with confirmed=1 via a danger button', () => {
		const { dialog } = setup();
		const form = dialog.querySelector('form') as HTMLFormElement;
		expect(form.getAttribute('method')?.toUpperCase()).toBe('POST');
		expect(form.getAttribute('action')).toBe('?/abandon');
		expect((form.querySelector('input[name="confirmed"]') as HTMLInputElement).value).toBe('1');
		const btn = form.querySelector('button[type="submit"]') as HTMLButtonElement;
		expect(btn.classList.contains('btn--danger')).toBe(true);
		expect(btn.textContent).toContain('Abandon draft');
		expect(btn.textContent).toContain('Abandoning…');
	});

	it('Cancel is aria-disabled while pending and the dialog stays open', async () => {
		const { comp, dialog } = setup();
		comp.show();
		const cancel = screen.getByRole('button', { name: 'Cancel', hidden: true });
		const s = await submit();
		await tick();
		expect(cancel.getAttribute('aria-disabled')).toBe('true');
		cancel.click();
		expect(dialog.open).toBe(true);
		await s.finish({ type: 'success', status: 200 });
		await tick();
		expect(cancel.getAttribute('aria-disabled')).toBeNull();
	});

	it('shows the failure reason and focuses the Abandon button', async () => {
		const { comp, dialog } = setup();
		comp.show();
		const s = await submit();
		await s.finish({ type: 'failure', status: 409, data: { actionError: 'Draft already committed' } });
		await tick();
		const alert = dialog.querySelector('.dlg__alert') as HTMLElement;
		expect(alert.textContent).toContain('Abandon failed');
		expect(alert.textContent).toContain('Draft already committed');
		expect(dialog.open).toBe(true);
		expect(document.activeElement).toBe(dialog.querySelector('form button[type="submit"]'));
	});
});
