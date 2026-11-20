<script lang="ts">
	// The eight filters (docs/ui/card-search.md §3), incl. Can be commander. All toggles are real
	// <button aria-pressed>, so state never relies on colour alone.
	import { CARD_TYPES, CI_MODES, COLORS, MANA_VALUES, OWNED_VALUES, RARITIES, toggleColor } from './query.ts';
	import type { Filters } from './types.ts';

	interface Props {
		filters: Filters;
		/** Can be commander is pressed and disabled because a partner search needs `cmdr=1` (§3.1.3). */
		commanderLocked?: boolean;
		idPrefix?: string;
	}
	let { filters = $bindable(), commanderLocked = false, idPrefix = 'cs' }: Props = $props();

	function toggle<T extends string>(list: T[], v: T): T[] {
		return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
	}
	const label = (s: string) => s.replace('_', ' ').replace(/^./, (c) => c.toUpperCase());
</script>

<div class="filter-bar">
	<div class="f-row">
		<button
			type="button"
			class="chip chip-cmdr"
			aria-pressed={filters.commander}
			disabled={commanderLocked}
			onclick={() => (filters.commander = !filters.commander)}
		>Can be commander</button>
		{#if commanderLocked}<span class="meta">Partner search shows legal partners only</span>{/if}
	</div>

	<div class="f-field">
		<label for="{idPrefix}-name">Name</label>
		<input id="{idPrefix}-name" type="text" bind:value={filters.name} autocomplete="off" />
	</div>

	<fieldset class="f-field">
		<legend>Color identity</legend>
		<div class="f-chips">
			{#each COLORS as c (c)}
				<button
					type="button"
					class="chip chip-mana chip-{c.toLowerCase()}"
					aria-pressed={filters.ci.includes(c)}
					aria-label={{ W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green', C: 'Colorless' }[c]}
					onclick={() => (filters.ci = toggleColor(filters.ci, c))}
				>{c}</button>
			{/each}
		</div>
		<div class="seg" role="group" aria-label="Color match mode">
			{#each CI_MODES as m (m)}
				<button type="button" aria-pressed={filters.ci_mode === m} onclick={() => (filters.ci_mode = m)}>{label(m)}</button>
			{/each}
		</div>
	</fieldset>

	<fieldset class="f-field">
		<legend>Type</legend>
		<div class="f-chips">
			{#each CARD_TYPES as t (t)}
				<button type="button" class="chip" aria-pressed={filters.type.includes(t)} onclick={() => (filters.type = toggle(filters.type, t))}>{label(t)}</button>
			{/each}
		</div>
		<label class="sr-only" for="{idPrefix}-type-text">Subtype or supertype</label>
		<input id="{idPrefix}-type-text" type="text" placeholder="Subtype or supertype, e.g. elf" bind:value={filters.type_text} autocomplete="off" />
	</fieldset>

	<fieldset class="f-field">
		<legend>Mana value</legend>
		<div class="f-chips">
			{#each MANA_VALUES as v (v)}
				<button type="button" class="chip" aria-pressed={filters.mv.includes(v)} onclick={() => (filters.mv = toggle(filters.mv, v))}>{v}</button>
			{/each}
		</div>
	</fieldset>

	<fieldset class="f-field">
		<legend>Rarity</legend>
		<div class="f-chips">
			{#each RARITIES as r (r)}
				<button type="button" class="chip" aria-pressed={filters.rarity.includes(r)} onclick={() => (filters.rarity = toggle(filters.rarity, r))}>{label(r)}</button>
			{/each}
		</div>
	</fieldset>

	<div class="f-field">
		<label for="{idPrefix}-oracle">Oracle text</label>
		<input id="{idPrefix}-oracle" type="text" placeholder={'draw a card, "enters tapped"'} bind:value={filters.oracle} autocomplete="off" />
	</div>

	<fieldset class="f-field">
		<legend>Owned</legend>
		<div class="seg" role="group" aria-label="Owned">
			{#each OWNED_VALUES as o (o)}
				<button type="button" aria-pressed={filters.owned === o} onclick={() => (filters.owned = o)}>{o === 'any' ? 'Any' : o === 'owned' ? 'Owned' : 'Not owned'}</button>
			{/each}
		</div>
	</fieldset>
</div>

<style>
	.filter-bar { display: flex; flex-direction: column; gap: var(--space-4); }
	.f-row { display: flex; align-items: center; gap: var(--space-3); flex-wrap: wrap; }
	.f-field { display: flex; flex-direction: column; gap: var(--space-2); border: 0; margin: 0; padding: 0; min-width: 0; }
	label, legend { font-size: var(--text-xs); letter-spacing: var(--tracking-label); text-transform: uppercase; color: var(--color-muted); padding: 0; }
	.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
	input[type='text'] {
		min-height: var(--touch-min); padding: var(--space-2) var(--space-3); font: inherit; width: 100%;
		background: var(--color-surface); color: var(--color-text); border: 1px solid var(--color-border-control); border-radius: var(--radius-sm);
	}
	.f-chips { display: flex; flex-wrap: wrap; gap: var(--space-2); }
	.chip, .seg button {
		min-height: var(--touch-min); min-width: var(--touch-min); padding: var(--space-1) var(--space-3); font: inherit; cursor: pointer;
		background: var(--color-surface-raised); color: var(--color-text); border: 1px solid var(--color-border-control); border-radius: var(--radius-sm);
	}
	.chip[aria-pressed='true'], .seg button[aria-pressed='true'] {
		background: var(--color-accent); color: var(--color-on-accent); border-color: var(--color-accent); font-weight: var(--weight-bold);
	}
	.chip[aria-pressed='true']::before, .seg button[aria-pressed='true']::before { content: '✓ '; }
	.chip:disabled { cursor: not-allowed; border-style: dashed; }
	.seg { display: inline-flex; flex-wrap: wrap; }
	.seg button { border-radius: 0; margin-left: -1px; }
	.seg button:first-child { border-radius: var(--radius-sm) 0 0 var(--radius-sm); margin-left: 0; }
	.seg button:last-child { border-radius: 0 var(--radius-sm) var(--radius-sm) 0; }
	:global(.chip:focus-visible), :global(.seg button:focus-visible), input[type='text']:focus-visible {
		outline: 2px solid var(--color-focus); outline-offset: 2px;
	}
</style>
