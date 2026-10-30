import { describe, expect, it } from 'vitest';
import { describeCommitFailure } from './errors';

describe('describeCommitFailure', () => {
	it('flags 409/400/404 as stale', () => {
		for (const s of [409, 400, 404]) expect(describeCommitFailure(s, 'x').stale).toBe(true);
	});
	it('does not flag others', () => {
		for (const s of [500, 502, undefined]) expect(describeCommitFailure(s, 'x').stale).toBe(false);
	});
	it('uses the reason, trimmed, or a default', () => {
		expect(describeCommitFailure(409, '  gone ').message).toBe('gone');
		expect(describeCommitFailure(500, undefined).message).toBe('The server rejected the commit.');
		expect(describeCommitFailure(500, '   ').message).toBe('The server rejected the commit.');
	});
});
