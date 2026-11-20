import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ICON_NAMES } from '#lib/icons.ts';
import { STATUS_CATALOG, draftStatus, fallback, gameResult, type StatusKey } from './status';

const ALL_KEYS: StatusKey[] = [
	'draft-open', 'draft-committed', 'draft-abandoned',
	'game-win', 'game-loss', 'game-draw', 'game-unknown',
	'unavailable', 'short', 'owned', 'in-mail',
	'success', 'warning', 'danger', 'info', 'neutral'
];
const GROUPS: Record<string, StatusKey[]> = {
	draft: ['draft-open', 'draft-committed', 'draft-abandoned'],
	game: ['game-win', 'game-loss', 'game-draw', 'game-unknown'],
	deck: ['short', 'owned', 'in-mail']
};

describe('STATUS_CATALOG', () => {
	it('has exactly the 16 keys, each fully specified', () => {
		expect(Object.keys(STATUS_CATALOG).sort()).toEqual([...ALL_KEYS].sort());
		for (const key of ALL_KEYS) {
			const e = STATUS_CATALOG[key];
			expect(e.label.length, key).toBeGreaterThan(0);
			expect(ICON_NAMES, key).toContain(e.icon);
			expect(['success', 'warning', 'danger', 'info', 'neutral'], key).toContain(e.role);
			expect(['solid', 'text'], key).toContain(e.variant);
		}
	});

	it.each(Object.entries(GROUPS))('%s group has pairwise distinct labels', (_name, keys) => {
		const labels = keys.map((k) => STATUS_CATALOG[k].label);
		expect(new Set(labels).size).toBe(labels.length);
	});

	// Non-colour distinctness: statuses that sit side by side must differ by icon silhouette.
	// check is shared by `owned` only across groups, never inside one.
	it.each(Object.entries(GROUPS))('%s group has pairwise distinct icons', (_name, keys) => {
		const icons = keys.map((k) => STATUS_CATALOG[k].icon);
		expect(new Set(icons).size).toBe(icons.length);
	});

	it('matches the catalog table', () => {
		expect(STATUS_CATALOG['draft-open']).toEqual({ label: 'Open', icon: 'dot-open', role: 'warning', variant: 'text' });
		expect(STATUS_CATALOG['game-unknown'].label).toBe('Result unavailable');
		expect(STATUS_CATALOG['in-mail']).toEqual({ label: 'In mail', icon: 'dot-open', role: 'warning', variant: 'text' });
		expect(STATUS_CATALOG.warning.variant).toBe('solid');
		expect(STATUS_CATALOG.info.variant).toBe('text');
		expect(['OK', 'Warning', 'Error', 'Info', '—']).toEqual(
			(['success', 'warning', 'danger', 'info', 'neutral'] as StatusKey[]).map((k) => STATUS_CATALOG[k].label)
		);
	});
});

describe('mapping helpers', () => {
	let warn: ReturnType<typeof vi.spyOn>;
	beforeEach(() => {
		warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
	});

	it('draftStatus maps the known values', () => {
		expect(draftStatus('open')).toBe('draft-open');
		expect(draftStatus('committed')).toBe('draft-committed');
		expect(draftStatus('abandoned')).toBe('draft-abandoned');
		expect(warn).not.toHaveBeenCalled();
	});

	it('gameResult maps null, win, loss and draw', () => {
		expect(gameResult(null)).toBe('game-unknown');
		expect(gameResult('win')).toBe('game-win');
		expect(gameResult('loss')).toBe('game-loss');
		expect(gameResult('draw')).toBe('game-draw');
		expect(warn).not.toHaveBeenCalled();
	});

	it('unknown strings fall back to neutral, including the empty string', () => {
		expect(draftStatus('archived-x1')).toBe('neutral');
		expect(gameResult('concede-x1')).toBe('neutral');
		expect(fallback('whatever-x1')).toBe('neutral');
		expect(draftStatus('')).toBe('neutral');
	});

	it('warns once per raw value in dev', () => {
		const raw = `unique-${Math.random()}`;
		expect(fallback(raw)).toBe('neutral');
		expect(fallback(raw)).toBe('neutral');
		expect(draftStatus(raw)).toBe('neutral');
		expect(warn).toHaveBeenCalledTimes(1);
		expect(String(warn.mock.calls[0][0])).toContain(raw);
		fallback(`${raw}-other`);
		expect(warn).toHaveBeenCalledTimes(2);
	});
});
