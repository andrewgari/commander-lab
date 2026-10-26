import { apiGet } from '#lib/api';
import type { PageServerLoad } from './$types';

export interface DeckSummary {
	id: number;
	name: string;
	archidekt_id: string | null;
	archidekt_url: string | null;
	status: string;
	color_identity: string[];
	commanders: string[];
	source: string;
}

export const load: PageServerLoad = async ({ fetch }) => {
	const data = await apiGet<{ decks: DeckSummary[] }>('/api/v2/decks', fetch);
	return { decks: data.decks };
};
