import { error, isHttpError } from '@sveltejs/kit';
import { requireShape } from '#lib/shape.ts';
import { apiGet, ApiError, type GameReport } from '#lib/api.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url }) => {
	try {
		const report = await apiGet<GameReport>(`/api/v2/games/${encodeURIComponent(params.id)}`);
		requireShape(report, 'Game data', { game: 'object', meta: 'object', players: 'array', cards: 'array' });
		return {
			report,
			imported: url.searchParams.get('imported') === '1',
			duplicate: url.searchParams.get('duplicate') === '1'
		};
	} catch (e) {
		if (isHttpError(e)) throw e;
		if (e instanceof ApiError && e.status === 404) error(404, 'Game not found');
		error(503, e instanceof Error ? e.message : 'Game data unavailable');
	}
};
