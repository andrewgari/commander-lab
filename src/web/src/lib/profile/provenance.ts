// Provenance keys and badge state for the taste editor. The key format mirrors
// `leaf_keys` in src/server/domain/profile/player_profile.py exactly:
//   scalar -> `budget_norms.hard_cap`, list item -> `pet_cards/Elspeth`, weight -> `color_affinities.Bant`.

export interface Provenance {
	source: 'stated' | 'learned_from_feedback';
	at: string;
	evidence?: string[];
}

export type BadgeKind = 'stated' | 'learned' | 'unsaved' | null;

export interface Badge {
	kind: BadgeKind;
	prov?: Provenance;
}

/** Same order as Python's `_VALUE_FIELDS`. */
export const VALUE_FIELDS = [
	'builder_identity',
	'color_affinities',
	'archetype_affinities',
	'favored_mechanics',
	'pet_cards',
	'dislikes',
	'power_tendencies',
	'budget_norms',
	'review_preferences',
	'done_when'
] as const;

export const CURRENCY_KEY = 'budget_norms.currency';
export const DEFAULT_CURRENCY = 'USD';

export const scalarKey = (path: string) => path;
export const itemKey = (path: string, item: string) => `${path}/${item}`;
export const weightKey = (path: string, label: string) => `${path}.${label}`;

type Json = unknown;

/**
 * Every populated leaf of a resolved profile, as key -> value (list items map to themselves, weights to
 * their number). Same walk as Python `leaf_keys`, so the key set matches the server's.
 */
export function leafEntries(data: Record<string, Json>): Map<string, Json> {
	const out = new Map<string, Json>();
	const walk = (prefix: string, value: Json, inList = false): void => {
		if (Array.isArray(value)) {
			for (const item of value) out.set(`${prefix}/${String(item)}`, item);
		} else if (value !== null && typeof value === 'object') {
			for (const [k, v] of Object.entries(value as Record<string, Json>)) walk(prefix ? `${prefix}.${k}` : k, v, inList);
		} else if (value === null || value === undefined || value === '') {
			return;
		} else {
			out.set(prefix, value);
		}
	};
	for (const f of VALUE_FIELDS) if (f in data) walk(f, data[f]);
	const bn = data.budget_norms as { currency?: string } | undefined;
	if (bn?.currency === DEFAULT_CURRENCY) out.delete(CURRENCY_KEY);
	return out;
}

export function leafKeys(data: Record<string, Json>): string[] {
	return [...leafEntries(data).keys()];
}

const isSet = (v: Json) => !(v === null || v === undefined || v === '');

/**
 * Badge for one leaf given the saved profile. Mirrors the server rule (`apply_stated_edit`): a leaf whose
 * value is unchanged keeps its provenance; a new or changed leaf will become stated on save.
 */
export function badgeFor(
	key: string,
	value: Json,
	saved: { leaves: Map<string, Json>; provenance: Record<string, Provenance> }
): Badge {
	if (!isSet(value)) return { kind: null };
	if (saved.leaves.has(key) && saved.leaves.get(key) === value) {
		const prov = saved.provenance[key];
		if (!prov) return { kind: null };
		return { kind: prov.source === 'learned_from_feedback' ? 'learned' : 'stated', prov };
	}
	return { kind: 'unsaved' };
}

/** True when an unsaved edit replaces a value the server holds as learned. */
export function replacesLearned(
	key: string,
	saved: { provenance: Record<string, Provenance> }
): boolean {
	return saved.provenance[key]?.source === 'learned_from_feedback';
}

/** Group roll-up text for list and weight fields; null when the group is empty. */
export function rollup(kinds: BadgeKind[]): string | null {
	const set = kinds.filter((k) => k !== null);
	if (set.length === 0) return null;
	const learned = set.filter((k) => k === 'learned').length;
	if (learned === 0) return 'All stated';
	if (learned === set.length) return 'All learned';
	return `${learned} learned`;
}
