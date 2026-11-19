import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('#lib/api.ts', () => ({
	API_URL: 'http://api', ApiError: class extends Error {}, apiGet: vi.fn(), apiPost: vi.fn(), apiDelete: vi.fn()
}));

import { apiDelete } from '#lib/api.ts';
import { actions } from './+page.server';

function remove(fields: Record<string, string>) {
	const form = new FormData();
	for (const [key, value] of Object.entries(fields)) form.set(key, value);
	return (actions.remove as (event: unknown) => Promise<unknown>)({
		request: { formData: async () => form }, params: { session_id: 's1' }
	});
}

beforeEach(() => {
	vi.mocked(apiDelete).mockReset();
	vi.mocked(apiDelete).mockResolvedValue({ draft: { cards: [] } });
});

describe('brew remove action', () => {
	it('encodes legacy card names in the query', async () => {
		await remove({ name: 'Fire // Ice & more' });
		expect(apiDelete).toHaveBeenCalledWith('/api/v2/brew/s1/cards/by-name?name=Fire+%2F%2F+Ice+%26+more');
	});
	it('continues to remove identified cards by oracle id', async () => {
		await remove({ oracle_id: 'id-1' });
		expect(apiDelete).toHaveBeenCalledWith('/api/v2/brew/s1/cards/id-1');
	});
	it('rejects missing identity without calling the API', async () => {
		const result = await remove({ name: '  ' }) as { status: number };
		expect(result.status).toBe(400);
		expect(apiDelete).not.toHaveBeenCalled();
	});
});
