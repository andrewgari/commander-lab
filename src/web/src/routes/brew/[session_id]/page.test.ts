import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte';

vi.mock('$app/state', () => ({ page: { url: new URL('http://x:8011/brew/s1') } }));
vi.mock('$app/forms', () => ({
	deserialize: (text: string) => JSON.parse(text)
}));

import Page from './+page.svelte';

const L = (name: string, tier: number, oracle_id: string) => ({ name, quantity: 1, tier, tier_label: '', oracle_id });
const base = [L('Atraxa', 1, 'o-atraxa'), L('Sol Ring', 2, 'o-sol')];
const data = (status = 'drafts_ready') => ({
	sessionId: 's1', status, name: 'Atraxa brew', commanders: ['Atraxa'], ownedDefault: 'owned', identity: ['W', 'U', 'B', 'G'],
	cards: base, legacyOrigin: ''
});
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const mount = (d = data()) => render(Page as any, { data: d });

beforeEach(() => {
	vi.stubGlobal('matchMedia', (q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('brew page', () => {
	it('lists cards by tier, protects the commander, and links to the legacy page', () => {
		mount();
		expect(screen.getByTestId('tier-1').textContent).toContain('Atraxa');
		expect(screen.getByTestId('tier-2').textContent).toContain('Sol Ring');
		expect(screen.queryByRole('button', { name: 'Remove Atraxa' })).toBeNull();
		expect(screen.getByRole('button', { name: 'Remove Sol Ring' })).toBeTruthy();
		expect(screen.getByTestId('legacy-link').getAttribute('href')).toBe('http://x:8010/brew/s1');
	});
	it('remove posts to ?/remove and renders the recomputed list', async () => {
		const fetchMock = vi.fn(async () => new Response(JSON.stringify({ type: 'success', status: 200, data: { cards: [base[0]] } })));
		vi.stubGlobal('fetch', fetchMock);
		mount();
		await fireEvent.click(screen.getByRole('button', { name: 'Remove Sol Ring' }));
		await waitFor(() => expect(screen.queryByTestId('tier-2')).toBeNull());
		expect(String((fetchMock.mock.calls[0] as unknown[])[0])).toBe('?/remove');
	});
	it('a failed remove shows the server reason and keeps the list', async () => {
		vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ type: 'failure', status: 409, data: { actionError: 'nope' } }))));
		mount();
		await fireEvent.click(screen.getByRole('button', { name: 'Remove Sol Ring' }));
		await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('nope'));
		expect(screen.getByTestId('tier-2')).toBeTruthy();
	});
	it('a non-editable session hides the search bar and the remove buttons', () => {
		mount(data('accepted'));
		expect(screen.queryByRole('search')).toBeNull();
		expect(screen.queryByRole('button', { name: /^Remove/ })).toBeNull();
	});
	it('renders the search bar for an editable session', () => {
		mount();
		expect(screen.getByRole('search')).toBeTruthy();
	});
});
