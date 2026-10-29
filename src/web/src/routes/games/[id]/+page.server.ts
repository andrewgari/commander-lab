import { error } from '@sveltejs/kit';
import { apiGet, ApiError, type GameReport } from '#lib/api.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url }) => {
	try {
		return {
			report: await apiGet<GameReport>(`/api/v2/games/${encodeURIComponent(params.id)}`),
			imported: url.searchParams.get('imported') === '1',
			duplicate: url.searchParams.get('duplicate') === '1'
		};
	} catch (e) {
		if (e instanceof ApiError && e.status === 404) error(404, 'Game not found');
		error(503, e instanceof Error ? e.message : 'Game data unavailable');
	}
};
