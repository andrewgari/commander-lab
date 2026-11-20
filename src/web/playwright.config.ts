import { defineConfig, devices } from '@playwright/test';

// The web server calls the API from Node, so the suite runs a mock API process (tests/e2e/mock) and
// points COMMANDER_API_URL at it. Tests share that one mock, hence a single worker.
const WEB_PORT = 5178;
const MOCK_PORT = 8799;

export default defineConfig({
	testDir: './tests/e2e',
	fullyParallel: false,
	workers: 1,
	forbidOnly: !!process.env.CI,
	retries: process.env.CI ? 1 : 0,
	reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
	use: {
		baseURL: `http://127.0.0.1:${WEB_PORT}`,
		// adapter-node derives its own origin from the request, defaulting to https. SvelteKit's CSRF check
		// compares that with the browser's Origin header, so tell it the plain-http truth.
		extraHTTPHeaders: { 'x-forwarded-proto': 'http' },
		trace: 'retain-on-failure',
		screenshot: 'only-on-failure'
	},
	projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
	webServer: [
		{
			command: 'node tests/e2e/mock/server.mjs',
			url: `http://127.0.0.1:${MOCK_PORT}/__mock/requests`,
			env: { MOCK_API_PORT: String(MOCK_PORT) },
			reuseExistingServer: !process.env.CI
		},
		{
			// A production build, so the tests see what ships (hydration timing included).
			command: 'npm run build && node build',
			url: `http://127.0.0.1:${WEB_PORT}/profile`,
			env: { PORT: String(WEB_PORT), HOST: '127.0.0.1', PROTOCOL_HEADER: 'x-forwarded-proto', COMMANDER_API_URL: `http://127.0.0.1:${MOCK_PORT}` },
			reuseExistingServer: !process.env.CI,
			timeout: 240_000
		}
	]
});
