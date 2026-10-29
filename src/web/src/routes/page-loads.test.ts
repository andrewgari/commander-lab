import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiGet = vi.fn();
vi.mock('#lib/api.ts', () => ({ apiGet: (...a: unknown[]) => apiGet(...a), ApiError: class extends Error {}, apiPost: vi.fn() }));

const ok = (path: string): unknown => {
	if (path.startsWith('/api/v2/decks')) return { decks: [] };
	if (path.startsWith('/api/v2/inventory'))
		return { inventory: [{ oracle_id: 'x', name: 'Secret Row' }], totals: { cards: 1, owned: 1, in_collection: 1, in_deck: 0, in_mail: 0 } };
	if (path.startsWith('/api/v2/games')) return { games: [] };
	if (path.startsWith('/api/v2/drafts')) return { drafts: [] };
	if (path.startsWith('/api/v2/profile/latest')) return { available: false, reason: null, snapshot: null };
	throw new Error('unexpected ' + path);
};

beforeEach(() => {
	apiGet.mockReset();
});

describe('home load', () => {
	it('never serialises inventory rows (gap G6) and reduces profile to power', async () => {
		apiGet.mockImplementation(async (p: string) => ok(p));
		const { load } = await import('./+page.server.ts');
		const data = (await load({} as never)) as { sources: Record<string, { ok: boolean; value?: unknown }> };
		expect(JSON.stringify(data)).not.toContain('Secret Row');
		expect(data.sources.inventory).toEqual({ ok: true, value: { cards: 1, owned: 1, in_collection: 1, in_deck: 0, in_mail: 0 } });
		expect(data.sources.profile).toEqual({ ok: true, value: null });
	});
	it.each(['decks', 'inventory', 'games', 'drafts?status=open', 'drafts?status=committed', 'profile'])(
		'a 503 from %s fails only that source and the load still resolves',
		async (frag) => {
			apiGet.mockImplementation(async (p: string) => {
				if (p.includes(frag)) throw new Error('HTTP 503');
				return ok(p);
			});
			const { load } = await import('./+page.server.ts');
			const data = (await load({} as never)) as { sources: Record<string, { ok: boolean }> };
			const failed = Object.entries(data.sources).filter(([, v]) => !v.ok);
			expect(failed).toHaveLength(1);
		}
	);
	it('requests games with limit=200 and open/committed drafts', async () => {
		apiGet.mockImplementation(async (p: string) => ok(p));
		const { load } = await import('./+page.server.ts');
		await load({} as never);
		const paths = apiGet.mock.calls.map((c) => c[0]);
		expect(paths).toContain('/api/v2/games?limit=200');
		expect(paths).toContain('/api/v2/drafts?status=open');
		expect(paths).toContain('/api/v2/drafts?status=committed&limit=5');
	});
});

describe('games load', () => {
	it('splits games and decks failures and honours ?import=1', async () => {
		apiGet.mockImplementation(async (p: string) => {
			if (p.startsWith('/api/v2/games')) throw new Error('games down');
			return { decks: [{ id: 'a' }] };
		});
		const { load } = await import('./games/+page.server.ts');
		const url = new URL('http://x/games?import=1');
		const d = (await load({ url } as never)) as Record<string, unknown>;
		expect(d.gamesError).toBe('games down');
		expect(d.decksError).toBeNull();
		expect(d.decks).toEqual([{ id: 'a' }]);
		expect(d.importRequested).toBe(true);
	});
	it('decks failing leaves games intact', async () => {
		apiGet.mockImplementation(async (p: string) => {
			if (p.startsWith('/api/v2/decks')) throw new Error('decks down');
			return { games: [{ id: 1 }] };
		});
		const { load } = await import('./games/+page.server.ts');
		const d = (await load({ url: new URL('http://x/games') } as never)) as Record<string, unknown>;
		expect(d.decksError).toBe('decks down');
		expect(d.gamesError).toBeNull();
		expect(d.games).toEqual([{ id: 1 }]);
		expect(d.importRequested).toBe(false);
	});
});
