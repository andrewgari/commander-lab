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
		<input type="search" name="q" value={data.q} placeholder="Filter by card name…" />
		<button type="submit">Search</button>
		{#if data.q}<a href="/inventory">clear</a>{/if}
	</form>

	{#if data.inventory.length === 0}
		<p>No cards{data.q ? ` matching “${data.q}”` : ''} in the collection.</p>
	{:else}
		<table>
			<thead>
				<tr>
					<th>Card</th>
					<th>Owned</th>
					<th>In collection</th>
					<th>In decks</th>
					<th>In mail</th>
					<th>Decks</th>
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
	{/if}
{/if}

<style>
	h1 {
		font-size: 1.5rem;
		margin-bottom: 0.25rem;
	}
	.totals {
		opacity: 0.85;
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
		padding: 0.35rem 0.5rem;
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
		opacity: 0.75;
	}
	.not-owned {
		font-size: 0.75rem;
		opacity: 0.6;
	}
	.error {
		color: var(--color-danger);
	}
</style>
