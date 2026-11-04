<script lang="ts">
	import type { PageProps } from './$types';
	import { page } from '$app/state';
	import ScrollTable, { type Column } from '#lib/components/ScrollTable.svelte';
	import StatusPill from '#lib/components/StatusPill.svelte';

	let { data }: PageProps = $props();

	const { deck, boards, have_buy, snapshot } = $derived(data.detail);
	const boardOrder = ['commander', 'main', 'sideboard', 'maybeboard', 'considering'] as const;
	const orderedBoards = $derived(
		[...boardOrder.filter((b) => boards[b]?.length), ...Object.keys(boards).filter((b) => !boardOrder.includes(b as (typeof boardOrder)[number]))]
	);

	const columns: Column[] = [
		{ key: 'name', header: 'Card' },
		{ key: 'board', header: 'Board' },
		{ key: 'need', header: 'Need', numeric: true },
		{ key: 'have', header: 'Have', numeric: true },
		{ key: 'buy', header: 'Buy', numeric: true },
		{ key: 'pending', header: 'In mail', numeric: true },
		{ key: 'elsewhere', header: 'Elsewhere', numeric: true }
	];
</script>

<svelte:head>
	<title>{deck.name} — Commander Lab</title>
</svelte:head>

<nav><a href="/decks">← all decks</a> · <a href="/decks/{encodeURIComponent(deck.id)}/review">Deck review</a></nav>

<h1>{deck.name}</h1>
{#if page.url.searchParams.get('from_draft') === '1'}<p role="status">Built from a draft.</p>{/if}
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
		{#if have_buy.totals.complete}<StatusPill status="owned" label="Complete" />{/if}
	</p>
	<ScrollTable
		caption="Deck have/buy breakdown"
		captionHidden
		{columns}
		rows={have_buy.lines}
		rowKey={(line) => line.oracle_id + line.board}
		rowHeader="name"
		stickyFirstCol
		minWidth="52rem"
	>
		{#snippet cell(line, column)}
			{#if column.key === 'board'}
				{line.board}{line.category ? ` · ${line.category}` : ''}
			{:else if column.key === 'buy' && line.buy > 0}
				<StatusPill status="short" label="Buy {line.buy}" />
			{:else if column.key === 'name'}
				{line.name}
			{:else}
				{line[column.key as 'need' | 'have' | 'buy' | 'pending' | 'elsewhere']}
			{/if}
		{/snippet}
	</ScrollTable>
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
					{#if card.buy > 0}<StatusPill status="short" label="Buy {card.buy}" />{/if}
					{#if card.pending > 0}<StatusPill status="in-mail" />{/if}
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
	.cards {
		list-style: none;
		padding: 0;
		columns: 2;
		font-size: 0.9rem;
	}
	.summary {
		font-style: italic;
	}
	pre {
		font-size: 0.75rem;
		overflow: auto;
	}
</style>
