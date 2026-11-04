// Shared icon set (docs/ui/shared-components README D13): 16x16 viewBox, stroke-only,
// drawn with currentColor at 1.5 width by Icon.svelte. Icons are decorative; meaning always
// comes from adjacent text.
export type IconName =
	| 'check'
	| 'x'
	| 'ban'
	| 'trophy'
	| 'equals'
	| 'help'
	| 'dash'
	| 'alert'
	| 'info'
	| 'error'
	| 'dot-open'
	| 'close'
	| 'spinner';

export const ICON_PATHS: Record<IconName, string> = {
	check: 'M3 8.5l3.5 3.5L13 4.5',
	x: 'M4 4l8 8M12 4l-8 8',
	ban: 'M8 2a6 6 0 1 0 0 12A6 6 0 0 0 8 2zM3.8 3.8l8.4 8.4',
	trophy: 'M5 2.5h6v4a3 3 0 0 1-6 0v-4zM5 4H2.5v1A2.5 2.5 0 0 0 5 7.5M11 4h2.5v1A2.5 2.5 0 0 1 11 7.5M8 9.5V12M5.5 13.5h5',
	equals: 'M3.5 6h9M3.5 10h9',
	help: 'M8 2a6 6 0 1 0 0 12A6 6 0 0 0 8 2zM6.2 6.3a1.9 1.9 0 1 1 2.6 1.7c-.6.3-.8.7-.8 1.3M8 11.3h.01',
	dash: 'M4 8h8',
	alert: 'M8 2.5l6 10.5H2L8 2.5zM8 6.5v3M8 11.2h.01',
	info: 'M8 2a6 6 0 1 0 0 12A6 6 0 0 0 8 2zM8 7.2V11M8 5h.01',
	error: 'M5.2 2h5.6L14 5.2v5.6L10.8 14H5.2L2 10.8V5.2L5.2 2zM8 5v3.5M8 10.8h.01',
	'dot-open': 'M8 3.5a4.5 4.5 0 1 0 0 9 4.5 4.5 0 0 0 0-9z',
	close: 'M3.5 3.5l9 9M12.5 3.5l-9 9',
	spinner: 'M8 2a6 6 0 1 0 6 6'
};

export const ICON_NAMES = Object.keys(ICON_PATHS) as IconName[];
