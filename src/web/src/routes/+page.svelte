<script lang="ts">
	import { invalidateAll, afterNavigate } from '$app/navigation';
	import { navigating } from '$app/state';
	import ErrorBanner from '#lib/components/ErrorBanner.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import StatTile from '#lib/components/home/StatTile.svelte';
	import DeckRow from '#lib/components/home/DeckRow.svelte';
	import { computeTiles, failedSources, recentDecks, SOURCE_LABELS } from '#lib/home/stats.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const loading = $derived(navigating.to?.url.pathname === '/');
	const failed = $derived(failedSources(data.sources));
	const allFailed = $derived(failed.length === Object.keys(data.sources).length);
	const tiles = $derived(computeTiles(data.sources, data.nowMs));
	const recent = $derived(data.sources.decks.ok ? recentDecks(data.sources.decks.value, data.sources.games.ok ? data.sources.games.value : [], data.sources.committed.ok ? data.sources.committed.value : [], data.nowMs) : null);

	// The entrance stagger runs on client navigation only, never on the SSR first paint.
	let enter = $state(false);
	afterNavigate(() => {
		enter = true;
	});

	const retry = () => invalidateAll();
	const bannerMessage = $derived(
		failed.length === 1 && failed[0].key === 'decks' ? "Decks couldn't load" : 'Some figures couldn\u2019t load'
	);
	const bannerDetail = $derived(failed.map((f) => `${SOURCE_LABELS[f.key]}: ${f.reason}`).join(' · '));
</script>

<svelte:head>
	<title>Commander Lab</title>
</svelte:head>

<h1 class="visually-hidden">Commander Lab</h1>

{#if loading}
	<p class="visually-hidden" role="status">Loading dashboard…</p>
	<ul class="tiles" aria-hidden="true">
		{#each Array(6) as _, i (i)}
			<li class="ph-tile"><span class="ph" style="width:45%;height:.75rem"></span><span class="ph" style="width:55%;height:2.5rem"></span><span class="ph" style="width:80%;height:.75rem"></span></li>
		{/each}
	</ul>
{:else if allFailed}
	<ErrorBanner message="Commander Lab's API is not responding" detail={bannerDetail} onRetry={retry} />
{:else}
	<section aria-labelledby="stats-h">
		<div class="eyebrow-row">
			<h2 id="stats-h" class="eyebrow">At a glance</h2>
			<span class="eyebrow">as of {data.asOf}</span>
		</div>
		<ul class="tiles" class:tiles--enter={enter} style:--tile-anim={enter ? 'tile-in var(--dur-base) var(--ease-out) backwards' : 'none'}>
			{#each tiles as tile, i (tile.key)}
				<StatTile {tile} index={i} />
			{/each}
		</ul>
		{#if failed.length > 0}
			<ErrorBanner class="banner-gap" message={bannerMessage} detail={bannerDetail} onRetry={retry} />
		{/if}
	</section>

	<section aria-labelledby="recent-h" class="recent">
		{#if recent === null}
			<div class="section-head"><h2 id="recent-h">Your decks</h2></div>
			<ErrorBanner message="Decks couldn't load" detail={data.sources.decks.ok ? '' : data.sources.decks.reason} onRetry={retry} />
		{:else}
			<div class="section-head">
				<h2 id="recent-h">{recent.heading}</h2>
				<a class="text-link" href="/decks">All decks →</a>
			</div>
			{#if recent.rows.length === 0}
				<EmptyState title="Import a deck to get started" description="Your most recent decks will show up here." ctaLabel="Import a deck" ctaHref="/decks" headingLevel={3} />
			{:else}
				<ul class="deck-list">
					{#each recent.rows as row (row.deck.id)}
						<li><DeckRow deck={row.deck} reason={row.reason} /></li>
					{/each}
				</ul>
			{/if}
		{/if}
	</section>

	<nav class="goto" aria-label="Go to">
		<span class="eyebrow">Go to</span>
		{#each [['/decks', 'Decks'], ['/inventory', 'Inventory'], ['/games', 'Games'], ['/drafts', 'Drafts'], ['/profile', 'Profile']] as [href, label] (href)}
			<a class="text-link" {href}>{label}</a>
		{/each}
	</nav>
{/if}

<style>
	.eyebrow { font-family: var(--font-mono); font-size: var(--text-xs); text-transform: uppercase; letter-spacing: var(--tracking-label); color: var(--color-muted); margin: 0; font-weight: var(--weight-regular); }
	.eyebrow-row { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: var(--space-3); }
	.tiles { display: grid; gap: var(--space-3); grid-template-columns: repeat(2, minmax(0, 1fr)); list-style: none; margin: 0; padding: 0; }
	@media (min-width: 48rem) { .tiles { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--space-4); } }
	@media (min-width: 80rem) { .tiles { grid-template-columns: repeat(6, minmax(0, 1fr)); } }
	.tiles :global(li) { animation: var(--tile-anim, none); }
	@keyframes tile-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
	:global(.banner-gap) { margin-top: var(--space-4); }
	.recent { margin-top: var(--space-8); }
	.section-head { display: flex; justify-content: space-between; align-items: baseline; border-bottom: 1px solid var(--color-border); margin-bottom: 0; }
	.section-head h2 { margin-bottom: var(--space-2); }
	.deck-list { list-style: none; margin: 0; padding: 0; }
	.goto { display: flex; flex-wrap: wrap; align-items: center; gap: 0 var(--space-1); margin-top: var(--space-4); }
	.goto .eyebrow { margin-right: var(--space-2); }
	.ph-tile { display: grid; align-content: space-between; gap: var(--space-2); min-height: 9rem; padding: var(--space-4); background: var(--color-surface); border: 1px solid var(--color-border); border-top: 3px solid var(--color-border-control); list-style: none; }
	.ph { display: block; background: var(--color-border); border-radius: var(--radius-sm); }
	@media (prefers-reduced-motion: no-preference) { .ph { animation: pulse var(--dur-pulse) ease-in-out infinite alternate; } }
	@keyframes pulse { to { opacity: 0.45; } }
</style>
