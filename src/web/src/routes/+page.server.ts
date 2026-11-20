import {
	apiGet,
	type DeckSummary,
	type DraftRecord,
	type GameRecord,
	type InventoryResponse,
	type ProfileLatestResponse
} from '#lib/api.ts';
import { isoDate, type DraftLite, type GameLite, type HomeSources, type PowerLite, type Source } from '#lib/home/stats.ts';
import type { PageServerLoad } from './$types';

async function settle<T, U>(p: Promise<T>, reduce: (v: T) => U): Promise<Source<U>> {
	try {
		return { ok: true, value: reduce(await p) };
	} catch (e) {
		return { ok: false, reason: e instanceof Error && e.message ? e.message : 'unavailable' };
	}
}

/** A partial payload fails only its own source (shown in the banner) instead of crashing the page. */
function need<T>(v: T | null | undefined, what: string): T {
	if (v === null || v === undefined) throw new Error(`${what} response incomplete`);
	return v;
}

const draftLite = (d: DraftRecord): DraftLite => ({
	id: d.id,
	name: d.name || d.target_name,
	closed_at: d.closed_at,
	created_at: d.created_at,
	result_deck_id: d.result_deck_id
});

function powerLite(r: ProfileLatestResponse): PowerLite | null {
	const snap = r?.snapshot;
	const power = snap?.metrics?.power as
		| { available?: boolean; power_level?: number | null; estimated_bracket?: number | null; bracket_name?: string | null }
		| undefined;
	if (!snap || !power || power.available === false) return null;
	return {
		power_level: power.power_level ?? null,
		estimated_bracket: power.estimated_bracket ?? null,
		bracket_name: power.bracket_name ?? null,
		snapshot_date: isoDate(snap.created_at)
	};
}

export const load: PageServerLoad = async () => {
	const [decks, inventory, games, drafts, committed, profile] = await Promise.all([
		settle(apiGet<{ decks: DeckSummary[] }>('/api/v2/decks'), (r) => need(r?.decks, 'decks')),
		// Gap G6: the API returns every row; only `totals` leaves the server.
		settle(apiGet<InventoryResponse>('/api/v2/inventory'), (r) => need(r?.totals, 'inventory')),
		settle(apiGet<{ games: GameRecord[] }>('/api/v2/games?limit=200'), (r): GameLite[] =>
			need(r?.games, 'games').map((g) => ({ id: g.id, deck_id: g.deck_id, played_at: g.played_at, imported_at: g.imported_at, result: g.result }))
		),
		settle(apiGet<{ drafts: DraftRecord[] }>('/api/v2/drafts?status=open'), (r) => need(r?.drafts, 'drafts').map(draftLite)),
		settle(apiGet<{ drafts: DraftRecord[] }>('/api/v2/drafts?status=committed&limit=5'), (r) => need(r?.drafts, 'drafts').map(draftLite)),
		settle(apiGet<ProfileLatestResponse>('/api/v2/profile/latest'), powerLite)
	]);
	const sources: HomeSources = { decks, inventory, games, drafts, committed, profile };
	const now = new Date();
	return {
		sources,
		nowMs: now.getTime(),
		asOf: now.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
	};
};
