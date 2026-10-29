import { error, fail, redirect } from '@sveltejs/kit';
import {
	API_URL,
	apiDelete,
	apiGet,
	apiPost,
	ApiError,
	type DeckSummary,
	type DraftState,
	type DraftSuggestions
} from '#lib/api.ts';
import { pickExactMatch } from '#lib/drafts/match.ts';
import { toApiBoard } from '#lib/drafts/boards.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params }) => {
	let state: DraftState;
	try {
		state = await apiGet<DraftState>(`/api/v2/drafts/${encodeURIComponent(params.id)}`);
	} catch (e) {
		if (e instanceof ApiError && e.status === 404) error(404, 'Draft not found');
		// The draft is the page's primary entity: without it there is nothing to show.
		error(503, e instanceof Error ? e.message : 'Draft unavailable');
	}
	const [suggestionsRes, decksRes] = await Promise.allSettled([
		apiGet<DraftSuggestions>(`/api/v2/drafts/${encodeURIComponent(params.id)}/suggestions`),
		apiGet<{ decks: DeckSummary[] }>('/api/v2/decks')
	]);
	const suggestions = suggestionsRes.status === 'fulfilled' ? suggestionsRes.value : null;
	const suggestionsError =
		suggestionsRes.status === 'rejected'
			? suggestionsRes.reason instanceof Error
				? suggestionsRes.reason.message
				: 'suggestions unavailable'
			: null;
	// deck id -> name, so no page text ever shows a raw deck id. null = the deck list failed.
	const deckNames: Record<string, string> | null =
		decksRes.status === 'fulfilled'
			? Object.fromEntries(decksRes.value.decks.map((d) => [d.id, d.name]))
			: null;
	return { state, suggestions, suggestionsError, deckNames };
};

function actionError(e: unknown) {
	if (e instanceof ApiError) return fail(e.status, { actionError: e.message });
	throw e;
}

const path = (id: string) => `/api/v2/drafts/${encodeURIComponent(id)}`;

export const actions: Actions = {
	upsert_card: async ({ request, params }) => {
		const form = await request.formData();
		const oracle_id = String(form.get('oracle_id') ?? '').trim();
		const name = String(form.get('name') ?? '').trim();
		const qty = Number(form.get('qty') ?? 1);
		// The API's board set says `maybe`; accept the card-search spelling too.
		const board = toApiBoard(String(form.get('board') ?? 'main'));
		if (!oracle_id || !Number.isInteger(qty) || qty < 1 || !board) {
			return fail(400, { actionError: 'Pick a card, a board, and a quantity of at least 1.' });
		}
		try {
			await apiPost(`${path(params.id)}/cards`, { oracle_id, name, qty, board });
		} catch (e) {
			return actionError(e);
		}
	},
	// No-JS path: resolve the typed name against the owned collection, then stage it (adds to any existing qty).
	upsert_card_by_name: async ({ request, params, fetch }) => {
		const form = await request.formData();
		const name = String(form.get('card_name') ?? '').trim();
		const qty = Number(form.get('qty') ?? 1);
		const board = toApiBoard(String(form.get('board') ?? 'main'));
		if (!name || !Number.isInteger(qty) || qty < 1 || !board) {
			return fail(400, { actionError: 'Type a card name, a board, and a quantity of at least 1.' });
		}
		try {
			const qs = new URLSearchParams({ name, owned: 'owned', page_size: '5' });
			const res = await fetch(`${API_URL}/api/v2/cards/search?${qs}`, { headers: { accept: 'application/json' } });
			if (!res.ok) return fail(res.status >= 500 ? 503 : res.status, { actionError: 'Card search is unavailable right now.' });
			const body = (await res.json()) as { results?: Array<{ oracle_id: string; name: string }> };
			const hit = pickExactMatch(body.results ?? [], name);
			if (!hit) return fail(400, { actionError: `No owned card named “${name}”.` });
			const state = await apiGet<DraftState>(path(params.id));
			const existing = state.cards.find((c) => c.oracle_id === hit.oracle_id && c.board === board)?.qty ?? 0;
			await apiPost(`${path(params.id)}/cards`, { oracle_id: hit.oracle_id, name: hit.name, qty: existing + qty, board });
		} catch (e) {
			return actionError(e);
		}
	},
	remove_card: async ({ request, params }) => {
		const form = await request.formData();
		const oracle_id = String(form.get('oracle_id') ?? '');
		const board = String(form.get('board') ?? 'main');
		try {
			await apiDelete(`${path(params.id)}/cards/${encodeURIComponent(oracle_id)}/${encodeURIComponent(board)}`);
		} catch (e) {
			return actionError(e);
		}
	},
	stage_move: async ({ request, params }) => {
		const form = await request.formData();
		const instance_id = String(form.get('instance_id') ?? '').trim();
		const moveAction = String(form.get('move_action') ?? 'assign');
		if (!instance_id) return fail(400, { actionError: 'Paste a copy ID to stage a move.' });
		try {
			await apiPost(`${path(params.id)}/moves`, { instance_id, action: moveAction });
		} catch (e) {
			return actionError(e);
		}
	},
	unstage_move: async ({ request, params }) => {
		const form = await request.formData();
		const instance_id = String(form.get('instance_id') ?? '');
		try {
			await apiDelete(`${path(params.id)}/moves/${encodeURIComponent(instance_id)}`);
		} catch (e) {
			return actionError(e);
		}
	},
	// Commit and Abandon need `confirmed=1`. With JS the dialog sends it; without JS the first POST
	// returns `needsConfirm` and the page renders the same summary inline (page-specs §2.6).
	commit: async ({ request, params }) => {
		const form = await request.formData();
		if (form.get('confirmed') !== '1') return fail(400, { needsConfirm: 'commit' as const });
		let deckId: string;
		try {
			const result = await apiPost<{ deck_id: string }>(`${path(params.id)}/commit`, {});
			deckId = result.deck_id;
		} catch (e) {
			return actionError(e);
		}
		redirect(303, `/decks/${encodeURIComponent(deckId)}?from_draft=1`);
	},
	abandon: async ({ request, params }) => {
		const form = await request.formData();
		if (form.get('confirmed') !== '1') return fail(400, { needsConfirm: 'abandon' as const });
		try {
			await apiPost(`${path(params.id)}/abandon`, {});
		} catch (e) {
			return actionError(e);
		}
		redirect(303, '/drafts');
	}
};
