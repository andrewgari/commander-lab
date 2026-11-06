// Pure form <-> profile values mapping for the taste editor. The server action and the browser run the same code,
// so a no-JS save and an enhanced save produce an identical PUT body.
import { leafEntries, VALUE_FIELDS } from './provenance.ts';

export type Values = Record<string, any>;

export interface FieldError {
	/** Profile path, e.g. `budget_norms.soft_cap`, `pet_cards`. */
	path: string;
	message: string;
	/** For list and weight rows: the offending item text or weight label. */
	item?: string;
}

export interface ListFieldDef { path: string; label: string; card?: boolean; multiline?: boolean }
export interface WeightFieldDef { path: string; label: string }

export const TIERS = ['casual', 'focused', 'optimized', 'competitive'] as const;
export const CURRENCIES = ['USD', 'EUR', 'GBP', 'CAD', 'AUD'] as const;
export const DEPTHS = ['brief', 'standard', 'deep'] as const;
export const TONES = ['direct', 'neutral', 'encouraging'] as const;

export const LIST_FIELDS: ListFieldDef[] = [
	{ path: 'builder_identity.tags', label: 'Builder tags' },
	{ path: 'favored_mechanics', label: 'Favoured mechanics' },
	{ path: 'pet_cards', label: 'Pet cards', card: true },
	{ path: 'dislikes.cards', label: 'Disliked cards', card: true },
	{ path: 'dislikes.strategies', label: 'Disliked strategies' },
	{ path: 'dislikes.play_patterns', label: 'Disliked play patterns' },
	{ path: 'review_preferences.skip', label: 'Skip in reviews' },
	{ path: 'done_when', label: 'Done when…', multiline: true }
];
export const WEIGHT_FIELDS: WeightFieldDef[] = [
	{ path: 'color_affinities', label: 'Colour affinities' },
	{ path: 'archetype_affinities', label: 'Archetype affinities' }
];
/** Labels for every editable path, used for proposals and error messages. */
export const FIELD_LABELS: Record<string, string> = {
	'builder_identity.text': 'How you build',
	'power_tendencies.tier': 'Power tier',
	'power_tendencies.notes': 'Power notes',
	'budget_norms.hard_cap': 'Hard cap',
	'budget_norms.soft_cap': 'Soft cap',
	'budget_norms.currency': 'Currency',
	'review_preferences.depth': 'Review depth',
	'review_preferences.tone': 'Tone',
	'review_preferences.welcome_generic_staples': 'Generic staples welcome?',
	...Object.fromEntries(LIST_FIELDS.map((f) => [f.path, f.label])),
	...Object.fromEntries(WEIGHT_FIELDS.map((f) => [f.path, f.label]))
};

export const fieldLabel = (path: string) => FIELD_LABELS[path] ?? path;

export function getPath(obj: Values, path: string): any {
	return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
}
function setPath(obj: Values, path: string, value: unknown) {
	const keys = path.split('.');
	let o = obj;
	for (const k of keys.slice(0, -1)) o = o[k] ??= {};
	o[keys[keys.length - 1]] = value;
}

const nl = (s: string) => s.replace(/\r\n?/g, '\n');
const norm = (s: string) => s.trim().replace(/\s+/g, ' ').toLowerCase();

/** What the editor shows initially: raw strings per path. */
export interface FormModel {
	text: Record<string, string>;
	lists: Record<string, string[]>;
	weights: Record<string, { label: string; weight: string }[]>;
	staples: '' | 'yes' | 'no';
}

const TEXT_PATHS = [
	'builder_identity.text',
	'power_tendencies.tier',
	'power_tendencies.notes',
	'budget_norms.hard_cap',
	'budget_norms.soft_cap',
	'budget_norms.currency',
	'review_preferences.depth',
	'review_preferences.tone'
];

export function profileToForm(profile: Values): FormModel {
	const text: Record<string, string> = {};
	for (const p of TEXT_PATHS) {
		const v = getPath(profile, p);
		text[p] = v === null || v === undefined ? '' : String(v);
	}
	if (!text['budget_norms.currency']) text['budget_norms.currency'] = 'USD';
	const lists: FormModel['lists'] = {};
	for (const f of LIST_FIELDS) lists[f.path] = [...((getPath(profile, f.path) as string[] | undefined) ?? [])];
	const weights: FormModel['weights'] = {};
	for (const f of WEIGHT_FIELDS) {
		weights[f.path] = Object.entries((getPath(profile, f.path) as Record<string, number> | undefined) ?? {}).map(
			([label, w]) => ({ label, weight: String(w) })
		);
	}
	const s = getPath(profile, 'review_preferences.welcome_generic_staples');
	return { text, lists, weights, staples: s === true ? 'yes' : s === false ? 'no' : '' };
}

/** FormData equivalent of a model (what a browser would submit); used by tests and the round trip. */
export function formModelToFormData(m: FormModel, extras: { name?: string; metadata?: unknown } = {}): FormData {
	const fd = new FormData();
	for (const [k, v] of Object.entries(m.text)) fd.append(k, v);
	for (const [k, items] of Object.entries(m.lists)) for (const i of items) fd.append(k, i);
	for (const [k, rows] of Object.entries(m.weights)) {
		for (const r of rows) {
			fd.append(`${k}__label`, r.label);
			fd.append(`${k}__weight`, r.weight);
		}
	}
	fd.append('welcome_generic_staples', m.staples);
	if (extras.name !== undefined) fd.append('name', extras.name);
	if (extras.metadata !== undefined) fd.append('metadata', JSON.stringify(extras.metadata));
	return fd;
}

function num(raw: string): number | null {
	const s = raw.trim();
	if (s === '') return null;
	return Number(s); // NaN on garbage; validate() reports it
}

/**
 * Submitted fields -> the PUT `values` object. Starts from `original` so `name`, `metadata` and anything the form
 * doesn't edit pass through unchanged, then overwrites every editable field.
 */
export function formToValues(fd: FormData, original: Values): Values {
	const out: Values = JSON.parse(JSON.stringify(original));
	delete out.provenance;
	const str = (k: string) => nl(String(fd.get(k) ?? ''));

	out.builder_identity ??= {};
	out.builder_identity.text = str('builder_identity.text');
	out.power_tendencies = { tier: str('power_tendencies.tier') || null, notes: str('power_tendencies.notes') };
	out.budget_norms = {
		hard_cap: num(str('budget_norms.hard_cap')),
		soft_cap: num(str('budget_norms.soft_cap')),
		currency: str('budget_norms.currency') || 'USD'
	};
	out.review_preferences ??= {};
	out.review_preferences.depth = str('review_preferences.depth') || null;
	out.review_preferences.tone = str('review_preferences.tone') || null;
	const st = str('welcome_generic_staples');
	out.review_preferences.welcome_generic_staples = st === 'yes' ? true : st === 'no' ? false : null;

	for (const f of LIST_FIELDS) {
		const removed = new Set(fd.getAll(`${f.path}__remove`).map((v) => Number(v)));
		const items = fd
			.getAll(f.path)
			.map((v, i) => (removed.has(i) ? '' : nl(String(v)).trim()))
			.filter((v) => v !== '');
		setPath(out, f.path, items);
	}
	for (const f of WEIGHT_FIELDS) {
		const removed = new Set(fd.getAll(`${f.path}__remove`).map((v) => Number(v)));
		const labels = fd.getAll(`${f.path}__label`);
		const weights = fd.getAll(`${f.path}__weight`);
		const map: Record<string, number> = {};
		labels.forEach((l, i) => {
			const label = String(l).trim();
			const w = String(weights[i] ?? '').trim();
			if (removed.has(i) || (label === '' && w === '')) return;
			// A repeated label would silently overwrite; validate() sees the duplicate through the key list below.
			const key = label in map ? `${label}\u0000dup${i}` : label;
			map[key] = w === '' ? NaN : Number(w);
		});
		setPath(out, f.path, map);
	}
	return out;
}

/** Client mirror of the server's `validate_profile`; the server stays authoritative. */
export function validate(values: Values): FieldError[] {
	const errs: FieldError[] = [];
	const push = (path: string, message: string, item?: string) => errs.push({ path, message, item });

	const hard = getPath(values, 'budget_norms.hard_cap');
	const soft = getPath(values, 'budget_norms.soft_cap');
	for (const [p, v] of [['budget_norms.hard_cap', hard], ['budget_norms.soft_cap', soft]] as const) {
		if (v !== null && v !== undefined && (Number.isNaN(v) || v < 0)) push(p, 'Enter a number, 0 or more, or leave it empty.');
	}
	if (typeof hard === 'number' && typeof soft === 'number' && !Number.isNaN(hard) && !Number.isNaN(soft) && soft > hard) {
		push('budget_norms.soft_cap', `Soft cap (${soft}) can't be higher than the hard cap (${hard}).`);
	}
	const tier = getPath(values, 'power_tendencies.tier');
	if (tier != null && !(TIERS as readonly string[]).includes(tier)) push('power_tendencies.tier', 'Choose a listed tier.');

	for (const f of LIST_FIELDS) {
		const seen = new Set<string>();
		for (const item of (getPath(values, f.path) as string[]) ?? []) {
			const k = norm(item);
			if (seen.has(k)) push(f.path, `"${item}" is listed twice.`, item);
			seen.add(k);
		}
	}
	const pets = new Set(((getPath(values, 'pet_cards') as string[]) ?? []).map(norm));
	for (const c of (getPath(values, 'dislikes.cards') as string[]) ?? []) {
		if (pets.has(norm(c))) {
			push('dislikes.cards', `"${c}" is also a pet card. A card can't be both.`, c);
			push('pet_cards', `"${c}" is also disliked. A card can't be both.`, c);
		}
	}
	for (const f of WEIGHT_FIELDS) {
		const seen = new Set<string>();
		for (const [rawLabel, w] of Object.entries((getPath(values, f.path) as Record<string, number>) ?? {})) {
			const label = rawLabel.split('\u0000dup')[0];
			if (rawLabel.includes('\u0000dup') || seen.has(norm(label))) push(f.path, `"${label}" is listed twice.`, label);
			seen.add(norm(label));
			if (label === '') push(f.path, 'Give this weight a label.', label);
			if (Number.isNaN(w) || w < 0 || w > 1) push(f.path, `Weight for "${label}" must be a number from 0 to 1.`, label);
		}
	}
	return errs;
}

/** Drop the duplicate-marker suffix before sending: only reachable when validate() already failed. */
export function cleanForSend(values: Values): Values {
	const out = JSON.parse(JSON.stringify(values));
	for (const f of WEIGHT_FIELDS) {
		const m = getPath(out, f.path) as Record<string, number>;
		setPath(out, f.path, Object.fromEntries(Object.entries(m).map(([k, v]) => [k.split('\u0000dup')[0], v])));
	}
	return out;
}

const KNOWN_PATHS = [...Object.keys(FIELD_LABELS)];

/**
 * Server 422 -> per-field errors. The server joins "loc: message" with "; " (gap G8), so only a leading known
 * path token can be mapped; the rest goes to the form-level banner.
 */
export function mapServerReason(reason: string): { fields: FieldError[]; unmapped: string[] } {
	const body = reason.replace(/^Invalid player profile:\s*/, '');
	const fields: FieldError[] = [];
	const unmapped: string[] = [];
	for (const part of body.split(/;\s+/)) {
		const path = KNOWN_PATHS.find((p) => part.startsWith(p) && /^(\W|$)/.test(part.slice(p.length)));
		const loose = part.match(/^([a-z_]+(?:\.[a-z_]+)?)(?:\.\d+)?:/)?.[1];
		const hit = path ?? KNOWN_PATHS.find((p) => p === loose);
		if (hit) fields.push({ path: hit, message: part.replace(/^[a-z_.]+(\.\d+)?:\s*/, '') });
		else if (part.trim()) unmapped.push(part.trim());
	}
	return { fields, unmapped };
}

export { VALUE_FIELDS, leafEntries };
