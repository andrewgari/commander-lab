// Home dashboard browser tests (page spec §1.5 states, §1.8 acceptance). The API is the mock in tests/e2e/mock.
import { test, expect, expectNoAxeViolations, hasHorizontalScroll, PAGE_VIEWPORTS, settleAnimations } from './fixtures';

const tiles = (page: import('@playwright/test').Page) => page.locator('ul.tiles > li');
const tile = (page: import('@playwright/test').Page, label: string) => tiles(page).filter({ hasText: label });

/** The tile's primary value, exactly (not the caption: a date or count there must not satisfy it). */
const value = (page: import('@playwright/test').Page, label: string) => tile(page, label).first().locator('.value');

test.describe('home dashboard', () => {
	test('§1.8 populated: six stat tiles carry values and recent decks show name, pips and status', async ({ page, mock }) => {
		await mock.set({ decksFixture: 'home', gamesFixture: 'some', profilePower: true });
		await page.goto('/');

		await expect(tiles(page)).toHaveCount(6);
		await expect(value(page, 'Decks')).toHaveText(/^3,?\s*$/);
		await expect(tile(page, 'Decks').first()).toContainText('2 built');
		await expect(value(page, 'Ready to play')).toHaveText(/^1\/2,?\s*$/);
		await expect(tile(page, 'Ready to play')).toContainText('10 cards to buy');
		await expect(value(page, 'Collection')).toHaveText(/^120,?\s*$/);
		await expect(tile(page, 'Collection')).toContainText('2 in the mail');
		await expect(value(page, 'Games')).toHaveText(/^2,?\s*$/);
		await expect(tile(page, 'Games')).toContainText('1–1');
		await expect(value(page, 'Open drafts')).toHaveText(/^1,?\s*$/);
		await expect(tile(page, 'Open drafts').getByRole('link', { name: 'Atraxa rebuild' })).toHaveAttribute('href', '/drafts/draft-1');
		await expect(value(page, 'Power level')).toHaveText(/^7\/10,?\s*$/);
		await expect(tile(page, 'Power level')).toContainText('Bracket 3 (Upgraded)');
		await expect(page.getByRole('alert')).toHaveCount(0);

		// Recent decks: the games drive the order (newest first), and each row shows name, pips and status.
		await expect(page.getByRole('heading', { level: 2, name: 'Recently played' })).toBeVisible();
		const rows = page.locator('ul.deck-list > li');
		await expect(rows).toHaveCount(2);
		await expect(rows.nth(0).getByRole('link', { name: 'Elf Ball', exact: true })).toHaveAttribute('href', '/decks/archidekt%3A1002');
		await expect(rows.nth(1).getByRole('link', { name: 'Sliver Swarm', exact: true })).toBeVisible();
		await expect(rows.nth(0)).toContainText('Built');
		await expect(rows.nth(0)).toContainText('played');
		// Pips are one labelled image per deck (Elf Ball is mono-green, Sliver Swarm five-colour).
		await expect(rows.nth(0).getByRole('img', { name: /green/i })).toBeVisible();
		await expect(rows.nth(1).getByRole('img', { name: /white/i })).toBeVisible();
	});

	test('§1.5 one failing source: only its tile errors, Try again reloads, the others are untouched', async ({ page, mock }) => {
		await mock.set({ decksFixture: 'home', gamesFixture: 'some', profilePower: true, gamesStatus: 500 });
		await page.goto('/');

		await expect(tiles(page)).toHaveCount(6);
		await expect(tile(page, 'Games')).toContainText("Couldn't load");
		for (const label of ['Decks', 'Ready to play', 'Collection', 'Open drafts', 'Power level']) {
			await expect(tile(page, label).first(), label).not.toContainText("Couldn't load");
		}
		await expect(value(page, 'Collection')).toHaveText(/^120,?\s*$/);
		const banner = page.getByRole('alert').filter({ hasText: 'Some figures couldn’t load' });
		await expect(banner).toBeVisible();
		await expect(banner).toContainText('Games');
		await expect(page.getByRole('alert')).toHaveCount(1);
		// The recent-decks list does not depend on the games source, so it falls back to "Your decks".
		await expect(page.getByRole('heading', { level: 2, name: 'Your decks' })).toBeVisible();

		await mock.set({ gamesStatus: 200 });
		await banner.getByRole('button', { name: 'Try again' }).click();

		await expect(tile(page, 'Games')).toContainText('1–1');
		await expect(page.getByRole('alert')).toHaveCount(0);
		await expect(value(page, 'Collection')).toHaveText(/^120,?\s*$/);
		await expect(value(page, 'Decks')).toHaveText(/^3,?\s*$/);
		await expect(page.getByRole('heading', { level: 2, name: 'Recently played' })).toBeVisible();
	});

	test('§1.5 the decks source failing errors both deck tiles and the deck list, not the rest', async ({ page, mock }) => {
		await mock.set({ decksStatus: 500, gamesFixture: 'some' });
		await page.goto('/');

		await expect(tile(page, 'Decks').first()).toContainText("Couldn't load");
		await expect(tile(page, 'Ready to play')).toContainText("Couldn't load");
		await expect(value(page, 'Collection')).toHaveText(/^120,?\s*$/);
		await expect(page.getByRole('alert').filter({ hasText: "Decks couldn't load" }).first()).toBeVisible();
		await expect(page.getByRole('button', { name: 'Try again' }).first()).toBeVisible();
	});

	test('§1.5 every source down shows one page-level error with Try again', async ({ page, mock }) => {
		await mock.set({ down: true });
		await page.goto('/');

		await expect(page.getByRole('alert')).toContainText('API is not responding');
		await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
		await expect(tiles(page)).toHaveCount(0);
	});

	test('§1.5 all empty: empty states in every tile, an import prompt, and no crash', async ({ page, mock }) => {
		await mock.set({ decksFixture: 'empty', inventoryEmpty: true, gamesFixture: 'none', draftsListEmpty: true, profilePower: false });
		const errors: string[] = [];
		page.on('pageerror', (e) => errors.push(e.message));
		await page.goto('/');

		await expect(tiles(page)).toHaveCount(6);
		await expect(tile(page, 'Decks').first()).toContainText('No decks yet');
		await expect(tile(page, 'Ready to play')).toContainText('No decks yet');
		await expect(tile(page, 'Collection')).toContainText('No cards yet');
		await expect(tile(page, 'Games')).toContainText('No games yet');
		await expect(tile(page, 'Games').getByRole('link')).toHaveAttribute('href', '/games?import=1');
		await expect(tile(page, 'Open drafts')).toContainText('None open');
		await expect(tile(page, 'Power level')).toContainText('Not computed yet');
		await expect(page.getByRole('heading', { name: 'Import a deck to get started' })).toBeVisible();
		await expect(page.getByRole('alert')).toHaveCount(0);
		await expect(page.getByRole('heading', { level: 1, name: 'Commander Lab' })).toBeAttached();
		expect(errors).toEqual([]);
	});

	for (const vp of PAGE_VIEWPORTS) {
		test(`§1.8 axe finds no violations and nothing scrolls sideways at ${vp.width}px`, async ({ page, mock }) => {
			await mock.set({ decksFixture: 'home', gamesFixture: 'some', profilePower: true });
			await page.setViewportSize(vp);
			await page.goto('/');
			await expect(tiles(page)).toHaveCount(6);
			await settleAnimations(page);
			await expectNoAxeViolations(page);
			expect(await hasHorizontalScroll(page), 'page scrolls sideways').toBe(false);
		});

		test(`§1.8 axe finds no violations in the partial-failure and empty states at ${vp.width}px`, async ({ page, mock }) => {
			await page.setViewportSize(vp);
			await mock.set({ gamesStatus: 500 });
			await page.goto('/');
			await expect(page.getByRole('alert').first()).toBeVisible();
			await settleAnimations(page);
			await expectNoAxeViolations(page);
			expect(await hasHorizontalScroll(page), 'error state scrolls sideways').toBe(false);

			await mock.set({ gamesStatus: 200, decksFixture: 'empty', inventoryEmpty: true, draftsListEmpty: true });
			await page.goto('/');
			await expect(tile(page, 'Collection')).toContainText('No cards yet');
			await settleAnimations(page);
			await expectNoAxeViolations(page);
			expect(await hasHorizontalScroll(page), 'empty state scrolls sideways').toBe(false);
		});
	}
});
