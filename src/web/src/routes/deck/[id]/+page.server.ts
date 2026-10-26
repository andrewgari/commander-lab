import { error } from '@sveltejs/kit';
import { apiGet } from '#lib/api';
import type { PageServerLoad } from './$types';

export interface HaveBuyCard {
	oracle_id: string;
	name: string;
	board: string;
	category: string | null;
	required: number;
	have: number;
	have_in_deck: number;
	have_from_collection: number;
	pending: number;
	buy: number;
}

export interface DeckDetail {
	deck: {
		id: number;
		name: string;
		archidekt_url: string | null;
		status: string;
		color_identity: string[];
		commanders: string[];
	};
	cards: Array<{
		oracle_id: string;
		name: string;
		board: string;
		qty: number;
		category: string | null;
		type_line: string | null;
		cmc: number | null;
		image_uri: string | null;
	}>;
	have_buy: {
		cards: HaveBuyCard[];
		totals: {
			required: number;
			have: number;
			pending: number;
			buy: number;
			have_in_deck: number;
			have_from_collection: number;
		};
	};
}

export interface Snapshot {
	id: number;
	trigger: string;
	summary_text: string;
	metrics: Record<string, unknown>;
}

export const load: PageServerLoad = async ({ fetch, params }) => {
	let detail: DeckDetail;
	try {
		detail = await apiGet<DeckDetail>(`/api/v2/decks/${params.id}`, fetch);
	} catch {
		throw error(404, `deck ${params.id} not found`);
	}
	// The profile snapshot is a bonus panel; its absence must not 404 the deck.
	let snapshot: Snapshot | null = null;
	try {
		snapshot = await apiGet<Snapshot>('/api/v2/profile/latest', fetch);
	} catch {
		snapshot = null;
	}
	return { detail, snapshot };
};
