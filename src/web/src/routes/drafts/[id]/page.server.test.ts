import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('#lib/api.ts', () => {
	class ApiError extends Error {
		constructor(
			public status: number,
			message: string
		) {
			super(message);
		}
	}
	return { API_URL: 'http://api', ApiError, apiGet: vi.fn(), apiPost: vi.fn(), apiDelete: vi.fn() };
});

import { apiGet, apiPost } from '#lib/api.ts';
import { actions } from './+page.server';

const SOL = { oracle_id: 'o-sol', name: 'Sol Ring' };

function run(action: 'upsert_card_by_name' | 'upsert_card', fields: Record<string, string>, searchResults: unknown[] = [SOL]) {
	const form = new FormData();
	for (const [k, v] of Object.entries(fields)) form.set(k, v);
	const fetchMock = vi.fn(async () => new Response(JSON.stringify({ results: searchResults }), { status: 200 }));
	const event = { request: { formData: async () => form }, params: { id: 'd1' }, fetch: fetchMock };
	return { fetchMock, result: (actions[action] as (e: unknown) => Promise<unknown>)(event) };
}

beforeEach(() => {
	vi.mocked(apiGet).mockReset();
	vi.mocked(apiPost).mockReset();
});

describe('upsert_card_by_name', () => {
	it('exact match: searches owned cards and upserts qty', async () => {
		vi.mocked(apiGet).mockResolvedValue({ cards: [] });
		const { fetchMock, result } = run('upsert_card_by_name', { card_name: 'sol ring', qty: '2', board: 'maybe' });
		await result;
		const url = String((fetchMock.mock.calls[0] as unknown[])[0]);
		expect(url).toContain('/api/v2/cards/search?');
		expect(url).toContain('owned=owned');
		expect(url).toContain('page_size=5');
		expect(apiPost).toHaveBeenCalledWith('/api/v2/drafts/d1/cards', { oracle_id: 'o-sol', name: 'Sol Ring', qty: 2, board: 'maybe' });
	});

	it('no match: 400 with the owned-card message and no write', async () => {
		const { result } = run('upsert_card_by_name', { card_name: 'Sol', qty: '1', board: 'main' }, [{ oracle_id: 'x', name: 'Solemn Simulacrum' }]);
		const out = (await result) as { status: number; data: { actionError: string } };
		expect(out.status).toBe(400);
		expect(out.data.actionError).toBe('No owned card named “Sol”.');
		expect(apiPost).not.toHaveBeenCalled();
	});

	it('existing + qty: adds to the staged quantity on that board only', async () => {
		vi.mocked(apiGet).mockResolvedValue({
			cards: [
				{ oracle_id: 'o-sol', board: 'main', qty: 3 },
				{ oracle_id: 'o-sol', board: 'maybe', qty: 9 }
			]
		});
		await run('upsert_card_by_name', { card_name: 'Sol Ring', qty: '2', board: 'main' }).result;
		expect(apiPost).toHaveBeenCalledWith('/api/v2/drafts/d1/cards', expect.objectContaining({ qty: 5, board: 'main' }));
	});

	it('rejects an unknown board', async () => {
		const out = (await run('upsert_card_by_name', { card_name: 'Sol Ring', qty: '1', board: 'bogus' }).result) as { status: number };
		expect(out.status).toBe(400);
	});
});

describe('upsert_card board validation', () => {
	it('maps maybeboard to maybe and 400s unknown boards', async () => {
		await run('upsert_card', { oracle_id: 'o', name: 'N', qty: '1', board: 'maybeboard' }).result;
		expect(apiPost).toHaveBeenCalledWith('/api/v2/drafts/d1/cards', { oracle_id: 'o', name: 'N', qty: 1, board: 'maybe' });
		const out = (await run('upsert_card', { oracle_id: 'o', name: 'N', qty: '1', board: 'zzz' }).result) as { status: number };
		expect(out.status).toBe(400);
	});
});
