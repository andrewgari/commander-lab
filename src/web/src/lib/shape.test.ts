import { describe, expect, it, vi } from 'vitest';
import { requireShape } from './shape.ts';

const apiGet = vi.fn();
vi.mock('#lib/api.ts', () => ({ apiGet: (...a: unknown[]) => apiGet(...a), ApiError: class extends Error { status = 0; }, apiPost: vi.fn(), apiDelete: vi.fn() }));

describe('requireShape', () => {
	it('throws 502 on an empty object', () => {
		expect(() => requireShape({}, 'Deck data', { deck: 'object' })).toThrow();
		try { requireShape({}, 'Deck data', { deck: 'object' }); } catch (e) { expect((e as { status: number }).status).toBe(502); }
	});
	it('accepts a complete payload', () => {
		expect(() => requireShape({ deck: { name: 'x' }, cards: [] }, 'D', { 'deck.name': 'string', cards: 'array' })).not.toThrow();
	});
	it('rejects null / arrays / wrong nested kinds', () => {
		expect(() => requireShape(null, 'D', {})).toThrow();
		expect(() => requireShape([], 'D', {})).toThrow();
		expect(() => requireShape({ deck: { name: 3 } }, 'D', { 'deck.name': 'string' })).toThrow();
	});
});

describe('detail loads degrade on {}', () => {
	it.each([
		['./../routes/decks/[id]/+page.server.ts'],
		['./../routes/games/[id]/+page.server.ts'],
		['./../routes/drafts/[id]/+page.server.ts']
	])('%s turns {} into an HTTP error, not a TypeError', async (mod) => {
		apiGet.mockResolvedValue({});
		const { load } = await import(/* @vite-ignore */ mod);
		const err = await load({ params: { id: 'x' }, url: new URL('http://x/') }).catch((e: unknown) => e);
		expect((err as { status?: number }).status).toBe(502);
	});
	it('profile load normalises {} payloads', async () => {
		apiGet.mockResolvedValue({});
		const { load } = await import('./../routes/profile/+page.server.ts');
		const d = (await load({} as never)) as unknown as Record<string, any>;
		expect(d.latest.snapshot).toBeNull();
		expect(d.history).toEqual([]);
		expect(d.overrides.known_keys).toEqual({});
		expect(d.player?.pending_proposals).toEqual([]);
	});
});
