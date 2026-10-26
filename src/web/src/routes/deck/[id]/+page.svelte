<script lang="ts">
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	let deck = $derived(data.detail.deck);
	let haveBuy = $derived(data.detail.have_buy);
	let totals = $derived(haveBuy.totals);
</script>

<svelte:head>
	<title>{deck.name} — Commander Lab</title>
</svelte:head>

<header>
	<h1>{deck.name}</h1>
	<p class="meta">
		{deck.status}
		{#if deck.commanders.length} — led by {deck.commanders.join(', ')}{/if}
		{#if deck.color_identity.length}({deck.color_identity.join('/')}){/if}
		{#if deck.archidekt_url}
			· <a href={deck.archidekt_url} rel="noreferrer">archidekt</a>
		{/if}
	</p>
</header>

<section class="have-buy">
	<h2>Have / Buy</h2>
	<p class="totals">
		<strong>{totals.have}</strong> of <strong>{totals.required}</strong> owned
		({totals.have_in_deck} in deck, {totals.have_from_collection} from collection)
		{#if totals.pending}· {totals.pending} on the way{/if}
		— <strong>{totals.buy}</strong> to buy
	</p>
	<table>
		<thead>
			<tr>
				<th>Card</th>
				<th>Board</th>
				<th>Role</th>
				<th>Need</th>
				<th>Have</th>
				<th>Pending</th>
				<th>Buy</th>
			</tr>
		</thead>
		<tbody>
			{#each haveBuy.cards as card (card.oracle_id)}
				<tr class:short={card.buy > 0}>
					<td>{card.name}</td>
					<td>{card.board}</td>
					<td>{card.category ?? '—'}</td>
					<td>{card.required}</td>
					<td>{card.have}</td>
					<td>{card.pending || ''}</td>
					<td>{card.buy || ''}</td>
				</tr>
			{/each}
		</tbody>
	</table>
</section>

{#if data.snapshot}
	<section class="profile">
		<h2>Profile snapshot</h2>
		<p>{data.snapshot.summary_text}</p>
	</section>
{/if}

<style>
	.meta {
		opacity: 0.75;
	}
	table {
		border-collapse: collapse;
		width: 100%;
	}
	th,
	td {
		text-align: left;
		padding: 0.3rem 0.6rem;
		border-bottom: 1px solid #333;
	}
	tr.short td:last-child {
		font-weight: 700;
	}
	.totals {
		font-size: 1.05em;
	}
</style>
