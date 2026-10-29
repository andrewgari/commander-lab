// Profile editor browser tests (page spec §3.8). The API is the mock in tests/e2e/mock.
import { test, expect, expectNoAxeViolations, VIEWPORTS } from './fixtures';

test.describe('profile editor', () => {
	test.beforeEach(async ({ page }) => {
		await page.goto('/profile');
		await expect(page.getByRole('button', { name: 'Save changes' })).toBeVisible();
	});

	test('§3.8.1 a no-edit save leaves every leaf with its provenance', async ({ page, mock }) => {
		const before = await mock.state();
		expect(Object.keys(before.provenance).length).toBeGreaterThan(3);

		await page.getByRole('button', { name: 'Save changes' }).click();
		await expect(page.getByText('Saved. Your edits are recorded as Stated.').first()).toBeVisible();

		const after = await mock.state();
		expect(after.provenance).toEqual(before.provenance);
		expect(after.profile).toEqual(before.profile);
		expect(await mock.count('PUT', /\/profile\/player\/andrew$/)).toBe(1);
	});

	test('§3.8.2 editing one learned item makes only that key stated', async ({ page, mock }) => {
		const before = await mock.state();
		expect(before.provenance['archetype_affinities.equipment'].source).toBe('learned_from_feedback');

		const weights = page.locator('[data-field="archetype_affinities"]');
		await weights.locator('input[type="number"]').first().fill('0.4');
		await page.getByRole('button', { name: 'Save changes' }).click();
		await expect(page.getByText('Saved. Your edits are recorded as Stated.').first()).toBeVisible();

		const after = await mock.state();
		expect(after.provenance['archetype_affinities.equipment'].source).toBe('stated');
		for (const [key, prov] of Object.entries(before.provenance)) {
			if (key === 'archetype_affinities.equipment') continue;
			expect(after.provenance[key], key).toEqual(prov);
		}
	});

	test('§3.8.5 client validation blocks soft > hard and sends nothing', async ({ page, mock }) => {
		await page.getByLabel('Hard cap').fill('50');
		await page.getByLabel('Soft cap').fill('60');
		await page.getByRole('button', { name: 'Save changes' }).click();

		await expect(page.getByLabel('Soft cap')).toHaveAttribute('aria-invalid', 'true');
		await expect(page.getByRole('alert').filter({ hasText: 'Fix 1 field before saving' })).toBeVisible();
		expect(await mock.count('PUT', /\/profile\/player\/andrew$/)).toBe(0);
	});

	test('§3.8.5 a server 422 is shown on its field and values are kept', async ({ page, mock }) => {
		await mock.set({ putStatus: 422, putDetail: 'budget_norms.soft_cap (60.0) cannot exceed hard_cap (50.0).' });
		await page.getByLabel('Power notes').fill('Keep it casual');
		await page.getByRole('button', { name: 'Save changes' }).click();

		await expect(page.getByText(/cannot exceed hard_cap/).first()).toBeVisible();
		await expect(page.getByLabel('Power notes')).toHaveValue('Keep it casual');
	});

	for (const vp of VIEWPORTS) {
		test(`§3.8.8 axe finds no violations at ${vp.width}px`, async ({ page }) => {
			await page.setViewportSize(vp);
			await expectNoAxeViolations(page);
		});
	}

	test('§3.8.8 list items can be added and removed with the keyboard', async ({ page }) => {
		const field = page.locator('[data-field="favored_mechanics"]');
		await field.getByRole('button', { name: /add/i }).focus();
		await page.keyboard.press('Enter');
		await expect(field.getByLabel('Favoured mechanics item 1')).toBeFocused();
		await page.keyboard.type('landfall');
		await page.keyboard.press('Tab');
		await expect(field.getByRole('button', { name: 'Remove landfall from Favoured mechanics' })).toBeFocused();
		await page.keyboard.press('Enter');
		await expect(field.getByLabel('Favoured mechanics item 1')).toHaveCount(0);
	});
});
