import { error, isHttpError } from '@sveltejs/kit';
import { requireShape } from '#lib/shape.ts';
import { apiGet, ApiError, type DeckDetail } from '#lib/api.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params }) => {
	try {
		const detail = await apiGet<DeckDetail>(`/api/v2/decks/${encodeURIComponent(params.id)}`);
		requireShape(detail, 'Deck data', {
			deck: 'object',
			'deck.name': 'string',
			'deck.id': 'string',
			'deck.color_identity': 'array',
			'deck.commanders': 'array',
			boards: 'object',
			'have_buy.totals': 'object',
			'have_buy.lines': 'array'
		});
		return { detail };
	} catch (e) {
		if (isHttpError(e)) throw e;
		if (e instanceof ApiError && e.status === 404) error(404, 'Deck not found');
		error(503, e instanceof Error ? e.message : 'Deck data unavailable');
	}
};
