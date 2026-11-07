import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
	cleanForSend, formModelToFormData, formToValues, mapServerReason, profileToForm, validate, type Values
} from './taste-form.ts';
import { leafKeys } from './provenance.ts';

const seed: Values = JSON.parse(readFileSync(resolve(process.cwd(), '../../profiles/andrew.json'), 'utf8'));

/** A profile as GET returns it: resolved defaults plus metadata. */
const resolved = (p: Values): Values => ({
	metadata: { source: 'seed', n: 1 },
	...p,
	pet_cards: p.pet_cards ?? [],
	provenance: undefined
});
const roundTrip = (p: Values) => formToValues(formModelToFormData(profileToForm(p)), p);

describe('round trip', () => {
	it('preserves the seed profile exactly (no-edit save)', () => {
		const p = resolved(seed);
		delete p.provenance;
		expect(roundTrip(p)).toEqual(p);
	});

	it('preserves a rich fixture, metadata and name', () => {
		const p: Values = {
			name: 'andrew',
			metadata: { origin: { a: [1, 2] } },
			builder_identity: { text: 'line1\nline2', tags: ['x', 'fav:Elspeth'] },
			color_affinities: { Bant: 0.8, W: 0.05 },
			archetype_affinities: {},
			favored_mechanics: ['landfall'],
			pet_cards: ['Fire // Ice', 'Mr. Orfeo'],
			dislikes: { cards: ['Sol Ring'], strategies: [], play_patterns: ['stax'] },
			power_tendencies: { tier: 'focused', notes: 'n' },
			budget_norms: { hard_cap: 50, soft_cap: 25.5, currency: 'EUR' },
			review_preferences: { depth: 'deep', tone: null, skip: ['padding'], welcome_generic_staples: false },
			done_when: ['a', 'b']
		};
		expect(roundTrip(p)).toEqual(p);
		expect(leafKeys(roundTrip(p))).toEqual(leafKeys(p));
	});

	it('keeps welcome_generic_staples three-state', () => {
		for (const v of [true, false, null]) {
			const p = { ...resolved(seed), review_preferences: { ...seed.review_preferences, welcome_generic_staples: v } };
			expect(roundTrip(p).review_preferences.welcome_generic_staples).toBe(v);
		}
	});

	it('sends the JSON shape the API expects (only known top-level keys, no provenance)', () => {
		const out = roundTrip(resolved(seed));
		expect(Object.keys(out).sort()).toEqual(
			['archetype_affinities', 'budget_norms', 'builder_identity', 'color_affinities', 'done_when', 'dislikes', 'favored_mechanics', 'metadata', 'name', 'pet_cards', 'power_tendencies', 'review_preferences'].sort()
		);
	});
});

describe('formToValues edits', () => {
	const base = resolved(seed);
	it('drops removed rows, blank rows and trims', () => {
		const fd = formModelToFormData(profileToForm(base));
		fd.append('pet_cards', '  Elspeth  ');
		fd.append('pet_cards', '   ');
		expect(formToValues(fd, base).pet_cards).toEqual(['Elspeth']);
		const fd2 = formModelToFormData(profileToForm(base));
		fd2.set('builder_identity.tags', 'a');
		fd2.append('builder_identity.tags', 'b');
		fd2.append('builder_identity.tags__remove', '0');
		expect(formToValues(fd2, base).builder_identity.tags).toEqual(['b']);
	});
	it('parses caps and clears them when empty', () => {
		const fd = formModelToFormData(profileToForm(base));
		fd.set('budget_norms.hard_cap', '12.5');
		fd.set('budget_norms.soft_cap', '');
		const v = formToValues(fd, base);
		expect(v.budget_norms.hard_cap).toBe(12.5);
		expect(v.budget_norms.soft_cap).toBeNull();
	});
});

describe('validate', () => {
	const base = resolved(seed);
	const edited = (fn: (fd: FormData) => void) => {
		const fd = formModelToFormData(profileToForm(base));
		fn(fd);
		return validate(formToValues(fd, base));
	};
	it('passes the seed', () => expect(validate(base)).toEqual([]));
	it('flags soft > hard', () => {
		const e = edited((fd) => { fd.set('budget_norms.hard_cap', '50'); fd.set('budget_norms.soft_cap', '60'); });
		expect(e.map((x) => x.path)).toEqual(['budget_norms.soft_cap']);
	});
	it('flags non-numeric and negative caps', () => {
		expect(edited((fd) => fd.set('budget_norms.hard_cap', 'abc')).length).toBe(1);
		expect(edited((fd) => fd.set('budget_norms.hard_cap', '-1')).length).toBe(1);
	});
	it('flags weights outside 0..1, duplicates and empty labels', () => {
		expect(edited((fd) => { fd.set('color_affinities__weight', '1.5'); }).some((e) => e.path === 'color_affinities')).toBe(true);
		const dup = edited((fd) => { fd.append('color_affinities__label', 'bant'); fd.append('color_affinities__weight', '0.1'); });
		expect(dup.some((e) => /listed twice/.test(e.message))).toBe(true);
		const nolabel = edited((fd) => { fd.append('color_affinities__label', ''); fd.append('color_affinities__weight', '0.3'); });
		expect(nolabel.some((e) => /label/.test(e.message))).toBe(true);
	});
	it('flags duplicate list items case-insensitively', () => {
		const e = edited((fd) => { fd.append('builder_identity.tags', 'Builder-First'); });
		expect(e.some((x) => x.path === 'builder_identity.tags' && /twice/.test(x.message))).toBe(true);
	});
	it('flags a card that is both pet and disliked, on both fields', () => {
		const e = edited((fd) => { fd.append('pet_cards', 'Sol  Ring'); fd.append('dislikes.cards', 'sol ring'); });
		expect(e.map((x) => x.path).sort()).toEqual(['dislikes.cards', 'pet_cards']);
	});
	it('cleanForSend strips duplicate markers', () => {
		const fd = formModelToFormData(profileToForm(base));
		fd.append('color_affinities__label', 'Bant'); fd.append('color_affinities__weight', '0.1');
		const sent = cleanForSend(formToValues(fd, base));
		expect(Object.keys(sent.color_affinities).every((k) => !k.includes('\u0000'))).toBe(true);
	});
});

describe('mapServerReason', () => {
	it('maps the soft cap 422 to its field', () => {
		const r = mapServerReason('Invalid player profile: budget_norms.soft_cap (60.0) cannot exceed hard_cap (50.0).');
		expect(r.fields).toHaveLength(1);
		expect(r.fields[0].path).toBe('budget_norms.soft_cap');
		expect(r.unmapped).toEqual([]);
	});
	it('maps schema locs with an index and leaves the rest unmapped', () => {
		const r = mapServerReason('pet_cards.0: \'\' is too short; Card conflict: \'X\' cannot be both a pet card and disliked.');
		expect(r.fields.map((f) => f.path)).toEqual(['pet_cards']);
		expect(r.unmapped).toHaveLength(1);
	});
});
