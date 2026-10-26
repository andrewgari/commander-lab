import { fail } from '@sveltejs/kit';
import {
	apiGet,
	apiPut,
	type OverridePutResponse,
	type ProfileHistoryResponse,
	type ProfileLatestResponse,
	type ProfileOverridesResponse
} from '#lib/api.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async () => {
	try {
		const [latest, history, overrides] = await Promise.all([
			apiGet<ProfileLatestResponse>('/api/v2/profile/latest'),
			apiGet<ProfileHistoryResponse>('/api/v2/profile/history?limit=50'),
			apiGet<ProfileOverridesResponse>('/api/v2/profile/overrides')
		]);
		return { latest, history: history.snapshots, overrides, error: null as string | null };
	} catch (e) {
		return {
			latest: { available: false, reason: 'unavailable', snapshot: null },
			history: [],
			overrides: { overrides: {}, known_keys: {} },
			error: e instanceof Error ? e.message : 'unavailable'
		};
	}
};

export const actions: Actions = {
	override: async ({ request }) => {
		const form = await request.formData();
		const key = String(form.get('key') ?? '');
		const kind = String(form.get('kind') ?? 'string');
		const raw = String(form.get('value') ?? '').trim();
		let value: unknown = null;
		if (raw !== '') {
			if (kind === 'number') {
				const n = Number(raw);
				if (Number.isNaN(n)) {
					return fail(422, { key, error: 'must be a number' });
				}
				value = n;
			} else {
				value = raw;
			}
		}
		try {
			const result = await apiPut<OverridePutResponse>(
				`/api/v2/profile/overrides/${encodeURIComponent(key)}`,
				{ value }
			);
			return { key, snapshotId: result.snapshot_id };
		} catch (e) {
			return fail(422, { key, error: e instanceof Error ? e.message : 'override failed' });
		}
	},
	clear: async ({ request }) => {
		const form = await request.formData();
		const key = String(form.get('key') ?? '');
		try {
			const result = await apiPut<OverridePutResponse>(
				`/api/v2/profile/overrides/${encodeURIComponent(key)}`,
				{ value: null }
			);
			return { key, snapshotId: result.snapshot_id };
		} catch (e) {
			return fail(422, { key, error: e instanceof Error ? e.message : 'override failed' });
		}
	}
};
