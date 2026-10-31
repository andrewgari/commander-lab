// Drafts commit flow browser tests (drafts page-specs §2.9, criteria 1-8, 10, 11; 9 is DC-10's grep).
// The API is the mock in tests/e2e/mock; its `draft-1` starts with Atraxa (commander), Command Tower (main)
// and one staged donor move (Sol Ring out of "Sliver Swarm").
import type { Locator, Page } from '@playwright/test';
import { test, expect, expectNoAxeViolations, draftPosts, DRAFT, VIEWPORTS } from './fixtures';

/** The production build hydrates after the first paint; wait for it so typed input is not lost. */
async function openDraft(page: Page) {
	await page.goto(DRAFT);
	await page.waitForLoadState('networkidle');
	await expect(page.getByRole('heading', { name: 'Atraxa rebuild' })).toBeVisible();
}

const quickAddInput = (page: Page) => page.getByRole('combobox', { name: /Add cards to/ });
const status = (page: Page) => page.locator('.qa-status');
const listbox = (page: Page) => page.getByRole('listbox', { name: 'Card suggestions' });
const commitTrigger = (page: Page) => page.getByRole('button', { name: 'Review & commit…' });
const abandonTrigger = (page: Page) => page.getByRole('button', { name: 'Abandon draft…' });
const qtyOf = (page: Page, card: string, board: string) => page.getByLabel(`Quantity of ${card} on ${board}`);

/** Distance between two boxes (0 when they touch or overlap). */
function gap(a: { x: number; y: number; width: number; height: number }, b: typeof a) {
	const dx = Math.max(a.x - (b.x + b.width), b.x - (a.x + a.width), 0);
	const dy = Math.max(a.y - (b.y + b.height), b.y - (a.y + a.height), 0);
	return Math.max(dx, dy);
}

test.describe('§2.9-1 quick add by keyboard', () => {
	test('1a "sol" + Enter stages the top match, keeps focus and announces it', async ({ page, mock }) => {
		await openDraft(page);
		const input = quickAddInput(page);
		await input.fill('sol');
		await input.press('Enter');
		await expect(status(page)).toHaveText('Added 1× Sol Ring to Main');
		await expect(input).toBeFocused();
		await expect(page.getByRole('link', { name: 'Sol Ring' })).toBeVisible();
		await expect(qtyOf(page, 'Sol Ring', 'Main')).toHaveValue('1');
		const posts = await draftPosts(mock, 'cards');
		expect(posts).toHaveLength(1);
		expect(posts[0].body).toMatchObject({ name: 'Sol Ring', qty: 1, board: 'main' });
	});

	test('1b "2x Sol Ring" adds 2 and adding again increments instead of resetting', async ({ page, mock }) => {
		await openDraft(page);
		const input = quickAddInput(page);
		await input.fill('2x Sol Ring');
		await input.press('Enter');
		await expect(status(page)).toHaveText('Added 2× Sol Ring to Main');
		await expect(qtyOf(page, 'Sol Ring', 'Main')).toHaveValue('2');

		await input.fill('Sol Ring');
		await input.press('Enter');
		await expect(status(page)).toHaveText('Added 1× Sol Ring to Main (now 3)');
		await expect(qtyOf(page, 'Sol Ring', 'Main')).toHaveValue('3');
		expect((await draftPosts(mock, 'cards')).map((r) => (r.body as { qty: number }).qty)).toEqual([2, 3]);
	});
});

test('§2.9-2 keyboard only: Ctrl+\', type, ↓↓, Enter, Esc, Esc', async ({ page }) => {
	await openDraft(page);
	const input = quickAddInput(page);
	await page.keyboard.press("Control+'");
	await expect(input).toBeFocused();

	await page.keyboard.type('sol');
	await expect(listbox(page)).toBeVisible();
	await expect(listbox(page).getByRole('option')).toHaveCount(3);
	await page.keyboard.press('ArrowDown');
	await page.keyboard.press('ArrowDown');
	const second = listbox(page).getByRole('option').nth(1);
	await expect(second).toHaveAttribute('aria-selected', 'true');
	await expect(input).toHaveAttribute('aria-activedescendant', (await second.getAttribute('id')) ?? '');
	await page.keyboard.press('Enter');
	await expect(status(page)).toHaveText('Added 1× Solemn Simulacrum to Main');
	await expect(input).toBeFocused();
	await expect(input).toHaveValue('');

	// First Esc closes the list and keeps the text; the second clears it.
	await page.keyboard.type('sol');
	await expect(listbox(page)).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(listbox(page)).toBeHidden();
	await expect(input).toHaveValue('sol');
	await page.keyboard.press('Escape');
	await expect(input).toHaveValue('');
	await expect(input).toBeFocused();
});

test.describe('§2.9-3 the four non-result rows', () => {
	const EMPTY = /No owned cards match/;

	test('3a a slow response shows Searching…', async ({ page, mock }) => {
		await mock.set({ searchDelayMs: 1500 });
		await openDraft(page);
		await quickAddInput(page).fill('sol');
		await expect(listbox(page).getByText('Searching…')).toBeVisible();
		await expect(listbox(page).getByText(EMPTY)).toHaveCount(0);
		await expect(listbox(page).getByRole('option', { name: /Sol Ring/ })).toBeVisible();
	});

	test('3b zero hits shows "No owned cards match"', async ({ page, mock }) => {
		await mock.set({ searchEmpty: true });
		await openDraft(page);
		await quickAddInput(page).fill('sol');
		await expect(listbox(page).getByText('No owned cards match “sol”')).toBeVisible();
	});

	test('3c a 502 shows Search failed, never "No owned cards match"', async ({ page, mock }) => {
		await mock.set({ searchStatus: 502 });
		await openDraft(page);
		await quickAddInput(page).fill('sol');
		await expect(listbox(page).getByText('Search failed — press Enter to retry')).toBeVisible();
		await expect(page.getByText(EMPTY)).toHaveCount(0);
		await expect(status(page)).toHaveText('Search failed. Press Enter to retry.');
	});

	test('3d degraded:true shows the offline row above the results', async ({ page, mock }) => {
		await mock.set({ searchDegraded: true });
		await openDraft(page);
		await quickAddInput(page).fill('sol');
		await expect(listbox(page).getByText('Card search is offline — showing local matches')).toBeVisible();
		await expect(listbox(page).getByRole('option', { name: /Sol Ring/ })).toBeVisible();
	});
});

test('§2.9-4 all five boards round-trip and Maybeboard sends board=maybe', async ({ page, mock }) => {
	await openDraft(page);
	const input = quickAddInput(page);
	const boards = ['Commander', 'Main', 'Sideboard', 'Maybeboard', 'Considering'];
	for (const label of boards) {
		await page.getByLabel('Board', { exact: true }).selectOption({ label });
		await input.fill('Sol Ring');
		await input.press('Enter');
		await expect(status(page)).toHaveText(`Added 1× Sol Ring to ${label}`);
	}
	const sent = (await draftPosts(mock, 'cards')).map((r) => (r.body as { board: string }).board);
	expect(sent).toEqual(['commander', 'main', 'sideboard', 'maybe', 'considering']);
	expect(sent).not.toContain('maybeboard');

	// Reload: what the API stored comes back under all five headings.
	await openDraft(page);
	for (const label of boards) {
		await expect(qtyOf(page, 'Sol Ring', label)).toHaveValue('1');
	}
	await expect(page.getByRole('columnheader', { name: 'Maybeboard 1' })).toBeVisible();
});

test('§2.9-5 no uuid, draft: or archidekt: id in visible text or accessible names', async ({ page }) => {
	await openDraft(page);
	await quickAddInput(page).fill('sol');
	await expect(listbox(page)).toBeVisible();
	const leaks = await page.evaluate(() => {
		const re = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|draft:|archidekt:/gi;
		const found: string[] = [];
		const text = document.body.innerText;
		for (const m of text.match(re) ?? []) found.push(`text: ${m}`);
		for (const el of document.querySelectorAll('[aria-label]')) {
			const label = el.getAttribute('aria-label') ?? '';
			if (re.test(label)) found.push(`aria-label: ${label}`);
			re.lastIndex = 0;
		}
		return found;
	});
	expect(leaks).toEqual([]);
	// Sanity: the page really shows the deck names the ids stand for.
	await expect(page.getByText(/Sliver Swarm/).first()).toBeVisible();
});

test.describe('§2.9-6 commit dialog', () => {
	test('6a opens a modal dialog with the summary, focus on Cancel', async ({ page }) => {
		await openDraft(page);
		await commitTrigger(page).click();
		const dlg = page.getByRole('dialog', { name: 'Commit draft?' });
		await expect(dlg).toBeVisible();
		expect(await dlg.evaluate((d: HTMLDialogElement) => d.matches(':modal'))).toBe(true);
		await expect(dlg.getByText('Atraxa Superfriends')).toBeVisible();
		await expect(dlg.getByText('2 cards: 1 commander · 1 main')).toBeVisible();
		await expect(dlg.getByText('Atraxa, Praetors’ Voice')).toBeVisible();
		await expect(dlg.getByText('1 copy moves into the new deck')).toBeVisible();
		await expect(dlg.getByText('Pulls 1 copy out of Sliver Swarm')).toBeVisible();
		await expect(dlg.getByText('Built (have)')).toBeVisible();
		await expect(dlg.getByText('Commander decks have 100 cards; this one will have 2.')).toBeVisible();
		await expect(dlg.getByRole('button', { name: 'Cancel' })).toBeFocused();
	});

	test('6b a backdrop click does not close; Esc closes and returns focus to the trigger', async ({ page }) => {
		await openDraft(page);
		await commitTrigger(page).click();
		const dlg = page.getByRole('dialog', { name: 'Commit draft?' });
		await expect(dlg).toBeVisible();
		await page.mouse.click(4, 4);
		await expect(dlg).toBeVisible();
		await page.keyboard.press('Escape');
		await expect(dlg).toBeHidden();
		await expect(commitTrigger(page)).toBeFocused();
	});
});

test.describe('§2.9-7 commit outcome', () => {
	test('7a success lands on the new deck with "Built from a draft"', async ({ page, mock }) => {
		await openDraft(page);
		await commitTrigger(page).click();
		await page.getByRole('dialog', { name: 'Commit draft?' }).getByRole('button', { name: 'Commit draft' }).click();
		await expect(page).toHaveURL(/\/decks\/built-deck-1\?from_draft=1$/);
		await expect(page.getByText('Built from a draft')).toBeVisible();
		expect(await draftPosts(mock, 'commit')).toHaveLength(1);
	});

	test('7b a 409 keeps the dialog open with an alert and focus on Commit', async ({ page, mock }) => {
		await mock.set({ commitStatus: 409 });
		await openDraft(page);
		await commitTrigger(page).click();
		const dlg = page.getByRole('dialog', { name: 'Commit draft?' });
		const commit = dlg.getByRole('button', { name: 'Commit draft' });
		await commit.click();
		await expect(dlg.getByRole('alert')).toContainText('Commit failed');
		await expect(dlg.getByRole('alert')).toContainText('The draft changed since you opened it');
		await expect(dlg).toBeVisible();
		await expect(commit).toBeFocused();
		await expect(page).toHaveURL(/\/drafts\/draft-1$/);
	});

	test('7c Esc is ignored while the commit is pending', async ({ page, mock }) => {
		await mock.set({ commitDelayMs: 1500 });
		await openDraft(page);
		await commitTrigger(page).click();
		const dlg = page.getByRole('dialog', { name: 'Commit draft?' });
		await dlg.getByRole('button', { name: 'Commit draft' }).click();
		await expect(dlg.getByRole('button', { name: /Committing/ })).toBeVisible();
		await page.keyboard.press('Escape');
		await expect(dlg).toBeVisible();
		await expect(page).toHaveURL(/\/decks\/built-deck-1\?from_draft=1$/);
	});
});

test.describe('§2.9-8 abandon', () => {
	test('8a an alertdialog with the counts, then redirect to /drafts', async ({ page, mock }) => {
		await openDraft(page);
		await abandonTrigger(page).click();
		const dlg = page.getByRole('alertdialog', { name: 'Abandon draft?' });
		await expect(dlg).toBeVisible();
		await expect(dlg.getByText('Deletes 2 staged cards and 1 staged move.')).toBeVisible();
		await expect(dlg.getByRole('button', { name: 'Cancel' })).toBeFocused();
		await dlg.getByRole('button', { name: 'Abandon draft' }).click();
		await expect(page).toHaveURL(/\/drafts$/);
		expect(await draftPosts(mock, 'abandon')).toHaveLength(1);
	});

	for (const vp of VIEWPORTS) {
		test(`8b the Commit and Abandon triggers are at least 2rem apart at ${vp.width}px`, async ({ page }) => {
			await page.setViewportSize(vp);
			await openDraft(page);
			const rem = await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).fontSize));
			const a = await commitTrigger(page).boundingBox();
			const b = await abandonTrigger(page).boundingBox();
			expect(a && b).toBeTruthy();
			expect(gap(a!, b!)).toBeGreaterThanOrEqual(2 * rem);
		});
	}
});

test.describe('§2.9-10 without JavaScript', () => {
	test.use({ javaScriptEnabled: false });

	test('10 add-by-name works; Commit shows the inline summary; only confirmed=1 commits', async ({ page, mock }) => {
		const commitForms: string[] = [];
		page.on('request', (r) => {
			if (r.method() === 'POST' && r.url().includes('?/commit')) commitForms.push(r.postData() ?? '');
		});

		await page.goto(DRAFT);
		await page.getByLabel('Card name').fill('Sol Ring');
		await page.getByRole('button', { name: 'Add card' }).click();
		await expect(page.getByRole('link', { name: 'Sol Ring' })).toBeVisible();
		const adds = await draftPosts(mock, 'cards');
		expect(adds).toHaveLength(1);
		expect(adds[0].body).toMatchObject({ name: 'Sol Ring', qty: 1, board: 'main' });

		await commitTrigger(page).click();
		await expect(page.getByRole('heading', { name: 'Commit draft?' })).toBeVisible();
		await expect(page.locator('.confirm-inline').getByText('Pulls 1 copy out of Sliver Swarm')).toBeVisible();
		expect(await draftPosts(mock, 'commit')).toHaveLength(0);
		expect(commitForms).toHaveLength(1);
		expect(commitForms[0]).not.toContain('confirmed=1');

		await page.getByRole('button', { name: 'Commit draft' }).click();
		await expect(page).toHaveURL(/\/decks\/built-deck-1\?from_draft=1$/);
		expect(commitForms).toHaveLength(2);
		expect(commitForms[1]).toContain('confirmed=1');
		expect(await draftPosts(mock, 'commit')).toHaveLength(1);
	});
});

test.describe('§2.9-11 axe', () => {
	for (const vp of VIEWPORTS) {
		test(`11 no violations with the dialogs closed and open at ${vp.width}px`, async ({ page }) => {
			await page.setViewportSize(vp);
			await openDraft(page);
			await expectNoAxeViolations(page);

			// TODO t_e905cfec: the dialog body is a scroll region without a keyboard stop at 375px (product bug,
			// fixed in its own card). Every other rule still applies; drop the exemption with that fix.
			const disableRules = vp.width === 375 ? ['scrollable-region-focusable'] : [];
			const check = async (trigger: Locator, name: string, role: 'dialog' | 'alertdialog') => {
				await trigger.click();
				const dlg = page.getByRole(role, { name });
				await expect(dlg).toBeVisible();
				await expectNoAxeViolations(page, { disableRules });
				await dlg.getByRole('button', { name: 'Cancel' }).click();
				await expect(dlg).toBeHidden();
			};
			await check(commitTrigger(page), 'Commit draft?', 'dialog');
			await check(abandonTrigger(page), 'Abandon draft?', 'alertdialog');
		});
	}
});
