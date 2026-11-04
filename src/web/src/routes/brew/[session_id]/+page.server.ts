import { error, fail, isHttpError } from '@sveltejs/kit';
import { API_URL, apiDelete, apiGet, apiPost, ApiError } from '#lib/api.ts';
import type { Color } from '#lib/card-search/types.ts';
import type { Actions, PageServerLoad } from './$types';

/** One line of the brewed list (`POST/DELETE /api/v2/brew/{id}/cards` and `GET /api/brew/{id}` agree on this). */
export interface BrewLine {
	name: string;
	quantity: number;
	tier: number;
	tier_label: string;
	cost?: number | null;
	oracle_id?: string | null;
}

export interface BrewSession {
	session_id: string;
	status: string;
	name?: string;
	commanders?: string[];
	build_mode?: string;
	color_identity?: string[];
	drafts?: { name?: string; cards?: BrewLine[]; commanders?: string[] }[];
}

const COLORS = ['W', 'U', 'B', 'R', 'G', 'C'];

/** Session identity: the API's `color_identity` when it sends one, else the union over the commanders' autocomplete hits. */
async function identityOf(session: BrewSession, commanders: string[], fetcher: typeof fetch): Promise<Color[] | undefined> {
	if (Array.isArray(session.color_identity)) return session.color_identity.filter((c) => COLORS.includes(c)) as Color[];
	const found = new Set<string>();
	for (const name of commanders) {
		try {
			// The shared autocomplete (Scryfall-backed, no database) returns each hit's color_identity.
			const res = await fetcher(`${API_URL}/api/cards/autocomplete?${new URLSearchParams({ q: name, limit: '8' })}`);
			if (!res.ok) return undefined;
			const body = (await res.json()) as { results?: { name: string; color_identity?: string[] }[] };
			const hit = body.results?.find((h) => h.name.toLowerCase() === name.toLowerCase());
			if (!hit?.color_identity) return undefined;
			for (const c of hit.color_identity) found.add(c);
		} catch {
			return undefined; // unknown identity: search unrestricted rather than wrongly restricted
		}
	}
	return COLORS.filter((c) => found.has(c) && c !== 'C') as Color[];
}

export const load: PageServerLoad = async ({ params, fetch }) => {
	let session: BrewSession;
	try {
		const body = await apiGet<{ session: BrewSession }>(`/api/brew/${encodeURIComponent(params.session_id)}`);
		session = body.session;
		if (!session || typeof session !== 'object') error(502, 'Brew data came back incomplete (missing "session")');
	} catch (e) {
		if (isHttpError(e)) throw e;
		if (e instanceof ApiError && e.status === 404) error(404, 'Brew session not found');
		error(503, e instanceof Error ? e.message : 'Brew data unavailable');
	}
	const draft = session.drafts?.[0];
	const commanders = session.commanders?.length ? session.commanders : (draft?.commanders ?? []);
	const identity = await identityOf(session, commanders, fetch);
	return {
		sessionId: params.session_id,
		status: session.status,
		name: session.name ?? draft?.name ?? '',
		commanders,
		ownedDefault: session.build_mode === 'best_build' ? ('any' as const) : ('owned' as const),
		identity,
		cards: (draft?.cards ?? []) as BrewLine[],
		// Origin of the classic (Jinja) app. Empty = derive in the browser (compose: web :8011 -> app :8010).
		legacyOrigin: (process.env.COMMANDER_LEGACY_URL ?? '').replace(/\/$/, '')
	};
};

function failure(e: unknown, action: string) {
	if (e instanceof ApiError) return fail(e.status, { actionError: e.message, action });
	return fail(503, { actionError: e instanceof Error ? e.message : 'Brew service unavailable', action });
}

export const actions: Actions = {
	add: async ({ request, params }) => {
		const form = await request.formData();
		const oracle_id = String(form.get('oracle_id') ?? '').trim();
		const name = String(form.get('name') ?? '').trim();
		const qty = Number(form.get('qty') ?? 1);
		if (!oracle_id || !name || !Number.isInteger(qty) || qty < 1) {
			return fail(400, { actionError: 'Card needs an oracle id, a name and a qty ≥ 1.', action: 'add' });
		}
		try {
			const out = await apiPost<{ draft: { cards: BrewLine[] } }>(
				`/api/v2/brew/${encodeURIComponent(params.session_id)}/cards`,
				{ oracle_id, name, qty }
			);
			return { action: 'add', cards: out.draft.cards };
		} catch (e) {
			return failure(e, 'add');
		}
	},
	remove: async ({ request, params }) => {
		const form = await request.formData();
		const oracle_id = String(form.get('oracle_id') ?? '').trim();
		if (!oracle_id) return fail(400, { actionError: 'Missing oracle id.', action: 'remove' });
		try {
			const out = await apiDelete<{ draft: { cards: BrewLine[] } }>(
				`/api/v2/brew/${encodeURIComponent(params.session_id)}/cards/${encodeURIComponent(oracle_id)}`
			);
			return { action: 'remove', cards: out.draft.cards };
		} catch (e) {
			return failure(e, 'remove');
		}
	}
};
