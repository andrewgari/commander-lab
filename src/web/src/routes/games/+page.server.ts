import { fail, redirect } from '@sveltejs/kit';
import { apiGet, apiPost, ApiError, type DeckSummary, type ForgeLogImportResult, type GameRecord } from '#lib/api.ts';
import type { Actions, PageServerLoad } from './$types';

const GAMES_LIMIT = 50;

function reasonOf(e: unknown, fallback: string): string {
	return e instanceof Error && e.message ? e.message : fallback;
}

export const load: PageServerLoad = async ({ url }) => {
	// Games and decks fail independently (page-specs D5): one 503 must not blank the page.
	const [games, decks] = await Promise.allSettled([
		apiGet<{ games: GameRecord[] }>(`/api/v2/games?limit=${GAMES_LIMIT}`),
		apiGet<{ decks: DeckSummary[] }>('/api/v2/decks')
	]);
	return {
		games: games.status === 'fulfilled' && Array.isArray(games.value?.games) ? games.value.games : ([] as GameRecord[]),
		gamesError: games.status === 'rejected' ? reasonOf(games.reason, 'Game data unavailable') : Array.isArray(games.value?.games) ? null : 'Game data came back incomplete',
		decks: decks.status === 'fulfilled' && Array.isArray(decks.value?.decks) ? decks.value.decks : ([] as DeckSummary[]),
		decksError: decks.status === 'rejected' ? reasonOf(decks.reason, 'Deck data unavailable') : Array.isArray(decks.value?.decks) ? null : 'Deck data came back incomplete',
		limit: GAMES_LIMIT,
		importRequested: url.searchParams.get('import') === '1'
	};
};

export const actions: Actions = {
	default: async ({ request }) => {
		const form = await request.formData();
		const deck_id = String(form.get('deck_id') ?? '');
		const log_text = String(form.get('log_text') ?? '');
		const seat = Number(form.get('seat') ?? 1) || 1;
		const played_at = String(form.get('played_at') ?? '').trim();
		if (!deck_id || !log_text.trim()) {
			return fail(400, { importError: 'Pick a deck and paste (or upload) a verbose Forge log.' });
		}
		try {
			const result = await apiPost<ForgeLogImportResult>('/api/v2/imports/forge-log', {
				deck_id,
				log_text,
				seat,
				played_at: played_at || null
			});
			if (!result.duplicate && result.games.length > 0) {
				redirect(303, `/games/${result.games[0].game_id}?imported=1`);
			}
			if (result.duplicate && result.games.length > 0) {
				redirect(303, `/games/${result.games[0].game_id}?duplicate=1`);
			}
			return { importError: result.error ?? 'No completed games found in that log.' };
		} catch (e) {
			if (e instanceof ApiError) return fail(e.status, { importError: e.message });
			throw e;
		}
	}
};
