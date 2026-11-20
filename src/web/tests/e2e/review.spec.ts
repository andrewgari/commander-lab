// Review page failure/retry states (page spec §5.4, acceptance §5.7). The API is the mock in tests/e2e/mock.
import { test, expect, expectNoAxeViolations, failed, ok, running, VIEWPORTS } from './fixtures';

const REVIEW = '/decks/deck-1/review';
const GENERATE = /\/decks\/deck-1\/review-documents$/;

test.describe('review failure and retry states', () => {
	test('S3 a failed start shows the reason and Try again starts the review', async ({ page, mock }) => {
		await mock.set({ generateStatus: 503 });
		await page.goto(REVIEW);
		await page.getByRole('button', { name: 'Generate review' }).first().click();
		await expect(page.getByText("Couldn't start the review")).toBeVisible();
		await expect(page.getByText('The generator is unavailable')).toBeVisible();

		await mock.set({ generateStatus: 202 });
		await page.getByRole('button', { name: 'Try again: start a new review' }).click();
		await expect(page.getByTestId('live-status')).toBeVisible();
	});

	test('S1 an empty deck offers one Generate action', async ({ page }) => {
		await page.goto(REVIEW);
		await expect(page.getByText('No review yet for this deck')).toBeVisible();
	});

	test('S6 a failed latest run explains itself and can offer the last good review', async ({ page, mock }) => {
		await mock.set({ documents: [failed(2), ok(1)] });
		await page.goto(`${REVIEW}?doc=2`);
		await expect(page.getByRole('heading', { name: "The review couldn't finish" })).toBeVisible();
		await page.getByText('Details').click();
		await expect(page.getByText('The model timed out')).toBeVisible();
		await expect(page.getByRole('button', { name: 'View last good review (#1)' })).toBeVisible();
		await page.getByRole('button', { name: 'View last good review (#1)' }).click();
		await expect(page).toHaveURL(/doc=1/);
	});

	test('S6 an interrupted run gets its own headline', async ({ page, mock }) => {
		await mock.set({ documents: [failed(1, 'The review was interrupted (the server restarted before it finished). Generate a new review.')] });
		await page.goto(`${REVIEW}?doc=1`);
		await expect(page.getByRole('heading', { name: 'The review was interrupted' })).toBeVisible();
	});

	test('S7 an older good review is shown with a warning about the failed newest run', async ({ page, mock }) => {
		await mock.set({ documents: [failed(2), ok(1)] });
		await page.goto(REVIEW);
		await expect(page.getByText(/Your latest review \(#2, .*\) failed\. Showing #1/)).toBeVisible();
		await page.getByRole('button', { name: 'See what went wrong' }).click();
		await expect(page).toHaveURL(/doc=2/);
		await expect(page.getByRole('heading', { name: "The review couldn't finish" })).toBeVisible();
	});

	test('S10 an unreachable review list keeps the bar and a working reload', async ({ page, mock }) => {
		await mock.set({ listStatus: 503 });
		await page.goto(REVIEW);
		await expect(page.getByRole('link', { name: '← deck' })).toBeVisible();
		await expect(page.getByText("Reviews couldn't load")).toBeVisible();
		await expect(page.getByRole('button', { name: /Generate/ })).toHaveCount(0);

		await mock.set({ listStatus: 200 });
		await page.getByRole('button', { name: 'Reload reviews' }).click();
		await expect(page.getByText("Reviews couldn't load")).toHaveCount(0);
	});

	test('S11 a review that will not load keeps the bar and the history select', async ({ page, mock }) => {
		await mock.set({ documents: [ok(1)], rowStatus: 503 });
		await page.goto(REVIEW);
		await expect(page.getByText("Couldn't load this review")).toBeVisible();
		await expect(page.getByRole('combobox', { name: 'Review history' })).toBeVisible();
		await expect(page.getByRole('link', { name: '← deck' })).toBeVisible();
	});

	test('S12 swap decisions are not operable while saved decisions are missing, then work after a retry', async ({ page, mock }) => {
		await mock.set({ documents: [ok(1)], feedbackStatus: 503 });
		await page.goto(REVIEW);
		await expect(page.getByText("Your saved swap decisions couldn't load.")).toBeVisible();
		const accept = page.getByRole('button', { name: 'Accept' }).first();
		await expect(accept).toHaveAttribute('aria-disabled', 'true');
		await accept.click({ force: true }); // aria-disabled counts as not enabled to Playwright, so force it: the page must still ignore it
		await expect(accept).toHaveAttribute('aria-pressed', 'false');

		await mock.set({ feedbackStatus: 200 });
		await page.getByRole('button', { name: 'Reload reviews' }).click();
		await expect(page.getByText("Your saved swap decisions couldn't load.")).toHaveCount(0);
		const again = page.getByRole('button', { name: 'Accept' }).first();
		await again.click();
		await expect(again).toHaveAttribute('aria-pressed', 'true');
	});

	test('S13 a partial review lists the unavailable parts and links to them', async ({ page, mock }) => {
		await mock.set({ documents: [ok(1)] });
		await page.goto(REVIEW);
		const banner = page.getByRole('status').filter({ hasText: 'Parts of this review are unavailable' });
		await expect(banner).toBeVisible();
		await expect(banner.getByRole('link', { name: 'Game evidence' })).toHaveAttribute('href', '#sec-forge');
	});

	test('S14 a failed PDF export is reported and can be retried', async ({ page, mock }) => {
		await mock.set({ documents: [ok(1)], pdfStatus: 502 });
		await page.goto(REVIEW);
		await page.getByRole('button', { name: 'Export PDF' }).click();
		await expect(page.getByText('PDF export failed')).toBeVisible();
		await expect(page.getByText('The PDF renderer is offline')).toBeVisible();

		await mock.set({ pdfStatus: 200 });
		const download = page.waitForEvent('download');
		await page.getByRole('button', { name: 'Try again' }).click();
		expect((await download).suggestedFilename()).toBe('review-1.pdf');
	});

	test('§5.7.3 Try again posts exactly one generate request, even on a double click, and keeps the failed run', async ({ page, mock }) => {
		await mock.set({ documents: [failed(1)] });
		await page.goto(`${REVIEW}?doc=1`);
		const retry = page.getByRole('button', { name: 'Try again: start a new review' });
		await retry.dblclick();
		await expect(page.getByTestId('live-status')).toBeVisible();

		expect(await mock.count('POST', GENERATE)).toBe(1);
		const options = await page.getByRole('combobox', { name: 'Review history' }).locator('option').allTextContents();
		expect(options.some((o) => o.includes('#1 · Failed'))).toBe(true);
		expect(options.some((o) => o.startsWith('#2'))).toBe(true);
	});

	test('§5.7.4 losing the API mid-run keeps the stage, backs off, offers Reconnect, and recovers', async ({ page, mock }) => {
		test.setTimeout(150_000);
		await mock.set({ documents: [running(1, 'analysing')], activeId: 1 });
		await page.goto(REVIEW);
		await expect(page.getByTestId('review-stage')).toHaveText('analysing');

		await mock.set({ down: true });
		await expect(page.getByText('Lost contact with the server — retrying…')).toBeVisible({ timeout: 20_000 });
		await expect(page.getByTestId('review-stage')).toHaveText('analysing');

		// 2 s, then 5 s, then 10 s steps: five misses take about 37 s.
		await expect(page.getByText("Still can't reach the server")).toBeVisible({ timeout: 90_000 });
		const reconnect = page.getByRole('button', { name: 'Reconnect' });
		await expect(reconnect).toBeVisible();
		const polls = (await mock.requests()).length;
		await page.waitForTimeout(3_000);
		expect((await mock.requests()).length).toBe(polls); // polling has stopped

		await mock.set({ down: false });
		await reconnect.click();
		await expect(page.getByText(/Lost contact|Still can't reach/)).toHaveCount(0);
		await expect(page.getByTestId('review-stage')).toHaveText('analysing');
	});

	test('S9 a run that finishes while watched switches to the new review', async ({ page, mock }) => {
		test.setTimeout(60_000);
		await mock.set({ documents: [running(1)], activeId: 1 });
		await page.goto(REVIEW);
		await expect(page.getByTestId('live-status')).toBeVisible();

		await mock.set({ documents: [ok(1)], activeId: null });
		await expect(page).toHaveURL(/doc=1/, { timeout: 15_000 });
		await expect(page.getByText('Review #1 is ready.')).toBeAttached();
		await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
	});

	for (const vp of VIEWPORTS) {
		for (const [name, docs, extra, path] of [
			['S6', [failed(2), ok(1)], {}, `${REVIEW}?doc=2`],
			['S7', [failed(2), ok(1)], {}, REVIEW],
			['S12', [ok(1)], { feedbackStatus: 503 }, REVIEW],
			['S13', [ok(1)], {}, REVIEW]
		] as const) {
			test(`§5.7.8 axe finds no violations in ${name} at ${vp.width}px`, async ({ page, mock }) => {
				await mock.set({ documents: [...docs], ...extra });
				await page.setViewportSize(vp);
				await page.goto(path);
				await expect(page.locator('main, body').first()).toBeVisible();
				await expectNoAxeViolations(page);
			});
		}
	}
});
