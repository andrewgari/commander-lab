<script lang="ts">
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

<svelte:head>
	<title>Decks — Commander Lab</title>
</svelte:head>

<h1>Decks</h1>

{#if data.error}
	<p class="error">Deck data unavailable: {data.error}</p>
{:else if data.decks.length === 0}
	<p>No imported decks yet.</p>
{:else}
	<ul class="deck-list">
		{#each data.decks as deck (deck.id)}
			<li>
				<a href="/decks/{encodeURIComponent(deck.id)}">{deck.name}</a>
				<span class="meta">
					{deck.status} · {deck.card_qty} cards
					{#if deck.commanders.length}· {deck.commanders.join(' + ')}{/if}
				</span>
				{#if deck.have_buy}
					<span class="have-buy">
						have {deck.have_buy.have}/{deck.have_buy.need}
						{#if deck.have_buy.pending}(+{deck.have_buy.pending} in mail){/if}
						· buy {deck.have_buy.buy}
						{#if deck.have_buy.complete}· ✅ complete{/if}
					</span>
				{/if}
			</li>
		{/each}
	</ul>
{/if}

<style>
	h1 {
		font-size: 1.5rem;
	}
	.deck-list {
		list-style: none;
		padding: 0;
		display: grid;
		gap: 0.75rem;
	}
	.deck-list li {
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
	}
	.meta,
	.have-buy {
		font-size: 0.85rem;
		opacity: 0.75;
	}
	.error {
		color: #c0392b;
	}
</style>
