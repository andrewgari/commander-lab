// StatusPill catalog (docs/ui/shared-components README D10, D14). Plain TS so the mapping is
// unit-testable without rendering. A status always carries a text label and an icon shape;
// colour (the role) is only the third channel.
import type { IconName } from '#lib/icons.ts';

export type { IconName };

export type StatusRole = 'success' | 'warning' | 'danger' | 'info' | 'neutral';

export type StatusKey =
	| 'draft-open'
	| 'draft-committed'
	| 'draft-abandoned'
	| 'game-win'
	| 'game-loss'
	| 'game-draw'
	| 'game-unknown'
	| 'unavailable'
	| 'short'
	| 'owned'
	| 'in-mail'
	| 'success'
	| 'warning'
	| 'danger'
	| 'info'
	| 'neutral';

export interface StatusEntry {
	label: string;
	icon: IconName;
	role: StatusRole;
	variant: 'solid' | 'text';
}

// One entry per line, single quotes: tests/test_web_status_pill.py regex-parses role/variant pairs.
export const STATUS_CATALOG: Record<StatusKey, StatusEntry> = {
	'draft-open': { label: 'Open', icon: 'dot-open', role: 'warning', variant: 'text' },
	'draft-committed': { label: 'Committed', icon: 'check', role: 'success', variant: 'solid' },
	'draft-abandoned': { label: 'Abandoned', icon: 'ban', role: 'neutral', variant: 'text' },
	'game-win': { label: 'Win', icon: 'trophy', role: 'success', variant: 'solid' },
	'game-loss': { label: 'Loss', icon: 'x', role: 'danger', variant: 'solid' },
	'game-draw': { label: 'Draw', icon: 'equals', role: 'neutral', variant: 'text' },
	'game-unknown': { label: 'Result unavailable', icon: 'help', role: 'neutral', variant: 'text' },
	unavailable: { label: 'Unavailable', icon: 'dash', role: 'neutral', variant: 'text' },
	short: { label: 'Short', icon: 'alert', role: 'danger', variant: 'solid' },
	owned: { label: 'Owned', icon: 'check', role: 'success', variant: 'solid' },
	'in-mail': { label: 'In mail', icon: 'dot-open', role: 'warning', variant: 'text' },
	success: { label: 'OK', icon: 'check', role: 'success', variant: 'solid' },
	warning: { label: 'Warning', icon: 'alert', role: 'warning', variant: 'solid' },
	danger: { label: 'Error', icon: 'x', role: 'danger', variant: 'solid' },
	info: { label: 'Info', icon: 'info', role: 'info', variant: 'text' },
	neutral: { label: '—', icon: 'dash', role: 'neutral', variant: 'text' }
};

const warned = new Set<string>();

/**
 * Unknown API value → the neutral status. The caller passes `label={raw}` so the raw string is
 * what the user reads; the pill never renders unstyled. Warns once per raw value in dev.
 */
export function fallback(raw: string): StatusKey {
	if (import.meta.env.DEV && !warned.has(raw)) {
		warned.add(raw);
		console.warn(`StatusPill: unknown status value ${JSON.stringify(raw)}; rendering as neutral`);
	}
	return 'neutral';
}

export function draftStatus(raw: string): StatusKey {
	switch (raw) {
		case 'open':
			return 'draft-open';
		case 'committed':
			return 'draft-committed';
		case 'abandoned':
			return 'draft-abandoned';
		default:
			return fallback(raw);
	}
}

export function gameResult(raw: string | null): StatusKey {
	if (raw === null) return 'game-unknown';
	switch (raw) {
		case 'win':
			return 'game-win';
		case 'loss':
			return 'game-loss';
		case 'draw':
			return 'game-draw';
		default:
			return fallback(raw);
	}
}
