import { describe, expect, it } from 'vitest';
import { pickExactMatch } from './match';

const hits = [
	{ oracle_id: '1', name: 'Sol Ring' },
	{ oracle_id: '2', name: 'Sol Ring Of Doom' },
	{ oracle_id: '3', name: 'Delver of Secrets // Insectile Aberration' }
];

describe('pickExactMatch', () => {
	it('ignores case and whitespace', () => {
		expect(pickExactMatch(hits, '  sOl  rInG ')?.oracle_id).toBe('1');
	});
	it('returns null when nothing matches exactly', () => {
		expect(pickExactMatch(hits, 'Sol')).toBeNull();
		expect(pickExactMatch([], 'Sol Ring')).toBeNull();
		expect(pickExactMatch(hits, '  ')).toBeNull();
	});
	it('matches a face of a double-faced card', () => {
		expect(pickExactMatch(hits, 'insectile aberration')?.oracle_id).toBe('3');
	});
});
