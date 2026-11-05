import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import Page from './+page.svelte';

function makeData(overrides: Partial<{
	deckId: string;
	print: false;
	list: { ok: boolean; value: { documents: any[]; active: any | null } | null; reason: string | null };
	row: { ok: boolean; value: any | null; reason: string | null };
	feedback: { ok: boolean; value: Record<string, any> | null; reason: string | null };
	document: any | null;
}> = {}) {
	return {
		deckId: 'test-deck',
		print: false,
		list: { ok: true, value: { documents: [], active: null }, reason: null },
		row: { ok: true, value: null, reason: null },
		feedback: { ok: true, value: {}, reason: null },
		document: null,
		...overrides
	} as any;
}

function makeActiveRun(id = 1) {
	return {
		id,
		deck_id: 'test-deck',
		status: 'running' as const,
		stage: 'analyzing',
		error: null,
		grade: null,
		headline: null,
		created_at: '2026-10-29T14:00:00Z',
		started_at: '2026-10-29T14:00:00Z',
		finished_at: null
	};
}

function makeFailedRun(id = 2) {
	return {
		id,
		deck_id: 'test-deck',
		status: 'failed' as const,
		stage: 'done',
		error: 'boom',
		grade: null,
		headline: null,
		created_at: '2026-10-29T14:05:00Z',
		started_at: '2026-10-29T14:05:00Z',
		finished_at: '2026-10-29T14:10:00Z'
	};
}

describe('Review page generate() guard', () => {
	beforeEach(() => {
		vi.stubGlobal('fetch', vi.fn());
	});

	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it('does not POST when active run exists', async () => {
		const fetchSpy = vi.fn();
		vi.stubGlobal('fetch', fetchSpy);

		const { getByRole, getByText } = render(Page as any, {
			data: makeData({
				list: { ok: true, value: { documents: [], active: makeActiveRun() }, reason: null }
			})
		});

		// The generate button should be disabled (aria-disabled=true) when active exists
		const btn = getByRole('button', { name: /generate review/i });
		expect(btn.getAttribute('aria-disabled')).toBe('true');
		expect(getByText('A review is already running')).toBeTruthy();

		// Click should not trigger fetch
		await userEvent.click(btn);
		expect(fetchSpy).not.toHaveBeenCalled();
	});

	it('does not POST when an active run is shown (shownActive derived from lastActive)', async () => {
		const fetchSpy = vi.fn();
		vi.stubGlobal('fetch', fetchSpy);

		// With a failed run being viewed AND an active run in the list (shownActive = active)
		const activeRun = makeActiveRun(5);
		const failedRun = makeFailedRun(6);

		const { getByRole, getByText } = render(Page as any, {
			data: makeData({
				list: { ok: true, value: { documents: [failedRun, activeRun], active: activeRun }, reason: null },
				row: { ok: true, value: failedRun, reason: null }
			})
		});

		// The "Try again" button in the failed panel should be disabled when active exists
		const btn = getByRole('button', { name: /try again: start a new review/i });
		expect(btn.getAttribute('aria-disabled')).toBe('true');

		await userEvent.click(btn);
		expect(fetchSpy).not.toHaveBeenCalled();
	});

	it('POSTs when no active run and not busy', async () => {
		const fetchSpy = vi.fn().mockResolvedValue({
			ok: true,
			json: () => Promise.resolve({})
		});
		vi.stubGlobal('fetch', fetchSpy);

		const { getAllByRole } = render(Page as any, {
			data: makeData({
				list: { ok: true, value: { documents: [], active: null }, reason: null }
			})
		});

		// Use the form submit button (not the EmptyState CTA button)
		// The form submit button is the first "Generate review" button
		const buttons = getAllByRole('button', { name: /generate review/i });
		const formBtn = buttons[0]; // form submit button comes first in DOM
		expect(formBtn.hasAttribute('aria-disabled')).toBe(false);

		await userEvent.click(formBtn);
		await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));

		const call = fetchSpy.mock.calls[0];
		expect(call[0]).toContain('/review/generate');
		expect(call[1]?.method).toBe('POST');
	});
});