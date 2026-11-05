// Pure helpers behind CardCombobox: option shape, keyboard movement, card/copy mapping.
// No Svelte or DOM imports, so they unit-test without a renderer.

export interface ComboOption<T = unknown> {
	/** Unique within a result list; used for the option element id. Never shown. */
	key: string;
	/** Primary text, the card name. */
	label: string;
	/** Human-friendly secondary line (type line, set, condition...). */
	detail?: string;
	/** Internal value (oracle id / instance id). Never rendered as text. */
	value: T;
}

export const SEARCH = { debounceMs: 250, minChars: 2, limit: 10 } as const;

export type ComboStatus = 'idle' | 'short' | 'loading' | 'results' | 'empty' | 'error';

/** Next highlighted index for a key; -1 means "none" (focus stays in the input). Wraps. */
export function nextIndex(
	key: 'ArrowDown' | 'ArrowUp' | 'Home' | 'End',
	current: number,
	count: number
): number {
	if (count <= 0) return -1;
	switch (key) {
		case 'Home':
			return 0;
		case 'End':
			return count - 1;
		case 'ArrowDown':
			return current < 0 || current >= count - 1 ? 0 : current + 1;
		case 'ArrowUp':
			return current <= 0 ? count - 1 : current - 1;
	}
}

/** Status line text for the non-result states; null when the list itself speaks. */
export function statusText(status: ComboStatus, term: string, minChars: number, error = ''): string | null {
	switch (status) {
		case 'short':
			return `Type at least ${minChars} letters to search`;
		case 'loading':
			return 'Searching…';
		case 'empty':
			return `No cards match “${term.trim()}”`;
		case 'error':
			return error ? `Search failed: ${error}` : 'Search failed';
		default:
			return null;
	}
}

// --- mapping of the existing search endpoints to options -------------------------------------

/** The slice of `GET /api/card-search` rows used here (CardHit). */
export interface CardHitLike {
	oracle_id: string;
	name: string;
	mana_cost?: string;
	type_line?: string;
	owned_qty?: number;
}

export interface CardChoice {
	oracleId: string;
	name: string;
}

export function cardOption(hit: CardHitLike): ComboOption<CardChoice> {
	const bits = [hit.type_line, hit.mana_cost].filter(Boolean) as string[];
	if (typeof hit.owned_qty === 'number') bits.push(`${hit.owned_qty} owned`);
	return {
		key: hit.oracle_id,
		label: hit.name,
		detail: bits.join(' · ') || undefined,
		value: { oracleId: hit.oracle_id, name: hit.name }
	};
}

/** The slice of `GET /api/instances` records used here. */
export interface InstanceLike {
	id: string;
	card_name: string;
	set?: string;
	set_name?: string;
	collector_number?: string;
	finish?: string;
	condition?: string;
	ownership_status: string;
	deck_name?: string;
}

const STATUS_LABEL: Record<string, string> = {
	in_collection: 'in collection',
	in_deck: 'in deck',
	in_mail: 'in the mail',
	not_owned: 'not owned'
};

export function copyOption(inst: InstanceLike): ComboOption<string> {
	const printing = [inst.set_name || inst.set?.toUpperCase(), inst.collector_number ? `#${inst.collector_number}` : '']
		.filter(Boolean)
		.join(' ');
	const where =
		inst.ownership_status === 'in_deck' && inst.deck_name
			? `in ${inst.deck_name}`
			: (STATUS_LABEL[inst.ownership_status] ?? inst.ownership_status.replace(/_/g, ' '));
	const finish = inst.finish && inst.finish !== 'nonfoil' ? inst.finish : '';
	return {
		key: inst.id,
		label: inst.card_name,
		detail: [printing, finish, inst.condition, where].filter(Boolean).join(' · '),
		value: inst.id
	};
}

/** Which copy statuses a staged move may start from (mirrors draft_mode.validate_move). */
export function copyStatusesFor(action: string): string[] {
	return action === 'unassign' ? ['in_deck'] : ['in_collection', 'in_deck'];
}
