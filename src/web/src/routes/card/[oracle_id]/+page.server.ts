import { error, fail } from '@sveltejs/kit';
import { apiGet, apiPut, ApiError, type CardDossier, type DossierAffinity } from '#lib/api.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url }) => {
	const printing = url.searchParams.get('printing');
	const query = printing ? `?printing=${encodeURIComponent(printing)}` : '';
	let dossier: CardDossier;
	try {
		dossier = await apiGet<CardDossier>(
			`/api/v2/card/${encodeURIComponent(params.oracle_id)}/dossier${query}`
		);
	} catch (e) {
		if (e instanceof ApiError && e.status === 404) error(404, 'Card not found');
		throw e;
	}
	return { dossier };
};

export const actions: Actions = {
	save_notes: async ({ request, params }) => {
		const form = await request.formData();
		const rawAffinity = String(form.get('affinity') ?? '').trim();
		const affinity = rawAffinity === '' ? null : Number(rawAffinity);
		if (affinity !== null && (!Number.isInteger(affinity) || affinity < 1 || affinity > 5)) {
			return fail(400, { actionError: 'Affinity must be a whole number from 1 to 5 (or empty).' });
		}
		const notes = String(form.get('notes') ?? '');
		try {
			const result = await apiPut<{ affinity: DossierAffinity }>(
				`/api/v2/card/${encodeURIComponent(params.oracle_id)}/notes`,
				{ affinity, notes }
			);
			return { savedAffinity: result.affinity };
		} catch (e) {
			if (e instanceof ApiError) return fail(e.status, { actionError: e.message });
			throw e;
		}
	}
};
