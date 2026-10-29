import { fail } from '@sveltejs/kit';
import {
	apiGet,
	apiPut,
	apiPost,
	type OverridePutResponse,
	type PlayerProfileResponse,
	type ProfileHistoryResponse,
	type ProfileLatestResponse,
	type ProfileOverridesResponse
} from '#lib/api.ts';
import { ApiError } from '#lib/api.ts';
import { cleanForSend, formToValues, mapServerReason, validate } from '#lib/profile/taste-form.ts';
import type { Actions, PageServerLoad } from './$types';

const PLAYER = 'andrew';

export const load: PageServerLoad = async () => {
	let player: PlayerProfileResponse | null = null;
	let playerError: string | null = null;
	try {
		player = await apiGet<PlayerProfileResponse>(`/api/v2/profile/player/${PLAYER}`);
	} catch (e) {
		playerError = e instanceof Error ? e.message : 'unavailable';
	}
	try {
		const [latest, history, overrides] = await Promise.all([
			apiGet<ProfileLatestResponse>('/api/v2/profile/latest'),
			apiGet<ProfileHistoryResponse>('/api/v2/profile/history?limit=50'),
			apiGet<ProfileOverridesResponse>('/api/v2/profile/overrides')
		]);
		return { latest, history: history.snapshots, overrides, player, playerError, error: null as string | null };
	} catch (e) {
		return {
			latest: { available: false, reason: 'unavailable', snapshot: null },
			history: [],
			overrides: { overrides: {}, known_keys: {} },
			player,
			playerError,
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
	},
	seedPlayer: async () => {
		try {
			await apiPost(`/api/v2/profile/player/${PLAYER}/seed`, {});
			return { playerOk: 'seeded' };
		} catch (e) {
			return fail(422, { playerError: e instanceof Error ? e.message : 'seed failed' });
		}
	},
	savePlayer: async ({ request }) => {
		const form = await request.formData();
		// The form is the whole editable surface; name and metadata pass through from hidden fields unchanged.
		let metadata: Record<string, unknown> = {};
		try {
			const raw = String(form.get('metadata') ?? '{}');
			metadata = JSON.parse(raw) ?? {};
		} catch {
			return fail(422, { playerError: 'The saved profile metadata was unreadable. Reload the page and try again.' });
		}
		const values = formToValues(form, { name: String(form.get('name') ?? PLAYER), metadata });
		const fieldErrors = validate(values);
		if (fieldErrors.length) return fail(422, { fieldErrors, playerError: null as string | null });
		try {
			await apiPut(`/api/v2/profile/player/${PLAYER}`, { values: cleanForSend(values) });
			return { playerOk: 'Saved. Your edits are recorded as Stated.' };
		} catch (e) {
			if (e instanceof ApiError && e.status === 422) {
				const { fields, unmapped } = mapServerReason(e.message);
				return fail(422, { fieldErrors: fields, playerError: unmapped.length ? unmapped.join('; ') : fields.length ? null : e.message });
			}
			const status = e instanceof ApiError && e.status >= 500 ? e.status : 502;
			return fail(status, { playerError: e instanceof Error ? e.message : 'save failed' });
		}
	},
	generateProposals: async () => {
		try {
			await apiPost(`/api/v2/profile/player/${PLAYER}/proposals/generate`, {});
			return { playerOk: 'proposals refreshed (nothing applied until you confirm)' };
		} catch (e) {
			return fail(422, { playerError: e instanceof Error ? e.message : 'failed' });
		}
	},
	confirmProposal: async ({ request }) => {
		const id = Number((await request.formData()).get('id'));
		try {
			await apiPost(`/api/v2/profile/player/${PLAYER}/proposals/${id}/confirm`, {});
			return { playerOk: 'confirmed and applied as learned_from_feedback' };
		} catch (e) {
			return fail(409, { proposalError: { id, message: e instanceof ApiError && e.status === 409 ? "You already set this yourself; the suggestion can't overwrite it." : e instanceof Error ? e.message : 'confirm failed' } });
		}
	},
	dismissProposal: async ({ request }) => {
		const id = Number((await request.formData()).get('id'));
		try {
			await apiPost(`/api/v2/profile/player/${PLAYER}/proposals/${id}/dismiss`, {});
			return { playerOk: 'dismissed' };
		} catch (e) {
			return fail(409, { playerError: e instanceof Error ? e.message : 'dismiss failed' });
		}
	}
};
