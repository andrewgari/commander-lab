// Pure: Filters <-> URLSearchParams, defaults, isEmpty (docs/ui/card-search.md §2, §3).
// No Svelte or DOM imports, so it runs under `node --test`.

import type {
	CardType,
	CiMode,
	Color,
	Filters,
	ManaValue,
	OwnedFilter,
	Rarity
} from './types.ts';

/** Same-origin endpoints (SvelteKit proxy routes; the browser never talks to FastAPI or Scryfall). */
export const CARD_SEARCH_PATH = '/api/card-search';
export const CARD_AUTOCOMPLETE_PATH = '/api/card-autocomplete';

/** Timing and sizing from §4 / §4.1. */
export const QUICK_ADD = { debounceMs: 200, minChars: 2, limit: 8 } as const;
export const PANEL = { debounceMs: 300, pageSize: 60 } as const;

// Canonical orders: toParams always emits values in these orders so equal filters give equal URLs.
export const COLORS: readonly Color[] = ['W', 'U', 'B', 'R', 'G', 'C'];
export const CARD_TYPES: readonly CardType[] = [
	'creature',
	'instant',
	'sorcery',
	'artifact',
	'enchantment',
	'planeswalker',
	'land',
	'battle'
];
export const MANA_VALUES: readonly ManaValue[] = ['0', '1', '2', '3', '4', '5', '6', '7+'];
export const RARITIES: readonly Rarity[] = ['common', 'uncommon', 'rare', 'mythic'];
export const CI_MODES: readonly CiMode[] = ['within', 'exact', 'includes'];
export const OWNED_VALUES: readonly OwnedFilter[] = ['any', 'owned', 'not_owned'];

export const DEFAULT_CI_MODE: CiMode = 'within';
export const DEFAULT_OWNED: OwnedFilter = 'any';

export function emptyFilters(): Filters {
	return {
		name: '',
		ci: [],
		ci_mode: DEFAULT_CI_MODE,
		type: [],
		type_text: '',
		mv: [],
		rarity: [],
		oracle: '',
		owned: DEFAULT_OWNED,
		commander: false,
		pair: null
	};
}

/** Keep only known values, drop duplicates, and put them in canonical order. */
function canonical<T extends string>(values: readonly string[] | undefined, order: readonly T[]): T[] {
	const wanted = new Set((values ?? []).map((v) => String(v).trim().toLowerCase()));
	return order.filter((o) => wanted.has(o.toLowerCase()));
}

function pick<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
	return allowed.find((a) => a === value) ?? fallback;
}

/** `C` (colorless) is exclusive with the five colors: if both are given, the colors win. */
export function normalizeColors(values: readonly string[] | undefined): Color[] {
	const ci = canonical(values, COLORS);
	return ci.length > 1 ? ci.filter((c) => c !== 'C') : ci;
}

/** Toggle one color chip, keeping `C` exclusive with W U B R G. */
export function toggleColor(ci: readonly Color[], color: Color): Color[] {
	if (ci.includes(color)) return normalizeColors(ci.filter((c) => c !== color));
	if (color === 'C') return ['C'];
	return normalizeColors([...ci.filter((c) => c !== 'C'), color]);
}

/**
 * Coerce any partial filter state into a valid, canonical Filters: unknown values dropped, lists
 * deduped and ordered, text trimmed, and `pair` cleared unless `commander` is on (the server
 * answers `pair` without `cmdr=1` with a 400, §5.1).
 */
export function normalizeFilters(input: Partial<Filters> = {}): Filters {
	const commander = input.commander === true;
	const pair = typeof input.pair === 'string' ? input.pair.trim() : '';
	return {
		name: (input.name ?? '').trim(),
		ci: normalizeColors(input.ci),
		ci_mode: pick(input.ci_mode, CI_MODES, DEFAULT_CI_MODE),
		type: canonical(input.type, CARD_TYPES),
		type_text: (input.type_text ?? '').trim(),
		mv: canonical(input.mv, MANA_VALUES),
		rarity: canonical(input.rarity, RARITIES),
		oracle: (input.oracle ?? '').trim(),
		owned: pick(input.owned, OWNED_VALUES, DEFAULT_OWNED),
		commander,
		pair: commander && pair ? pair : null
	};
}

function splitList(raw: string | null): string[] {
	return raw ? raw.split(',').map((s) => s.trim()).filter(Boolean) : [];
}

/**
 * Filters -> params (§3 "Param" column). Defaults and empty filters are omitted, so
 * `toParams(emptyFilters())` is empty. `cmdr=1` is present only when the filter is on.
 */
export function toParams(filters: Partial<Filters>): URLSearchParams {
	const f = normalizeFilters(filters);
	const p = new URLSearchParams();
	if (f.name) p.set('name', f.name);
	if (f.ci.length) p.set('ci', f.ci.join('').toLowerCase());
	if (f.ci_mode !== DEFAULT_CI_MODE) p.set('ci_mode', f.ci_mode);
	if (f.type.length) p.set('type', f.type.join(','));
	if (f.type_text) p.set('type_text', f.type_text);
	if (f.mv.length) p.set('mv', f.mv.join(','));
	if (f.rarity.length) p.set('rarity', f.rarity.join(','));
	if (f.oracle) p.set('oracle', f.oracle);
	if (f.owned !== DEFAULT_OWNED) p.set('owned', f.owned);
	if (f.commander) p.set('cmdr', '1');
	if (f.pair) p.set('pair', f.pair);
	return p;
}

/** Params -> Filters. Lenient: unknown params and invalid values are ignored. Inverse of `toParams`. */
export function fromParams(params: URLSearchParams | string): Filters {
	const p = typeof params === 'string' ? new URLSearchParams(params) : params;
	return normalizeFilters({
		name: p.get('name') ?? '',
		ci: (p.get('ci') ?? '').toUpperCase().split('') as Color[],
		ci_mode: (p.get('ci_mode') ?? '') as CiMode,
		type: splitList(p.get('type')) as CardType[],
		type_text: p.get('type_text') ?? '',
		mv: splitList(p.get('mv')) as ManaValue[],
		rarity: splitList(p.get('rarity')) as Rarity[],
		oracle: p.get('oracle') ?? '',
		owned: (p.get('owned') ?? '') as OwnedFilter,
		commander: p.get('cmdr') === '1',
		pair: p.get('pair')
	});
}

/**
 * Filter chips set on the panel, as the "N filters" count shows them. Type and type text are one
 * filter, `ci_mode` and `pair` are modifiers of other filters, so none of those count separately.
 */
export function activeFilterCount(filters: Partial<Filters>): number {
	const f = normalizeFilters(filters);
	return [
		f.name !== '',
		f.ci.length > 0,
		f.type.length > 0 || f.type_text !== '',
		f.mv.length > 0,
		f.rarity.length > 0,
		f.oracle !== '',
		f.owned !== DEFAULT_OWNED,
		f.commander
	].filter(Boolean).length;
}

/** True when no filter is set: the panel then shows a hint and makes no request (§4.1). */
export function isEmpty(filters: Partial<Filters>): boolean {
	return activeFilterCount(filters) === 0;
}

export interface PageOptions {
	/** 1-based. */
	page?: number;
	/** Server default 60, max 100. */
	page_size?: number;
}

/** Params for `GET /api/card-search`: the filters plus paging. */
export function searchParams(filters: Partial<Filters>, opts: PageOptions = {}): URLSearchParams {
	const p = toParams(filters);
	if (opts.page !== undefined) p.set('page', String(Math.max(1, Math.trunc(opts.page))));
	if (opts.page_size !== undefined) p.set('page_size', String(Math.max(1, Math.trunc(opts.page_size))));
	return p;
}

export function searchUrl(filters: Partial<Filters>, opts: PageOptions = {}): string {
	const qs = searchParams(filters, opts).toString();
	return qs ? `${CARD_SEARCH_PATH}?${qs}` : CARD_SEARCH_PATH;
}

export interface QuickAddSuggestOptions {
	limit?: number;
	/** The Can be commander chip is on (§4 "Quick-add while Can be commander is on"). */
	commander?: boolean;
	/** oracle_id of the first commander (`pairWith`); needs `commander`. */
	pair?: string | null;
}

/**
 * URL for the quick-add suggestion list (§4). Normal and commander scope use the autocomplete
 * endpoint; a partner search uses `card-search` because the autocomplete has no pairing clause.
 */
export function suggestUrl(term: string, opts: QuickAddSuggestOptions = {}): string {
	const limit = opts.limit ?? QUICK_ADD.limit;
	if (opts.commander && opts.pair) {
		return searchUrl(
			{ commander: true, pair: opts.pair, name: term },
			{ page_size: limit }
		);
	}
	const p = new URLSearchParams({ q: term.trim(), limit: String(limit) });
	if (opts.commander) p.set('commanders_only', 'true');
	return `${CARD_AUTOCOMPLETE_PATH}?${p.toString()}`;
}

/** Whether `term` is long enough to ask for suggestions at all (§4.3). */
export function canSuggest(term: string): boolean {
	return term.trim().length >= QUICK_ADD.minChars;
}
