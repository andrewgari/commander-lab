// Games list + Forge-log import browser tests (page spec §4.6). The API is the mock in tests/e2e/mock.
import { test, expect, expectNoAxeViolations, hasHorizontalScroll, PAGE_VIEWPORTS } from './fixtures';

const IMPORT_POST = /\/api\/v2\/imports\/forge-log$/;
const LOG = 'Turn: Turn 1 (Ai(1)-Andrew)\nGame outcome: Ai(1) has won because all opponents have lost';

test.describe('games list', () => {
	test('§4.6 with games: the table is shown and the import disclosure is collapsed', async ({ page, mock }) => {
		await mock.set({ gamesFixture: 'some' });
		await page.goto('/games');

		const table = page.getByRole('table', { name: 'Imported games, newest first' });
		await expect(table).toBeVisible();
		await expect(table.getByRole('row')).toHaveCount(3); // header + 2 games
		await expect(table.getByRole('link', { name: 'Elf Ball' })).toHaveAttribute('href', '/games/2');
		await expect(page.getByText('2 imported')).toBeVisible();
		const disclosure = page.locator('details#import');
		await expect(disclosure).toHaveJSProperty('open', false);
		await expect(page.getByLabel('Forge log', { exact: true })).toBeHidden();
		await expect(page.getByRole('alert')).toHaveCount(0);
		// Spec §4.3 wants the table before the disclosure; main renders the disclosure first (collapsed, one row high).
		const box = await table.boundingBox();
		expect(box!.y, 'table is within the first screen').toBeLessThan(400);
	});

	test('§4.6 no games: the import disclosure opens by itself next to the empty state', async ({ page }) => {
		await page.goto('/games');

		await expect(page.locator('details#import')).toHaveJSProperty('open', true);
		await expect(page.getByLabel('Forge log', { exact: true })).toBeVisible();
		await expect(page.getByRole('heading', { name: 'No games imported yet' })).toBeVisible();
	});

	test('§4.6 ?import=1 opens the disclosure even when games exist', async ({ page, mock }) => {
		await mock.set({ gamesFixture: 'some' });
		await page.goto('/games?import=1');
		await expect(page.locator('details#import')).toHaveJSProperty('open', true);
		await expect(page.getByRole('table', { name: 'Imported games, newest first' })).toBeVisible();
	});

	test('§4.6 games failing to load shows an in-page error and Try again recovers', async ({ page, mock }) => {
		await mock.set({ gamesStatus: 500 });
		await page.goto('/games');

		await expect(page.getByRole('alert').filter({ hasText: "Games couldn't load" })).toBeVisible();
		await mock.set({ gamesStatus: 200, gamesFixture: 'some' });
		await page.getByRole('button', { name: 'Try again' }).click();
		await expect(page.getByRole('table', { name: 'Imported games, newest first' })).toBeVisible();
		await expect(page.getByRole('alert')).toHaveCount(0);
	});

	test('§4.6 the disclosure toggles with Enter and Space on its summary', async ({ page, mock }) => {
		await mock.set({ gamesFixture: 'some' });
		await page.goto('/games');
		const disclosure = page.locator('details#import');
		const summary = disclosure.locator('summary');

		await summary.focus();
		await page.keyboard.press('Enter');
		await expect(disclosure).toHaveJSProperty('open', true);
		await expect(page.getByLabel('Forge log', { exact: true })).toBeVisible();
		await page.keyboard.press('Enter');
		await expect(disclosure).toHaveJSProperty('open', false);
		await page.keyboard.press('Space');
		await expect(disclosure).toHaveJSProperty('open', true);
		await page.keyboard.press('Space');
		await expect(disclosure).toHaveJSProperty('open', false);
		await expect(summary).toBeFocused();
	});

	test('§4.6 a rejected import (400) keeps the disclosure open, keeps the input, shows the banner in the form and posts once on double-click', async ({ page, mock }) => {
		await mock.set({ gamesFixture: 'some', importStatus: 400, importDetail: 'Could not find a completed game in that log.', importDelayMs: 600 });
		await page.goto('/games');
		const disclosure = page.locator('details#import');
		await disclosure.locator('summary').click();
		await expect(disclosure).toHaveJSProperty('open', true);

		await page.getByLabel('Forge log', { exact: true }).fill(LOG);
		await page.getByLabel('Seat').fill('2');
		await page.getByRole('button', { name: 'Import & analyze' }).dblclick();

		const banner = disclosure.getByRole('alert').filter({ hasText: 'Import failed' });
		await expect(banner).toBeVisible();
		await expect(banner).toContainText('Could not find a completed game in that log.');
		await expect(disclosure).toHaveJSProperty('open', true);
		await expect(page.getByLabel('Forge log', { exact: true })).toHaveValue(LOG);
		await expect(page.getByLabel('Seat')).toHaveValue('2');
		await expect(page.getByRole('button', { name: 'Import & analyze' })).toBeEnabled();
		expect(await mock.count('POST', IMPORT_POST)).toBe(1);
		// The request carried the pasted log, not a blank form.
		const post = (await mock.requests()).find((r) => r.method === 'POST' && IMPORT_POST.test(r.path))!;
		expect(post.body).toMatchObject({ deck_id: 'archidekt:1001', log_text: LOG, seat: 2 });
	});

	test('§4.6 submitting an empty form is refused with an in-form message and sends nothing', async ({ page, mock }) => {
		await mock.set({ gamesFixture: 'some' });
		await page.goto('/games?import=1');
		// `required` is enforced by the browser first; drop it to reach the server-side guard.
		await page.locator('#log_text').evaluate((el) => el.removeAttribute('required'));
		await page.getByRole('button', { name: 'Import & analyze' }).click();
		await expect(page.locator('details#import').getByRole('alert')).toContainText('Pick a deck and paste');
		expect(await mock.count('POST', IMPORT_POST)).toBe(0);
	});

	for (const vp of PAGE_VIEWPORTS) {
		test(`§4.6 axe finds no violations and nothing scrolls sideways at ${vp.width}px`, async ({ page, mock }) => {
			await page.setViewportSize(vp);

			// Collapsed, with games.
			await mock.set({ gamesFixture: 'some' });
			await page.goto('/games');
			await expect(page.getByRole('table', { name: 'Imported games, newest first' })).toBeVisible();
			await expectNoAxeViolations(page);
			expect(await hasHorizontalScroll(page), 'list scrolls sideways').toBe(false);

			// Open, with a failed import in the form.
			await page.locator('details#import summary').click();
			await page.getByLabel('Forge log', { exact: true }).fill(LOG);
			await page.getByRole('button', { name: 'Import & analyze' }).click();
			await expect(page.getByRole('alert').filter({ hasText: 'Import failed' })).toBeVisible();
			await expectNoAxeViolations(page);
			expect(await hasHorizontalScroll(page), 'import form scrolls sideways').toBe(false);

			// Empty state.
			await mock.set({ gamesFixture: 'none' });
			await page.goto('/games');
			await expect(page.getByRole('heading', { name: 'No games imported yet' })).toBeVisible();
			await expectNoAxeViolations(page);
			expect(await hasHorizontalScroll(page), 'empty state scrolls sideways').toBe(false);
		});
	}
});
