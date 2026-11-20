import { fail, redirect } from '@sveltejs/kit';
import { apiGet, apiPost, ApiError, type DeckSummary, type DraftRecord, type DraftState } from '#lib/api.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async () => {
	try {
		const [drafts, decks] = await Promise.all([
			apiGet<{ drafts: DraftRecord[] }>('/api/v2/drafts'),
			apiGet<{ decks: DeckSummary[] }>('/api/v2/decks')
		]);
		if (!Array.isArray(drafts?.drafts) || !Array.isArray(decks?.decks)) throw new Error('Draft data came back incomplete');
		return { drafts: drafts.drafts, decks: decks.decks, error: null as string | null };
	} catch (e) {
		return {
			drafts: [] as DraftRecord[],
			decks: [] as DeckSummary[],
			error: e instanceof Error ? e.message : 'Draft data unavailable'
		};
	}
};

export const actions: Actions = {
	default: async ({ request }) => {
		const form = await request.formData();
		const kind = String(form.get('kind') ?? 'combine');
		const source_deck_ids = form.getAll('source_deck_ids').map(String).filter(Boolean);
		const name = String(form.get('name') ?? '').trim();
		const target_name = String(form.get('target_name') ?? '').trim();
		const prefill = form.get('prefill') === 'on';
		if (!name || !target_name || source_deck_ids.length === 0) {
			return fail(400, { createError: 'Name the draft and the new deck, and pick at least one source deck.' });
		}
		try {
			const state = await apiPost<DraftState>('/api/v2/drafts', {
				kind, source_deck_ids, name, target_name, prefill
			});
			redirect(303, `/drafts/${state.draft.id}`);
		} catch (e) {
			if (e instanceof ApiError) return fail(e.status, { createError: e.message });
			throw e;
		}
	}
};
