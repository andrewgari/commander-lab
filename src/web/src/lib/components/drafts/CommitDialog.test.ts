import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import { tick } from 'svelte';

type Submit = (input: unknown) => Promise<((o: { result: unknown; update: () => Promise<void> }) => Promise<void>) | undefined>;
const hoisted = vi.hoisted(() => ({ submit: null as unknown, invalidateAll: vi.fn(async () => {}) }));

// The real enhance would intercept the submit and fetch; capture the SubmitFunction instead.
vi.mock('$app/forms', () => ({
	enhance: (_form: HTMLFormElement, fn: unknown) => {
		hoisted.submit = fn;
		return { destroy() {} };
	}
}));
vi.mock('$app/navigation', () => ({ invalidateAll: hoisted.invalidateAll }));

import CommitDialog from './CommitDialog.svelte';
import { summarize } from '#lib/drafts/summary.ts';

const summary = summarize({
	draft: { target_name: 'Goblin Tribal' },
	cards: [{ oracle_id: 'o1', name: 'Krenko, Mob Boss', qty: 1, board: 'commander', color_identity: ['R'] }],
	moves: []
});

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
	hoisted.invalidateAll.mockClear();
});

function setup() {
	const r = render(CommitDialog, { draftName: 'Goblin Tribal', summary });
	const comp = r.component as unknown as { show(o?: HTMLElement): void };
	return { ...r, comp, dialog: document.querySelector('dialog') as HTMLDialogElement };
}

/** Run the form's SubmitFunction the way SvelteKit does; returns a finisher for the result. */
async function submit() {
	const cancel = vi.fn();
	const cb = await (hoisted.submit as Submit)({
		action: new URL('http://x/drafts/1?/commit'),
		formData: new FormData(),
		formElement: document.querySelector('form'),
		controller: new AbortController(),
		submitter: null,
		cancel
	});
	const update = vi.fn(async () => {});
	return { update, finish: (result: unknown) => cb!({ result, update }) };
}

describe('CommitDialog', () => {
	it('is a dialog titled "Commit draft?" with the draft eyebrow and the summary body', () => {
		const { comp, dialog } = setup();
		comp.show();
		expect(dialog.getAttribute('role')).toBe('dialog');
		expect(screen.getByRole('heading', { name: 'Commit draft?', hidden: true })).toBeTruthy();
		expect(dialog.textContent).toContain('Draft · Goblin Tribal');
		expect(dialog.querySelector('dl.sum')?.textContent).toContain('Creates Goblin Tribal');
		expect(dialog.classList.contains('dlg--commit')).toBe(true);
	});

	it('posts to ?/commit with confirmed=1', () => {
		const { dialog } = setup();
		const form = dialog.querySelector('form') as HTMLFormElement;
		expect(form.getAttribute('method')?.toUpperCase()).toBe('POST');
		expect(form.getAttribute('action')).toBe('?/commit');
		expect((form.querySelector('input[name="confirmed"]') as HTMLInputElement).value).toBe('1');
		const btn = form.querySelector('button[type="submit"]') as HTMLButtonElement;
		expect(btn.textContent).toContain('Commit draft');
		expect(btn.textContent).toContain('Committing…');
	});

	it('locks Cancel (aria-disabled) while the request is pending, then releases it', async () => {
		const { comp, dialog } = setup();
		comp.show();
		const cancel = screen.getByRole('button', { name: 'Cancel', hidden: true });
		expect(cancel.getAttribute('aria-disabled')).toBeNull();
		const s = await submit();
		await tick();
		expect(cancel.getAttribute('aria-disabled')).toBe('true');
		cancel.click();
		expect(dialog.open).toBe(true);
		const esc = new Event('cancel', { cancelable: true });
		dialog.dispatchEvent(esc);
		expect(esc.defaultPrevented).toBe(true);
		await s.finish({ type: 'success', status: 200 });
		await tick();
		expect(cancel.getAttribute('aria-disabled')).toBeNull();
		expect(s.update).toHaveBeenCalled();
	});

	it('shows the failure banner with the reason, keeps the dialog open and focuses Commit; no refresh for 422', async () => {
		const { comp, dialog } = setup();
		comp.show();
		const s = await submit();
		await s.finish({ type: 'failure', status: 422, data: { actionError: 'Name already taken' } });
		await tick();
		const alert = dialog.querySelector('.dlg__alert') as HTMLElement;
		expect(alert.textContent).toContain('Commit failed');
		expect(alert.textContent).toContain('Name already taken');
		expect(screen.queryByRole('button', { name: 'Close and refresh the draft', hidden: true })).toBeNull();
		expect(dialog.open).toBe(true);
		expect(document.activeElement).toBe(dialog.querySelector('form button[type="submit"]'));
		expect(s.update).toHaveBeenCalledWith({ reset: false });
	});

	it('offers "Close and refresh the draft" for stale failures; it closes and invalidates', async () => {
		const { comp, dialog } = setup();
		comp.show();
		const s = await submit();
		await s.finish({ type: 'failure', status: 409, data: { actionError: 'Draft changed' } });
		await tick();
		const btn = screen.getByRole('button', { name: 'Close and refresh the draft', hidden: true });
		btn.click();
		expect(dialog.open).toBe(false);
		expect(hoisted.invalidateAll).toHaveBeenCalledOnce();
	});

	it('shows a network message on transport error and clears the banner on next show()', async () => {
		const { comp, dialog } = setup();
		comp.show();
		const s = await submit();
		await s.finish({ type: 'error', status: 500, error: new Error('x') });
		await tick();
		expect(dialog.querySelector('.dlg__alert')?.textContent).toContain('Network or server error');
		comp.show();
		await tick();
		expect(dialog.querySelector('.dlg__alert')?.textContent ?? '').not.toContain('Commit failed');
	});
});
