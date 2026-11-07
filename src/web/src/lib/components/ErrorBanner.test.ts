import { afterEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet } from 'svelte';
import { SvelteMap } from 'svelte/reactivity';
import { render, screen, waitFor } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import ErrorBanner from './ErrorBanner.svelte';
import { expectNoAxeViolations } from '#lib/test/axe.ts';

type Severity = 'error' | 'warning' | 'info';

const SEVERITIES: Array<{ severity: Severity; role: string; word: string; icon: string; dismiss: string }> = [
	{ severity: 'error', role: 'alert', word: 'Error:', icon: 'error', dismiss: 'Dismiss error message' },
	{ severity: 'warning', role: 'status', word: 'Warning:', icon: 'alert', dismiss: 'Dismiss warning message' },
	{ severity: 'info', role: 'status', word: 'Notice:', icon: 'info', dismiss: 'Dismiss notice message' }
];

/** A promise the test settles by hand. */
function deferred() {
	let resolve!: () => void;
	let reject!: (e: unknown) => void;
	const promise = new Promise<void>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

afterEach(() => {
	document.body.innerHTML = '';
});

describe('ErrorBanner: roles and severity', () => {
	it('defaults to severity error', () => {
		render(ErrorBanner, { message: 'Deck data unavailable' });
		expect(screen.getByRole('alert')).toBeTruthy();
	});

	it.each(SEVERITIES)('$severity renders role=$role with aria-atomic', ({ severity, role }) => {
		const { container } = render(ErrorBanner, { message: 'Boom', severity });
		const banner = screen.getByRole(role);
		expect(container.contains(banner)).toBe(true);
		expect(banner.getAttribute('aria-atomic')).toBe('true');
		// only errors are assertive alerts
		expect(screen.queryAllByRole(role === 'alert' ? 'status' : 'alert')).toHaveLength(0);
	});

	it.each(SEVERITIES)('$severity shows the visible word "$word" and the $icon icon', ({ severity, word, icon }) => {
		const { container } = render(ErrorBanner, { message: 'Boom', severity });
		const strong = container.querySelector('strong');
		expect(strong?.textContent).toBe(word);
		expect(screen.getByRole(severity === 'error' ? 'alert' : 'status').textContent).toContain(`${word} Boom`);
		const svg = container.querySelector('svg');
		expect(svg?.getAttribute('data-icon')).toBe(icon);
		expect(svg?.getAttribute('aria-hidden')).toBe('true');
	});

	it.each(SEVERITIES)('$severity uses its own token family via a modifier class', ({ severity }) => {
		render(ErrorBanner, { message: 'Boom', severity });
		const banner = screen.getByRole(severity === 'error' ? 'alert' : 'status');
		expect(banner.className).toContain(severity === 'error' ? 'banner--danger' : `banner--${severity}`);
	});

	it('merges the class prop onto the banner element', () => {
		render(ErrorBanner, { message: 'Boom', class: 'my-margin' });
		expect(screen.getByRole('alert').classList.contains('my-margin')).toBe(true);
	});

	it('does not move focus on mount', () => {
		render(ErrorBanner, { message: 'Boom', dismissible: true, onRetry: () => {} });
		expect(document.activeElement).toBe(document.body);
	});
});

describe('ErrorBanner: detail', () => {
	it('renders detail up to 120 chars inline, not in <details>', () => {
		const detail = 'x'.repeat(120);
		const { container } = render(ErrorBanner, { message: 'Boom', detail });
		expect(container.querySelector('details')).toBeNull();
		expect(screen.getByText(detail)).toBeTruthy();
	});

	it('collapses a 121-char detail into <details><summary>Details</summary>', () => {
		const detail = 'y'.repeat(121);
		const { container } = render(ErrorBanner, { message: 'Boom', detail });
		const details = container.querySelector('details');
		expect(details).not.toBeNull();
		expect(details!.querySelector('summary')?.textContent).toBe('Details');
		expect(details!.textContent).toContain(detail);
		expect(details!.hasAttribute('open')).toBe(false);
	});

	it('collapses a short detail when detailCollapsed is set', () => {
		const { container } = render(ErrorBanner, { message: 'Boom', detail: 'short', detailCollapsed: true });
		expect(container.querySelector('details > summary')?.textContent).toBe('Details');
	});

	it.each(['short', 'z'.repeat(200)])('renders detail %#  as text, never HTML', (pad) => {
		const detail = `<script>alert(1)</script><img src=x onerror=alert(2)>${pad === 'short' ? '' : pad}`;
		const { container } = render(ErrorBanner, { message: 'Boom', detail });
		expect(container.querySelector('script')).toBeNull();
		expect(container.querySelector('img')).toBeNull();
		expect(container.textContent).toContain('<script>alert(1)</script>');
	});

	it('children replace detail', () => {
		const children = createRawSnippet(() => ({ render: () => '<span data-testid="rich">Rich content</span>' }));
		render(ErrorBanner, { message: 'Boom', detail: 'plain detail', children });
		expect(screen.getByTestId('rich')).toBeTruthy();
		expect(screen.queryByText('plain detail')).toBeNull();
	});

	it('renders nothing extra when there is no detail', () => {
		const { container } = render(ErrorBanner, { message: 'Boom' });
		expect(container.querySelector('details')).toBeNull();
		expect(container.querySelector('.banner__detail')).toBeNull();
	});
});

describe('ErrorBanner: retry', () => {
	it('renders no retry button without onRetry', () => {
		render(ErrorBanner, { message: 'Boom' });
		expect(screen.queryByRole('button')).toBeNull();
	});

	it('renders a native secondary button with the default and a custom label', () => {
		const a = render(ErrorBanner, { message: 'Boom', onRetry: () => {} });
		const btn = screen.getByRole('button', { name: 'Try again' });
		expect(btn.tagName).toBe('BUTTON');
		expect(btn.getAttribute('type')).toBe('button');
		expect(btn.classList.contains('btn')).toBe(true);
		expect(btn.classList.contains('btn--secondary')).toBe(true);
		a.unmount();
		render(ErrorBanner, { message: 'Boom', onRetry: () => {}, retryLabel: 'Reload decks' });
		expect(screen.getByRole('button', { name: 'Reload decks' })).toBeTruthy();
	});

	it('calls a synchronous onRetry on every click without entering a pending state', async () => {
		const onRetry = vi.fn();
		render(ErrorBanner, { message: 'Boom', onRetry });
		const btn = screen.getByRole('button', { name: 'Try again' });
		await userEvent.click(btn);
		await userEvent.click(btn);
		expect(onRetry).toHaveBeenCalledTimes(2);
		expect(btn.getAttribute('aria-disabled')).not.toBe('true');
	});

	it('tracks an async retry: aria-disabled (not disabled), keeps focus, never double-fires', async () => {
		const d = deferred();
		const onRetry = vi.fn(() => d.promise);
		render(ErrorBanner, { message: 'Boom', onRetry });
		const btn = screen.getByRole('button', { name: 'Try again' });

		await userEvent.click(btn);
		await waitFor(() => expect(btn.getAttribute('aria-disabled')).toBe('true'));
		expect(btn.hasAttribute('disabled')).toBe(false);
		expect(btn.textContent).toContain('Retrying…');
		expect(document.activeElement).toBe(btn);

		await userEvent.click(btn);
		await userEvent.keyboard('{Enter}');
		expect(onRetry).toHaveBeenCalledTimes(1);

		d.resolve();
		await waitFor(() => expect(btn.getAttribute('aria-disabled')).not.toBe('true'));
		expect(btn.textContent).toContain('Try again');
		expect(document.activeElement).toBe(btn);

		await userEvent.click(btn);
		expect(onRetry).toHaveBeenCalledTimes(2);
		d.resolve();
	});

	it('catches a rejected retry: no unhandled rejection, state resets', async () => {
		const unhandled = vi.fn();
		process.on('unhandledRejection', unhandled);
		try {
			const d = deferred();
			const onRetry = vi.fn(() => d.promise);
			render(ErrorBanner, { message: 'Boom', onRetry });
			const btn = screen.getByRole('button', { name: 'Try again' });
			await userEvent.click(btn);
			await waitFor(() => expect(btn.getAttribute('aria-disabled')).toBe('true'));

			d.reject(new Error('still down'));
			await waitFor(() => expect(btn.getAttribute('aria-disabled')).not.toBe('true'));
			expect(btn.textContent).toContain('Try again');
			await new Promise((r) => setTimeout(r, 20)); // let node deliver any unhandledRejection
			expect(unhandled).not.toHaveBeenCalled();
		} finally {
			process.off('unhandledRejection', unhandled);
		}
	});

	it('also survives a synchronous throw from onRetry', async () => {
		const onRetry = vi.fn(() => {
			throw new Error('sync boom');
		});
		render(ErrorBanner, { message: 'Boom', onRetry });
		const btn = screen.getByRole('button', { name: 'Try again' });
		await userEvent.click(btn);
		expect(onRetry).toHaveBeenCalledTimes(1);
		expect(btn.getAttribute('aria-disabled')).not.toBe('true');
	});

	it('honours the controlled retrying prop and ignores clicks while set', async () => {
		const onRetry = vi.fn();
		render(ErrorBanner, { message: 'Boom', onRetry, retrying: true });
		const btn = screen.getByRole('button', { name: /Retrying/ });
		expect(btn.getAttribute('aria-disabled')).toBe('true');
		expect(btn.hasAttribute('disabled')).toBe(false);
		await userEvent.click(btn);
		expect(onRetry).not.toHaveBeenCalled();
	});

	it('shows a spinner icon while retrying', async () => {
		const d = deferred();
		const { container } = render(ErrorBanner, { message: 'Boom', onRetry: () => d.promise });
		await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
		await waitFor(() => expect(container.querySelector('.banner__actions [data-icon="spinner"]')).not.toBeNull());
		d.resolve();
	});

	it('renders the actions snippet after the retry button', () => {
		const actions = createRawSnippet(() => ({ render: () => '<a href="/docs" data-testid="docs">Docs</a>' }));
		render(ErrorBanner, { message: 'Boom', onRetry: () => {}, actions });
		const retry = screen.getByRole('button', { name: 'Try again' });
		const docs = screen.getByTestId('docs');
		expect(retry.compareDocumentPosition(docs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
	});
});

describe('ErrorBanner: dismiss', () => {
	it('renders no dismiss button unless dismissible', () => {
		render(ErrorBanner, { message: 'Boom' });
		expect(screen.queryByRole('button')).toBeNull();
	});

	it.each(SEVERITIES)('names the $severity close button "$dismiss" and uses the close icon', ({ severity, dismiss }) => {
		render(ErrorBanner, { message: 'Boom', severity, dismissible: true });
		const btn = screen.getByRole('button', { name: dismiss });
		expect(btn.querySelector('svg')?.getAttribute('data-icon')).toBe('close');
	});

	it('removes the banner and calls onDismiss when the close button is clicked', async () => {
		const onDismiss = vi.fn();
		render(ErrorBanner, { message: 'Boom', dismissible: true, onDismiss });
		await userEvent.click(screen.getByRole('button', { name: 'Dismiss error message' }));
		await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
		expect(onDismiss).toHaveBeenCalledTimes(1);
	});

	it('dismisses on Escape while focus is inside the banner', async () => {
		const onDismiss = vi.fn();
		render(ErrorBanner, { message: 'Boom', dismissible: true, onDismiss, detail: 'q'.repeat(130) });
		screen.getByText('Details').focus(); // a non-button focus target inside the banner
		await userEvent.keyboard('{Escape}');
		await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
		expect(onDismiss).toHaveBeenCalledTimes(1);
	});

	it('ignores Escape when focus is outside the banner', async () => {
		const outside = document.createElement('button');
		document.body.append(outside);
		render(ErrorBanner, { message: 'Boom', dismissible: true });
		outside.focus();
		await userEvent.keyboard('{Escape}');
		expect(screen.getByRole('alert')).toBeTruthy();
	});

	it('ignores Escape when not dismissible', async () => {
		render(ErrorBanner, { message: 'Boom', onRetry: () => {} });
		screen.getByRole('button', { name: 'Try again' }).focus();
		await userEvent.keyboard('{Escape}');
		expect(screen.getByRole('alert')).toBeTruthy();
	});

	it('returns focus to the element focused before entering the banner', async () => {
		const before = document.createElement('button');
		before.textContent = 'Submit';
		document.body.append(before);
		render(ErrorBanner, { message: 'Boom', dismissible: true });
		before.focus();
		await userEvent.tab(); // into the banner: the close button
		const close = screen.getByRole('button', { name: 'Dismiss error message' });
		expect(document.activeElement).toBe(close);
		await userEvent.click(close);
		await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
		expect(document.activeElement).toBe(before);
	});

	it('restores the prior element on Escape too', async () => {
		const before = document.createElement('button');
		document.body.append(before);
		render(ErrorBanner, { message: 'Boom', dismissible: true });
		before.focus();
		await userEvent.tab();
		await userEvent.keyboard('{Escape}');
		await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
		expect(document.activeElement).toBe(before);
	});

	it('falls back to #main when the prior element is gone', async () => {
		const main = document.createElement('main');
		main.id = 'main';
		main.tabIndex = -1;
		const before = document.createElement('button');
		document.body.append(main, before);
		render(ErrorBanner, { message: 'Boom', dismissible: true });
		before.focus();
		await userEvent.tab();
		before.remove(); // e.g. the form button it came from was unmounted
		await userEvent.click(screen.getByRole('button', { name: 'Dismiss error message' }));
		await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
		expect(document.activeElement).toBe(main);
	});

	it('falls back to #main when focus entered from nowhere, and never lands on body', async () => {
		const main = document.createElement('main');
		main.id = 'main';
		main.tabIndex = -1;
		document.body.append(main);
		render(ErrorBanner, { message: 'Boom', dismissible: true });
		screen.getByRole('button', { name: 'Dismiss error message' }).focus();
		await userEvent.keyboard('{Escape}');
		await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
		expect(document.activeElement).toBe(main);
		expect(document.activeElement).not.toBe(document.body);
	});

	it('does not remember a prior element from inside the banner', async () => {
		const before = document.createElement('button');
		const main = document.createElement('main');
		main.id = 'main';
		main.tabIndex = -1;
		document.body.append(main, before);
		render(ErrorBanner, { message: 'Boom', dismissible: true, onRetry: () => {} });
		before.focus();
		await userEvent.tab(); // retry
		await userEvent.tab(); // close: relatedTarget is the retry button, inside the banner
		await userEvent.keyboard('{Escape}');
		await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
		expect(document.activeElement).toBe(before);
	});

	it('supports bind:open in both directions', async () => {
		// A SvelteMap makes the getter reactive, which is what a parent's $state gives bind:open.
		const store = new SvelteMap<string, boolean>([['open', true]]);
		render(ErrorBanner, {
			message: 'Boom',
			dismissible: true,
			get open() {
				return store.get('open')!;
			},
			set open(v: boolean) {
				store.set('open', v);
			}
		});
		await userEvent.click(screen.getByRole('button', { name: 'Dismiss error message' }));
		expect(store.get('open')).toBe(false);
		await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
		store.set('open', true); // parent re-opens it
		await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy());
	});

	it('renders nothing when open starts false', () => {
		const { container } = render(ErrorBanner, { message: 'Boom', open: false });
		expect(container.querySelector('[role="alert"]')).toBeNull();
	});
});

describe('ErrorBanner: axe', () => {
	it.each(SEVERITIES)('$severity has no violations (message only)', async ({ severity }) => {
		const { container } = render(ErrorBanner, { message: 'Deck data unavailable', severity });
		await expectNoAxeViolations(container);
	});

	it.each(SEVERITIES)('$severity has no violations (everything on)', async ({ severity }) => {
		const actions = createRawSnippet(() => ({ render: () => '<a href="/docs">Read the docs</a>' }));
		const { container } = render(ErrorBanner, {
			message: 'Deck data unavailable',
			severity,
			detail: 'Server said: ' + 'x'.repeat(130),
			onRetry: () => {},
			dismissible: true,
			actions
		});
		await expectNoAxeViolations(container);
	});

	it('has no violations while retrying', async () => {
		const { container } = render(ErrorBanner, { message: 'Boom', onRetry: () => {}, retrying: true });
		await expectNoAxeViolations(container);
	});
});
