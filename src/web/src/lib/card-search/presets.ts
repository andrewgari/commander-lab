// Pure: named presets -> default props + filters (docs/ui/card-search.md §2.1, §3.1).
// A preset only sets DEFAULTS; explicit props always win.

import { emptyFilters, normalizeFilters } from './query.ts';
import type { Board, Filters, PresetName } from './types.ts';

export interface Preset {
	/** Starting filters, e.g. `{ commander: true, ci_mode: 'exact' }`. */
	filters: Partial<Filters>;
	boards: Board[];
	placeholder: string;
	/** Hides the qty stepper and ignores "2x" in quick-add. */
	fixedQty?: 1;
	/** Render this filter as a chip in the bar itself, not only in the panel. */
	promoteFilter?: 'commander';
}

export const PRESETS: Record<PresetName, Preset> = {
	commander: {
		filters: { commander: true, ci_mode: 'exact' },
		boards: ['commander'],
		placeholder: 'Search commanders…',
		fixedQty: 1,
		promoteFilter: 'commander'
	}
};

/** The `<CardSearch>` props that a preset can supply or that override it. */
export interface PresetProps {
	preset?: PresetName;
	boards?: Board[];
	placeholder?: string;
	fixedQty?: 1;
	/** Explicit starting filters; each key overrides the preset's. */
	filters?: Partial<Filters>;
	/** `ownedDefault` prop. */
	ownedDefault?: Filters['owned'];
	/** `identity` prop: sets the color filter to "within identity". */
	identity?: Filters['ci'];
	/** `pairWith` prop (its oracle_id). Honoured only under the `commander` preset (§3.1.3). */
	pairWith?: { oracle_id: string } | null;
}

export interface ResolvedConfig {
	/** Starting filters; also what "Clear filters" returns to (see `clearFilters`). */
	filters: Filters;
	boards: Board[];
	placeholder: string;
	fixedQty?: 1;
	promoteFilter?: 'commander';
	/** The Can be commander chip is shown pressed and disabled because `pair` needs `cmdr=1`. */
	commanderLocked: boolean;
}

/** Defaults when a host passes neither `boards` nor a preset. */
const DEFAULT_BOARDS: Board[] = ['main'];

/** Resolve props + preset into the config the component starts from. Explicit props beat the preset. */
export function resolveConfig(props: PresetProps = {}): ResolvedConfig {
	const preset = props.preset ? PRESETS[props.preset] : undefined;
	const boards = props.boards?.length ? props.boards : (preset?.boards ?? DEFAULT_BOARDS);

	const filters: Partial<Filters> = { ...preset?.filters, ...props.filters };
	if (props.ownedDefault) filters.owned = props.ownedDefault;
	if (props.identity) {
		filters.ci = props.identity;
		filters.ci_mode = 'within';
	}
	const pair = props.preset === 'commander' ? (props.pairWith?.oracle_id ?? null) : null;
	if (pair) {
		filters.commander = true;
		filters.pair = pair;
	}

	return {
		filters: normalizeFilters(filters),
		boards,
		placeholder: props.placeholder ?? preset?.placeholder ?? `Add cards to ${boards[0]}…`,
		fixedQty: props.fixedQty ?? preset?.fixedQty,
		promoteFilter: preset?.promoteFilter,
		commanderLocked: pair !== null
	};
}

/**
 * "Clear filters". Under a preset it resets to the preset defaults, so Can be commander stays on;
 * turning it off is always one explicit click (§3). With no preset it clears everything, and a
 * `pair` clause set by `pairWith` is kept because the host, not the user, owns it.
 */
export function clearFilters(config: ResolvedConfig): Filters {
	return config.promoteFilter || config.commanderLocked
		? normalizeFilters(config.filters)
		: emptyFilters();
}
