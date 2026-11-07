import { describe, expect, it } from 'vitest';
import { badgeFor, leafEntries, leafKeys, rollup, itemKey, weightKey, type Provenance } from './provenance.ts';

const stated = (at = '2026-10-01T00:00:00Z'): Provenance => ({ source: 'stated', at });
const learned = (): Provenance => ({ source: 'learned_from_feedback', at: '2026-10-12T00:00:00Z', evidence: ['accepted 3 swaps'] });

describe('leafKeys (mirrors player_profile.leaf_keys)', () => {
	it('forms scalar, list item and weight keys', () => {
		const keys = leafKeys({
			builder_identity: { text: 'hi', tags: ['a', 'b'] },
			color_affinities: { Bant: 0.8 },
			pet_cards: ['Elspeth, Sun\'s Champion'],
			budget_norms: { hard_cap: 50, soft_cap: null, currency: 'EUR' },
			power_tendencies: { tier: null, notes: '' },
			review_preferences: { welcome_generic_staples: false },
			done_when: ['x']
		});
		expect(keys).toContain('builder_identity.text');
		expect(keys).toContain('builder_identity.tags/a');
		expect(keys).toContain('color_affinities.Bant');
		expect(keys).toContain("pet_cards/Elspeth, Sun's Champion");
		expect(keys).toContain('budget_norms.hard_cap');
		expect(keys).toContain('budget_norms.currency');
		expect(keys).toContain('review_preferences.welcome_generic_staples');
		expect(keys).not.toContain('budget_norms.soft_cap');
		expect(keys).not.toContain('power_tendencies.tier');
		expect(keys).not.toContain('power_tendencies.notes');
	});

	it('omits the default currency but keeps false', () => {
		const k = leafKeys({ budget_norms: { currency: 'USD' }, review_preferences: { welcome_generic_staples: false } });
		expect(k).not.toContain('budget_norms.currency');
		expect(k).toContain('review_preferences.welcome_generic_staples');
	});

	it('handles items and labels that contain / and .', () => {
		expect(itemKey('pet_cards', 'Fire // Ice')).toBe('pet_cards/Fire // Ice');
		expect(weightKey('color_affinities', 'a.b/c')).toBe('color_affinities.a.b/c');
		const k = leafKeys({ pet_cards: ['Fire // Ice', 'Mr. Orfeo'], archetype_affinities: { 'a.b/c': 0.5 } });
		expect(k).toEqual(['archetype_affinities.a.b/c', 'pet_cards/Fire // Ice', 'pet_cards/Mr. Orfeo']);
	});

	it('walks fields in the server order', () => {
		const k = leafKeys({ done_when: ['z'], builder_identity: { text: 't' } });
		expect(k).toEqual(['builder_identity.text', 'done_when/z']);
	});
});

describe('badgeFor', () => {
	const profile = { pet_cards: ['A', 'B'], budget_norms: { hard_cap: 50, currency: 'USD' } };
	const saved = {
		leaves: leafEntries(profile),
		provenance: { 'pet_cards/A': stated(), 'pet_cards/B': learned(), 'budget_norms.hard_cap': stated() }
	};
	it('reports the stored source for unchanged leaves', () => {
		expect(badgeFor('pet_cards/A', 'A', saved).kind).toBe('stated');
		expect(badgeFor('pet_cards/B', 'B', saved).kind).toBe('learned');
		expect(badgeFor('pet_cards/B', 'B', saved).prov?.evidence).toEqual(['accepted 3 swaps']);
	});
	it('flips changed or new leaves to unsaved and restores on revert', () => {
		expect(badgeFor('budget_norms.hard_cap', 60, saved).kind).toBe('unsaved');
		expect(badgeFor('pet_cards/C', 'C', saved).kind).toBe('unsaved');
		expect(badgeFor('budget_norms.hard_cap', 50, saved).kind).toBe('stated');
	});
	it('has no badge for unset values', () => {
		expect(badgeFor('budget_norms.soft_cap', null, saved).kind).toBeNull();
		expect(badgeFor('budget_norms.soft_cap', '', saved).kind).toBeNull();
	});
});

describe('rollup', () => {
	it('summarises a group', () => {
		expect(rollup([])).toBeNull();
		expect(rollup([null])).toBeNull();
		expect(rollup(['stated', 'unsaved'])).toBe('All stated');
		expect(rollup(['learned', 'learned'])).toBe('All learned');
		expect(rollup(['learned', 'stated', 'learned'])).toBe('2 learned');
	});
});
