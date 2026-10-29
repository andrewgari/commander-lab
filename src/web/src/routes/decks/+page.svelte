<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import type { PageProps } from './$types';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import ErrorBanner from '#lib/components/ErrorBanner.svelte';
	import StatusPill from '#lib/components/StatusPill.svelte';

	let { data }: PageProps = $props();
</script>

<svelte:head>
	<title>Decks — Commander Lab</title>
</svelte:head>

<h1>Decks</h1>

{#if data.error}
	<ErrorBanner message="Deck data unavailable" detail={data.error} onRetry={() => invalidateAll()} retryLabel="Reload decks" />
{:else if data.decks.length === 0}
	<EmptyState title="No decks yet" description="Import a deck from Archidekt or Moxfield to start analysing it." />
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
						{#if deck.have_buy.complete}<StatusPill status="owned" label="Complete" />{/if}
						{#if deck.have_buy.buy > 0}<StatusPill status="short" label="Buy {deck.have_buy.buy}" />{/if}
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
	.have-buy {
		font-size: 0.85rem;
		color: var(--color-muted);
	}
</style>
