import { apiGet, type InventoryResponse } from '#lib/api.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ url }) => {
	const q = (url.searchParams.get('q') ?? '').trim();
	try {
		const data = await apiGet<InventoryResponse>(
			`/api/v2/inventory${q ? `?q=${encodeURIComponent(q)}` : ''}`
		);
		return { inventory: data.inventory, totals: data.totals, q, error: null as string | null };
	} catch (e) {
		return {
			inventory: [],
			totals: { cards: 0, owned: 0, in_collection: 0, in_deck: 0, in_mail: 0 },
			q,
			error: e instanceof Error ? e.message : 'unavailable'
		};
	}
};
