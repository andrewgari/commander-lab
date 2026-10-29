import { fail, redirect } from '@sveltejs/kit';
import { apiGet, apiPost, ApiError, type DeckSummary, type ForgeLogImportResult, type GameRecord } from '#lib/api.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async () => {
	try {
		const [games, decks] = await Promise.all([
			apiGet<{ games: GameRecord[] }>('/api/v2/games'),
			apiGet<{ decks: DeckSummary[] }>('/api/v2/decks')
		]);
		return { games: games.games, decks: decks.decks, error: null as string | null };
	} catch (e) {
		return {
			games: [] as GameRecord[],
			decks: [] as DeckSummary[],
			error: e instanceof Error ? e.message : 'Game data unavailable'
		};
	}
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
