<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import type { PageProps } from './$types';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import ErrorBanner from '#lib/components/ErrorBanner.svelte';
	import ScrollTable, { type Column } from '#lib/components/ScrollTable.svelte';

	let { data }: PageProps = $props();

	const columns: Column[] = [
		{ key: 'name', header: 'Card' },
		{ key: 'owned', header: 'Owned', numeric: true },
		{ key: 'in_collection', header: 'In collection', numeric: true },
		{ key: 'in_deck', header: 'In decks', numeric: true },
		{ key: 'in_mail', header: 'In mail', numeric: true },
		{ key: 'decks', header: 'Decks' }
	];
</script>

<svelte:head>
	<title>Inventory — Commander Lab</title>
</svelte:head>

<nav><a href="/">← home</a></nav>

<h1>Inventory</h1>

{#if data.error}
	<ErrorBanner message="Inventory unavailable" detail={data.error} onRetry={() => invalidateAll()} retryLabel="Reload inventory" />
{:else}
	<p class="totals">
		<strong>{data.totals.owned}</strong> copies across
		<strong>{data.totals.cards}</strong> cards ·
		{data.totals.in_collection} in collection ·
		{data.totals.in_deck} in decks ·
		{data.totals.in_mail} in mail
	</p>

	<form method="get" class="search">
		<input
			type="search"
			name="q"
			value={data.q}
			placeholder="Filter by card name…"
			aria-label="Filter inventory by card name"
		/>
		<button type="submit">Search</button>
		{#if data.q}<a href="/inventory" aria-label="Clear inventory filter">clear</a>{/if}
	</form>

	<ScrollTable
		caption="Inventory by card"
		captionHidden
		{columns}
		rows={data.inventory}
		rowKey={(row) => row.oracle_id}
		rowHeader="name"
		stickyFirstCol
		density="compact"
	>
		{#snippet cell(row, column)}
			{#if column.key === 'name'}
				{row.name}
				{#if row.not_owned > 0}
					<span class="not-owned" title="placeholder copies, not counted as owned">+{row.not_owned} not owned</span>
				{/if}
			{:else if column.key === 'decks'}
				<span class="decks">{row.decks.join(', ')}</span>
			{:else if column.key === 'owned'}
				<strong>{row.owned}</strong>
			{:else}
				{row[column.key as 'in_collection' | 'in_deck' | 'in_mail']}
			{/if}
		{/snippet}
		{#snippet empty()}
			{#if data.q}
				<EmptyState variant="inline" filtered title="No cards match “{data.q}”" ctaLabel="Clear search" ctaHref="/inventory" />
			{:else}
				<EmptyState variant="inline" title="Your collection is empty" description="Cards appear here after you sync or import decks." />
			{/if}
		{/snippet}
	</ScrollTable>
{/if}

<style>
	h1 {
		font-size: 1.5rem;
		margin-bottom: 0.25rem;
	}
	.totals {
		color: var(--color-text-body);
	}
	.search {
		display: flex;
		gap: 0.5rem;
		align-items: center;
		margin: 0.75rem 0;
	}
	.search input {
		flex: 1;
		max-width: 24rem;
		min-height: 44px;
		padding: 0.35rem 0.5rem;
		box-sizing: border-box;
	}
	.search button {
		min-height: 44px;
		min-width: 44px;
		padding: 0.35rem 0.9rem;
	}
	.search a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
		min-width: 44px;
		padding: 0 0.5rem;
	}
	nav a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
	}
	.decks {
		font-size: 0.8rem;
		color: var(--color-muted);
	}
	.not-owned {
		font-size: 0.75rem;
		color: var(--color-muted);
	}
</style>
