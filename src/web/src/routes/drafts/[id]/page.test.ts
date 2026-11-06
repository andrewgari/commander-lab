import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';

vi.mock('$app/forms', () => ({
	enhance: () => ({ destroy() {} }),
	deserialize: vi.fn()
}));
vi.mock('$app/navigation', () => ({ invalidateAll: vi.fn(async () => {}) }));
vi.mock('$app/state', () => ({ page: { url: new URL('http://x/drafts/d1') } }));

import Page from './+page.svelte';

const card = { oracle_id: 'o1', name: 'Krenko, Mob Boss', qty: 1, board: 'commander', color_identity: ['R'], type_line: 'Legendary Creature', cmc: 4 };

function data(cards: unknown[]) {
	return {
		state: {
			draft: { id: 'd1', name: 'Goblins', status: 'open', kind: 'brew', target_name: 'Goblin Tribal', source_decks: [], notes: null, result_deck_id: null },
			cards,
			moves: []
		},
		suggestions: null,
		suggestionsError: 'n/a',
		deckNames: {}
	};
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const mount = (cards: unknown[], form: unknown = null) => render(Page as any, { data: data(cards), form });

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
	cleanup();
	delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).showModal;
	delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).close;
});

describe('drafts page commit bar', () => {
	it('blocked: trigger is aria-disabled, described by the reason, and does not open the dialog', async () => {
		mount([]);
		const trigger = screen.getByRole('button', { name: /Review & commit/ });
		expect(trigger.getAttribute('aria-disabled')).toBe('true');
		const reason = document.getElementById(trigger.getAttribute('aria-describedby') ?? '');
		expect(reason?.textContent).toBe('Stage at least one card to commit.');
		await fireEvent.click(trigger);
		expect(document.querySelector('dialog[open]')).toBeNull();
	});

	it('opens the commit dialog with focus on Cancel; closing returns focus to the trigger', async () => {
		mount([card]);
		const trigger = screen.getByRole('button', { name: /Review & commit/ });
		expect(trigger.getAttribute('aria-disabled')).toBeNull();
		expect(trigger.getAttribute('aria-describedby')).toBeNull();
		trigger.focus();
		await fireEvent.click(trigger);
		const dlg = document.querySelector('dialog[open]') as HTMLDialogElement;
		expect(dlg).not.toBeNull();
		expect(document.activeElement?.textContent).toBe('Cancel');
		dlg.close();
		expect(document.activeElement).toBe(trigger);
	});

	it('abandon trigger opens its dialog and is not adjacent to commit', async () => {
		mount([card]);
		const abandon = screen.getByRole('button', { name: /Abandon draft…/ });
		const forms = document.querySelectorAll('section.commit-bar > form');
		expect(forms).toHaveLength(2);
		expect(forms[0].contains(abandon)).toBe(false);
		abandon.focus();
		await fireEvent.click(abandon);
		expect(document.querySelector('dialog[open]')).not.toBeNull();
	});

	it('no-JS: needsConfirm=commit renders the inline confirm with confirmed=1', () => {
		mount([card], { needsConfirm: 'commit' });
		expect(screen.getByRole('heading', { name: 'Commit draft?', level: 2 })).toBeTruthy();
		const f = document.querySelector('section.confirm-inline form') as HTMLFormElement;
		expect(f.getAttribute('action')).toBe('?/commit');
		expect((f.querySelector('input[name=confirmed]') as HTMLInputElement).value).toBe('1');
		expect(screen.getByRole('link', { name: 'Cancel' }).getAttribute('href')).toBe('/drafts/d1');
	});

	it('no-JS: needsConfirm=abandon renders the abandon confirm', () => {
		mount([card], { needsConfirm: 'abandon' });
		expect(screen.getByRole('heading', { name: 'Abandon draft?', level: 2 })).toBeTruthy();
		const f = document.querySelector('section.confirm-inline form') as HTMLFormElement;
		expect(f.getAttribute('action')).toBe('?/abandon');
		expect((f.querySelector('input[name=confirmed]') as HTMLInputElement).value).toBe('1');
	});
});
