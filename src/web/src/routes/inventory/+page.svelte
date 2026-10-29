<script lang="ts">
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

<svelte:head>
	<title>Inventory — Commander Lab</title>
</svelte:head>

<nav><a href="/">← home</a></nav>

<h1>Inventory</h1>

{#if data.error}
	<p class="error">Inventory unavailable: {data.error}</p>
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

	{#if data.inventory.length === 0}
		<p>No cards{data.q ? ` matching “${data.q}”` : ''} in the collection.</p>
	{:else}
		<!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard-scrollable region) -->
		<div class="table-scroll" role="region" aria-label="Inventory by card" tabindex="0">
		<table>
			<caption class="visually-hidden">Inventory by card</caption>
			<thead>
				<tr>
					<th scope="col">Card</th>
					<th scope="col">Owned</th>
					<th scope="col">In collection</th>
					<th scope="col">In decks</th>
					<th scope="col">In mail</th>
					<th scope="col">Decks</th>
				</tr>
			</thead>
			<tbody>
				{#each data.inventory as row (row.oracle_id)}
					<tr>
						<td>
							{row.name}
							{#if row.not_owned > 0}
								<span class="not-owned" title="placeholder copies, not counted as owned">
									+{row.not_owned} not owned
								</span>
							{/if}
						</td>
						<td><strong>{row.owned}</strong></td>
						<td>{row.in_collection || ''}</td>
						<td>{row.in_deck || ''}</td>
						<td>{row.in_mail || ''}</td>
						<td class="decks">{row.decks.join(', ')}</td>
					</tr>
				{/each}
			</tbody>
		</table>
		</div>
	{/if}
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
	.table-scroll {
		overflow-x: auto;
		-webkit-overflow-scrolling: touch;
		margin-bottom: 1rem;
	}
	.table-scroll:focus-visible {
		outline: 2px solid currentColor;
		outline-offset: 2px;
	}
	.table-scroll table {
		min-width: 40rem;
		margin-bottom: 0;
	}
	.visually-hidden {
		position: absolute;
		width: 1px;
		height: 1px;
		padding: 0;
		margin: -1px;
		overflow: hidden;
		clip: rect(0, 0, 0, 0);
		white-space: nowrap;
		border: 0;
	}

	table {
		border-collapse: collapse;
		font-size: 0.85rem;
		width: 100%;
	}
	th,
	td {
		padding: 0.2rem 0.6rem 0.2rem 0;
		text-align: left;
		vertical-align: top;
	}
	thead th {
		border-bottom: 1px solid currentColor;
	}
	.decks {
		font-size: 0.8rem;
		color: var(--color-muted);
	}
	.not-owned {
		font-size: 0.75rem;
		color: var(--color-muted);
	}
	.error {
		color: var(--color-danger);
	}
</style>
