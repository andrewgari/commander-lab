<script lang="ts">
	// Composite: the bar (board select + QuickAdd + promoted chip + "Card search" button) and the panel
	// (docs/ui/card-search.md §2.1). The host owns the list; this component only calls `onadd`.
	import { untrack } from 'svelte';
	import Icon from '#lib/components/Icon.svelte';
	import CardSearchPanel from './CardSearchPanel.svelte';
	import QuickAdd from './QuickAdd.svelte';
	import { autocompleteTerm } from './quickAdd.ts';
	import { clearFilters, resolveConfig } from './presets.ts';
	import type { AddRequest, Board, CardHit, Color, Filters, OwnedFilter, PresetName } from './types.ts';

	interface Props {
		onadd: (req: AddRequest) => Promise<void>;
		boards?: Board[];
		identity?: Color[];
		inList?: Map<string, number>;
		ownedDefault?: OwnedFilter;
		hotkey?: boolean;
		placeholder?: string;
		preset?: PresetName;
		pairWith?: CardHit | null;
		/** Injected in the dev harness; defaults to the global fetch. */
		fetcher?: typeof fetch;
	}
	const uid = $props.id();
	let { onadd, boards, identity, inList, ownedDefault, hotkey = true, placeholder, preset, pairWith, fetcher = fetch }: Props = $props();

	const config = $derived(resolveConfig({ preset, boards, placeholder, identity, ownedDefault, pairWith }));
	let filters = $state<Filters>(untrack(() => structuredClone(config.filters)));
	let board = $state<Board>(untrack(() => config.boards[0]));
	let panelOpen = $state(false);
	let status = $state('');
	let statusErr = $state(false);
	let quick: QuickAdd;
	let searchBtn: HTMLButtonElement;

	// A host that changes pairWith/identity/preset re-seeds the filters it owns.
	let seed = untrack(() => JSON.stringify(config.filters));
	$effect(() => {
		const next = JSON.stringify(config.filters);
		if (next !== seed) {
			seed = next;
			filters = structuredClone(config.filters);
		}
	});
	$effect(() => {
		if (!config.boards.includes(board)) board = config.boards[0];
	});

	const placeholderText = $derived(
		placeholder ?? (preset ? config.placeholder : `Add cards to ${board}…`)
	);

	async function add(card: CardHit, qty: number): Promise<boolean> {
		try {
			await onadd({ card, qty, board });
			statusErr = false;
			status = preset === 'commander' ? `Commander: ${card.name}` : `Added ${qty}× ${card.name} to ${board}`;
			return true;
		} catch (e) {
			statusErr = true;
			status = e instanceof Error ? e.message : 'Could not add that card';
			return false;
		}
	}

	function closePanel() {
		panelOpen = false;
		searchBtn?.focus();
	}

	function openPanel() {
		const typed = quickTerm();
		if (typed) filters.name = typed; // §4: a non-empty quick-add term seeds the filter on every opening
		panelOpen = true;
	}
	function quickTerm() {
		return autocompleteTerm((document.getElementById(`${uid}-quick`) as HTMLInputElement | null)?.value ?? '');
	}

	function onWindowKeydown(e: KeyboardEvent) {
		if (hotkey && e.ctrlKey && !e.altKey && !e.metaKey && e.key === "'") {
			e.preventDefault();
			quick?.focus();
		}
	}
</script>

<svelte:window onkeydown={onWindowKeydown} />

<div class="cs-bar" role="search" aria-label="Add cards">
	{#if config.boards.length > 1}
		<label class="sr-only" for="{uid}-board">Add to board</label>
		<select id="{uid}-board" class="cs-board" bind:value={board}>{#each config.boards as b (b)}<option value={b}>{b}</option>{/each}</select>
	{/if}
	<QuickAdd
		bind:this={quick}
		placeholder={placeholderText}
		fixedQty={config.fixedQty}
		commander={filters.commander}
		pair={filters.pair}
		onpick={add}
		id="{uid}-quick"
		{fetcher}
	/>
	{#if config.promoteFilter === 'commander'}
		<button
			type="button"
			class="chip"
			aria-pressed={filters.commander}
			disabled={config.commanderLocked}
			title={config.commanderLocked ? 'Partner search shows legal partners only' : undefined}
			onclick={() => (filters.commander = !filters.commander)}
		>Can be commander</button>
	{/if}
	<button type="button" class="btn btn--secondary" bind:this={searchBtn} aria-expanded={panelOpen} aria-haspopup="dialog" onclick={openPanel}>
		<Icon name="dot-open" /> Card search
	</button>
</div>
<p class="cs-status" class:err={statusErr} role="status" aria-live="polite">{status}</p>

<CardSearchPanel
	idPrefix={uid}
	open={panelOpen}
	bind:filters
	bind:board
	commanderLocked={config.commanderLocked}
	clearTo={() => clearFilters(config)}
	boards={config.boards}
	{identity}
	{inList}
	{preset}
	fixedQty={config.fixedQty}
	{fetcher}
	{status}
	{statusErr}
	onclose={closePanel}
	onadd={(c, q) => void add(c, q)}
/>

<style>
	.cs-bar { display: flex; flex-wrap: wrap; gap: var(--space-2); align-items: stretch; }
	.cs-board, .chip {
		min-height: var(--touch-min); padding: 0 var(--space-3); font: inherit; cursor: pointer;
		background: var(--color-surface-raised); color: var(--color-text); border: 1px solid var(--color-border-control); border-radius: var(--radius-sm);
	}
	.chip[aria-pressed='true'] { background: var(--color-accent); color: var(--color-on-accent); border-color: var(--color-accent); font-weight: var(--weight-bold); }
	.chip[aria-pressed='true']::before { content: '✓ '; }
	.chip:disabled { cursor: not-allowed; border-style: dashed; }
	.chip:focus-visible, .cs-board:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 2px; }
	.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
	.cs-status { margin: var(--space-2) 0 0; min-height: 1.5em; color: var(--color-success); font-size: var(--text-sm); }
	.cs-status.err { color: var(--color-danger); }
</style>
