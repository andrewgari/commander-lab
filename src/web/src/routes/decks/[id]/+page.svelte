<script lang="ts">
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const { deck, boards, have_buy, snapshot } = $derived(data.detail);
	const boardOrder = ['commander', 'main', 'sideboard', 'maybeboard', 'considering'] as const;
	const orderedBoards = $derived(
		[...boardOrder.filter((b) => boards[b]?.length), ...Object.keys(boards).filter((b) => !boardOrder.includes(b as (typeof boardOrder)[number]))]
	);
</script>

<svelte:head>
	<title>{deck.name} — Commander Lab</title>
</svelte:head>

<nav><a href="/decks">← all decks</a> · <a href="/decks/{encodeURIComponent(deck.id)}/review">Deck review</a></nav>

<h1>{deck.name}</h1>
<p class="meta">
	{deck.status} · {deck.color_identity.join('') || 'colorless'}
	{#if deck.commanders.length}· {deck.commanders.join(' + ')}{/if}
	{#if deck.archidekt_id}
		· <a href="https://archidekt.com/decks/{deck.archidekt_id}" rel="noreferrer">archidekt</a>
	{/if}
</p>

<section class="have-buy">
	<h2>Have / Buy</h2>
	<p class="totals">
		<strong>{have_buy.totals.have}</strong> / {have_buy.totals.need} owned
		{#if have_buy.totals.have_pct !== null}({have_buy.totals.have_pct}%){/if}
		{#if have_buy.totals.pending}· {have_buy.totals.pending} in mail{/if}
		· <strong>{have_buy.totals.buy}</strong> to buy
		{#if have_buy.totals.complete}· ✅ deck complete{/if}
	</p>
	<table>
		<thead>
			<tr><th>Card</th><th>Board</th><th>Need</th><th>Have</th><th>Buy</th><th>In mail</th><th>Elsewhere</th></tr>
		</thead>
		<tbody>
			{#each have_buy.lines as line (line.oracle_id + line.board)}
				<tr class:short={line.buy > 0}>
					<td>{line.name}</td>
					<td>{line.board}{line.category ? ` · ${line.category}` : ''}</td>
					<td>{line.need}</td>
					<td>{line.have}</td>
					<td>{line.buy}</td>
					<td>{line.pending || ''}</td>
					<td>{line.elsewhere || ''}</td>
				</tr>
			{/each}
		</tbody>
	</table>
</section>

{#if snapshot}
	<section class="profile">
		<h2>Deck profile</h2>
		<p class="summary">{snapshot.summary_text}</p>
		<details>
			<summary>metrics</summary>
			<pre>{JSON.stringify(snapshot.metrics, null, 2)}</pre>
		</details>
	</section>
{/if}

{#each orderedBoards as board (board)}
	<section>
		<h2>{board} ({boards[board].reduce((n, c) => n + c.qty, 0)})</h2>
		<ul class="cards">
			{#each boards[board] as card (card.oracle_id + card.board)}
				<li>
					{card.qty}× <a href="/card/{encodeURIComponent(card.oracle_id)}">{card.name}</a>
					{#if card.buy > 0}<span class="buy-tag">buy {card.buy}</span>{/if}
					{#if card.pending > 0}<span class="pending-tag">in mail</span>{/if}
				</li>
			{/each}
		</ul>
	</section>
{/each}

<style>
	h1 {
		font-size: 1.5rem;
		margin-bottom: 0.25rem;
	}
	.meta {
		opacity: 0.75;
	}
	table {
		border-collapse: collapse;
		font-size: 0.85rem;
	}
	th,
	td {
		padding: 0.15rem 0.6rem 0.15rem 0;
		text-align: left;
	}
	tr.short td {
		color: #c0392b;
	}
	.cards {
		list-style: none;
		padding: 0;
		columns: 2;
		font-size: 0.9rem;
	}
	.buy-tag {
		color: #c0392b;
		font-size: 0.8rem;
	}
	.pending-tag {
		color: #b8860b;
		font-size: 0.8rem;
	}
	.summary {
		font-style: italic;
	}
	pre {
		font-size: 0.75rem;
		overflow: auto;
	}
</style>
