import { error, fail, isHttpError, redirect } from '@sveltejs/kit';
import { requireShape } from '#lib/shape.ts';
import {
	apiDelete,
	apiGet,
	apiPost,
	ApiError,
	type DeckSummary,
	type DraftState,
	type DraftSuggestions
} from '#lib/api.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params }) => {
	let state: DraftState;
	try {
		state = await apiGet<DraftState>(`/api/v2/drafts/${params.id}`);
		requireShape(state, 'Draft data', { draft: 'object', 'draft.name': 'string', 'draft.status': 'string', 'draft.source_decks': 'array', cards: 'array', moves: 'array' });
	} catch (e) {
		if (isHttpError(e)) throw e;
		if (e instanceof ApiError && e.status === 404) error(404, 'Draft not found');
		error(503, e instanceof Error ? e.message : 'Draft data unavailable');
	}
	let suggestions: DraftSuggestions | null = null;
	let suggestionsError: string | null = null;
	let deckNames: Record<string, string> | null = null;
	const [sugg, decks] = await Promise.allSettled([
		apiGet<DraftSuggestions>(`/api/v2/drafts/${params.id}/suggestions`),
		apiGet<{ decks: DeckSummary[] }>('/api/v2/decks')
	]);
	if (sugg.status === 'fulfilled') suggestions = sugg.value;
	else suggestionsError = sugg.reason instanceof Error ? sugg.reason.message : 'suggestions unavailable';
	if (decks.status === 'fulfilled' && Array.isArray(decks.value?.decks)) {
		deckNames = Object.fromEntries(decks.value.decks.map((d) => [d.id, d.name]));
	}
	return { state, suggestions, suggestionsError, deckNames };
};

function actionError(e: unknown) {
	if (e instanceof ApiError) return fail(e.status, { actionError: e.message });
	throw e;
}

export const actions: Actions = {
	upsert_card: async ({ request, params }) => {
		const form = await request.formData();
		const oracle_id = String(form.get('oracle_id') ?? '').trim();
		const name = String(form.get('name') ?? '').trim();
		const qty = Number(form.get('qty') ?? 1);
		const board = String(form.get('board') ?? 'main');
		if (!oracle_id || !name || !Number.isFinite(qty) || qty < 1) {
			return fail(400, { actionError: 'Card needs an oracle id, a name and a qty ≥ 1.' });
		}
		try {
			await apiPost(`/api/v2/drafts/${params.id}/cards`, { oracle_id, name, qty, board });
		} catch (e) {
			return actionError(e);
		}
	},
	remove_card: async ({ request, params }) => {
		const form = await request.formData();
		const oracle_id = String(form.get('oracle_id') ?? '');
		const board = String(form.get('board') ?? 'main');
		try {
			await apiDelete(`/api/v2/drafts/${params.id}/cards/${oracle_id}/${board}`);
		} catch (e) {
			return actionError(e);
		}
	},
	stage_move: async ({ request, params }) => {
		const form = await request.formData();
		const instance_id = String(form.get('instance_id') ?? '').trim();
		const moveAction = String(form.get('move_action') ?? 'assign');
		if (!instance_id) return fail(400, { actionError: 'Move needs an instance id.' });
		try {
			await apiPost(`/api/v2/drafts/${params.id}/moves`, { instance_id, action: moveAction });
		} catch (e) {
			return actionError(e);
		}
	},
	unstage_move: async ({ request, params }) => {
		const form = await request.formData();
		const instance_id = String(form.get('instance_id') ?? '');
		try {
			await apiDelete(`/api/v2/drafts/${params.id}/moves/${instance_id}`);
		} catch (e) {
			return actionError(e);
		}
	},
	commit: async ({ params }) => {
		try {
			const result = await apiPost<{ result_deck_id: string }>(
				`/api/v2/drafts/${params.id}/commit`,
				{}
			);
			redirect(303, `/decks?built=${result.result_deck_id}`);
		} catch (e) {
			return actionError(e);
		}
	},
	abandon: async ({ params }) => {
		try {
			await apiPost(`/api/v2/drafts/${params.id}/abandon`, {});
		} catch (e) {
			return actionError(e);
		}
		redirect(303, '/drafts');
	}
};
