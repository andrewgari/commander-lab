import { error } from '@sveltejs/kit';
import { apiGet, ApiError, type DeckDetail } from '#lib/api.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params }) => {
	try {
		return { detail: await apiGet<DeckDetail>(`/api/v2/decks/${encodeURIComponent(params.id)}`) };
	} catch (e) {
		if (e instanceof ApiError && e.status === 404) error(404, 'Deck not found');
		error(503, e instanceof Error ? e.message : 'Deck data unavailable');
	}
};
