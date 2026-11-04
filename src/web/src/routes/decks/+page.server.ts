import { apiGet, type DeckSummary } from '#lib/api.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async () => {
	try {
		const data = await apiGet<{ decks: DeckSummary[] }>('/api/v2/decks');
		if (!Array.isArray(data?.decks)) throw new Error('Deck data came back incomplete');
		return { decks: data.decks, error: null as string | null };
	} catch (e) {
		return { decks: [] as DeckSummary[], error: e instanceof Error ? e.message : 'unavailable' };
	}
};
