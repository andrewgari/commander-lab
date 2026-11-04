<script lang="ts">
	// Drawer (>=1024px) / full-screen sheet (390px): FilterBar + ResultGrid + paging (docs/ui/card-search.md §4.1).
	import { tick, untrack } from 'svelte';
	import Icon from '#lib/components/Icon.svelte';
	import ErrorBanner from '#lib/components/ErrorBanner.svelte';
	import FilterBar from './FilterBar.svelte';
	import ResultGrid from './ResultGrid.svelte';
	import { PANEL, activeFilterCount, isEmpty, searchUrl } from './query.ts';
	import type { Board, CardHit, Color, Filters, SearchResponse } from './types.ts';

	interface Props {
		open: boolean;
		filters: Filters;
		commanderLocked?: boolean;
		clearTo: () => Filters;
		boards: Board[];
		board: Board;
		identity?: Color[];
		inList?: Map<string, number>;
		preset?: 'commander';
		fixedQty?: 1;
		fetcher?: typeof fetch;
		idPrefix?: string;
		onclose: () => void;
		onadd: (card: CardHit, qty: number) => void;
	}
	let {
		open, filters = $bindable(), commanderLocked = false, clearTo, boards, board = $bindable(), identity, inList, preset, fixedQty,
		fetcher = fetch, idPrefix = 'cs', onclose, onadd
	}: Props = $props();

	let results = $state<CardHit[]>([]);
	let total = $state<number | null>(null);
	let hasMore = $state(false);
	let page = $state(1);
	let loading = $state(false);
	let error = $state('');
	let degraded = $state(false);
	let scryQuery = $state('');
	let heading = $state<HTMLHeadingElement>();
	let seq = 0;

	const empty = $derived(isEmpty(filters));
	const count = $derived(activeFilterCount(filters));

	async function load(p: number) {
		const mine = ++seq;
		loading = true;
		error = '';
		try {
			const res = await fetcher(searchUrl(filters, { page: p, page_size: PANEL.pageSize }));
			if (!res.ok) throw new Error(`Search failed (${res.status})`);
			const body = (await res.json()) as SearchResponse;
			if (mine !== seq) return;
			results = p === 1 ? body.results : [...results, ...body.results];
			page = body.page;
			hasMore = body.has_more;
			total = body.total;
			degraded = body.degraded;
			scryQuery = body.query;
		} catch (e) {
			if (mine !== seq) return;
			error = e instanceof Error ? e.message : 'Search failed';
		} finally {
			if (mine === seq) loading = false;
		}
	}

	// Live search: 300 ms after any filter change; nothing is requested while every filter is empty.
	$effect(() => {
		if (!open) return;
		const key = JSON.stringify(filters); // tracked: any filter change reruns
		void key;
		if (untrack(() => empty)) {
			seq++;
			results = [];
			loading = false;
			error = '';
			return;
		}
		const t = setTimeout(() => void load(1), PANEL.debounceMs);
		return () => clearTimeout(t);
	});

	$effect(() => {
		if (open) void tick().then(() => heading?.focus());
	});

	function onKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			e.stopPropagation();
			onclose();
		}
	}
</script>

{#if open}
	<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
	<div class="panel" role="dialog" tabindex="-1" aria-modal="false" aria-labelledby="{idPrefix}-title" onkeydown={onKeydown}>
		<header class="p-head">
			<h2 id="{idPrefix}-title" tabindex="-1" bind:this={heading}>Card search</h2>
			<span class="meta" aria-live="polite">{#if total !== null && !empty}{total} cards{/if}</span>
			<button type="button" class="p-close" onclick={onclose} aria-label="Close card search"><Icon name="close" size="1.25rem" /></button>
		</header>

		<div class="p-body">
			<section aria-label="Filters" class="p-filters">
				<div class="p-filter-head">
					<span class="meta">{count} {count === 1 ? 'filter' : 'filters'}</span>
					<button type="button" class="btn btn--secondary" onclick={() => (filters = clearTo())}>Clear filters</button>
				</div>
				<FilterBar bind:filters {commanderLocked} idPrefix="{idPrefix}-f" />
				{#if boards.length > 1}
					<div class="board-pick">
						<label for="{idPrefix}-pboard">Add to</label>
						<select id="{idPrefix}-pboard" bind:value={board}>{#each boards as b (b)}<option value={b}>{b}</option>{/each}</select>
					</div>
				{/if}
			</section>

			<section aria-label="Results" class="p-results" aria-busy={loading}>
				{#if degraded}<ErrorBanner severity="warning" message="Card search is offline" detail="Showing local matches only." />{/if}
				{#if empty}
					<p class="hint">Set a filter to search cards.</p>
				{:else if error}
					<ErrorBanner message="Search failed" detail={error} />
					<button type="button" class="btn btn--secondary" onclick={() => void load(1)}>Retry</button>
				{:else if loading && !results.length}
					<ul class="skeleton" aria-label="Loading results">{#each Array(8) as _, i (i)}<li></li>{/each}</ul>
				{:else if !results.length}
					<p class="hint">No cards match. Loosen a filter.</p>
				{:else}
					<ResultGrid {results} {inList} {identity} {preset} {fixedQty} {board} {onadd} />
					{#if hasMore}
						<button type="button" class="btn btn--secondary more" disabled={loading} onclick={() => void load(page + 1)}>{loading ? 'Loading…' : 'Load more'}</button>
					{/if}
				{/if}
			</section>
		</div>

		<footer class="p-foot">
			{#if scryQuery && !empty}
				<a class="text-link" href="https://scryfall.com/search?q={encodeURIComponent(scryQuery)}" target="_blank" rel="noopener">Open on Scryfall ↗</a>
				<code class="q">{scryQuery}</code>
			{/if}
		</footer>
	</div>
{/if}

<style>
	.panel {
		position: fixed; z-index: 40; inset: 0; display: flex; flex-direction: column;
		background: var(--color-bg); color: var(--color-text-body); border-left: 1px solid var(--color-border);
	}
	@media (min-width: 1024px) {
		.panel { inset: 0 0 0 auto; width: 32rem; max-width: 100vw; box-shadow: -8px 0 24px rgb(0 0 0 / 0.4); }
	}
	.p-head { display: flex; align-items: center; gap: var(--space-3); padding: var(--space-3) var(--space-4); border-bottom: 1px solid var(--color-border); background: var(--color-surface); }
	.p-head h2 { margin: 0; font-size: var(--text-lg); color: var(--color-text); flex: 1; }
	.p-head h2:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 2px; }
	.p-close { min-width: var(--touch-min); min-height: var(--touch-min); display: inline-flex; align-items: center; justify-content: center; background: none; color: var(--color-text); border: 1px solid var(--color-border-control); border-radius: var(--radius-sm); cursor: pointer; }
	.p-close:focus-visible, select:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 2px; }
	.p-body { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: var(--space-6); padding: var(--space-4); }
	.p-filter-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-3); }
	.board-pick { display: flex; flex-direction: column; gap: var(--space-2); margin-top: var(--space-4); }
	.board-pick label { font-size: var(--text-xs); letter-spacing: var(--tracking-label); text-transform: uppercase; color: var(--color-muted); }
	select { min-height: var(--touch-min); padding: 0 var(--space-3); font: inherit; background: var(--color-surface); color: var(--color-text); border: 1px solid var(--color-border-control); border-radius: var(--radius-sm); }
	.p-results { display: flex; flex-direction: column; gap: var(--space-4); }
	.hint { color: var(--color-muted); margin: 0; }
	.more { align-self: center; justify-content: center; }
	.skeleton { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(9.5rem, 1fr)); gap: var(--space-4); }
	.skeleton li { aspect-ratio: 146 / 204; background: var(--color-surface-raised); border-radius: var(--radius-md); animation: pulse var(--dur-pulse) ease-in-out infinite; }
	@keyframes pulse { 50% { opacity: 0.5; } }
	@media (prefers-reduced-motion: reduce) { .skeleton li { animation: none; } }
	.p-foot { display: flex; align-items: center; gap: var(--space-3); padding: var(--space-2) var(--space-4); border-top: 1px solid var(--color-border); background: var(--color-surface); min-height: 3rem; }
	.q { font-family: var(--font-mono); font-size: var(--text-xs); color: var(--color-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
</style>
