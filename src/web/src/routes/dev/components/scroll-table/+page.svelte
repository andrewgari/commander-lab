<script lang="ts">
	// Fixture for ScrollTable, exercised by src/web/tests/components_a11y.py.
	import ScrollTable from '#lib/components/ScrollTable.svelte';
	import type { Column } from '#lib/components/ScrollTable.svelte';

	type Have = { card: string; set: string; have: number; need: number; buy: number | null; price: number; status: string };
	const haveColumns: Column[] = [
		{ key: 'card', header: 'Card' },
		{ key: 'set', header: 'Set' },
		{ key: 'have', header: 'Have', numeric: true },
		{ key: 'need', header: 'Need', numeric: true },
		{ key: 'buy', header: 'Buy', numeric: true },
		{ key: 'price', header: 'Price (USD)', numeric: true },
		{ key: 'status', header: 'Status' }
	];
	const haveRows: Have[] = [
		{ card: 'Sol Ring', set: 'C21', have: 0, need: 2, buy: 2, price: 1.5, status: 'Missing' },
		{ card: 'Command Tower', set: 'CMR', have: 3, need: 3, buy: 0, price: 0.25, status: 'Complete' },
		{ card: 'Arcane Signet', set: 'ELD', have: 1, need: 2, buy: null, price: 1.1, status: 'Partial' },
		{ card: 'Cultivate', set: 'M21', have: 2, need: 2, buy: 0, price: 0.4, status: 'Complete' }
	];

	type Small = { name: string; a: number; b: number; c: number; d: number; e: number };
	const smallColumns: Column[] = [
		{ key: 'name', header: 'Deck' },
		{ key: 'a', header: 'Lands', numeric: true },
		{ key: 'b', header: 'Ramp', numeric: true },
		{ key: 'c', header: 'Draw', numeric: true },
		{ key: 'd', header: 'Removal', numeric: true },
		{ key: 'e', header: 'Wipes', numeric: true }
	];
	const smallRows: Small[] = [
		{ name: 'Atraxa', a: 36, b: 10, c: 8, d: 6, e: 2 },
		{ name: 'Korvold', a: 35, b: 12, c: 9, d: 5, e: 0 }
	];

	const hiddenColumns: Column[] = [
		{ key: 'tag', header: 'Tag' },
		{ key: 'count', header: 'Cards', numeric: true }
	];
	const hiddenRows = [{ tag: 'Ramp', count: 12 }, { tag: 'Removal', count: 7 }];
</script>

<svelte:head><title>ScrollTable — Component fixtures — Commander Lab</title></svelte:head>

<div class="fixture">
	<h1>ScrollTable</h1>

	<section aria-labelledby="h-have">
		<h2 id="h-have">Seven columns, sticky first column</h2>
		<ScrollTable caption="Cards to buy" columns={haveColumns} rows={haveRows} rowHeader="card" stickyFirstCol minWidth="52rem">
			{#snippet cell(row, column)}
				{#if column.key === 'status'}
					<span class="pill">{row.status}</span>
				{:else if column.key === 'price'}
					${row.price.toFixed(2)}
				{:else if row[column.key as keyof Have] === null}
					—<span class="visually-hidden">no value</span>
				{:else}
					{row[column.key as keyof Have]}
				{/if}
			{/snippet}
		</ScrollTable>
	</section>

	<section aria-labelledby="h-compact">
		<h2 id="h-compact">Compact, six columns</h2>
		<ScrollTable caption="Deck composition" columns={smallColumns} rows={smallRows} rowHeader="name" density="compact" />
	</section>

	<section aria-labelledby="h-empty">
		<h2 id="h-empty">Empty, default content</h2>
		<ScrollTable caption="Wishlist" columns={haveColumns} rows={[]} emptyTitle="Your wishlist is empty" emptyDescription="Add cards from a deck to track them here." />
	</section>

	<section aria-labelledby="h-empty-cta">
		<h2 id="h-empty-cta">Empty, custom content with a call to action</h2>
		<ScrollTable caption="Trades" columns={haveColumns} rows={[]}>
			{#snippet empty()}
				<p>No trades yet.</p>
				<a class="btn btn--secondary" href="/dev/components">Back to fixtures</a>
			{/snippet}
		</ScrollTable>
	</section>

	<section aria-labelledby="h-hidden">
		<h2 id="h-hidden">Hidden caption</h2>
		<ScrollTable caption="Tags in this deck" captionHidden columns={hiddenColumns} rows={hiddenRows} rowHeader="tag" minWidth="20rem" />
	</section>
</div>

<style>
	.fixture { padding: var(--space-4); max-width: var(--content-max); margin: 0 auto; }
	section { margin-bottom: var(--space-6); }
	.pill {
		display: inline-block; padding: 0 var(--space-2); border: 1px solid var(--color-border-control);
		border-radius: var(--radius-sm); font-size: var(--text-sm); color: var(--color-text);
	}
</style>
