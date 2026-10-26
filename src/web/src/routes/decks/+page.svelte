<script lang="ts">
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

<svelte:head>
	<title>Decks — Commander Lab</title>
</svelte:head>

<h1>Imported decks</h1>

{#if data.decks.length === 0}
	<p>No decks imported into Postgres yet. Import one from Archidekt to see it here.</p>
{:else}
	<ul class="deck-list">
		{#each data.decks as deck (deck.id)}
			<li>
				<a href="/deck/{deck.id}">{deck.name}</a>
				<span class="meta">
					{deck.status}{#if deck.commanders.length} — {deck.commanders.join(', ')}{/if}
					{#if deck.color_identity.length}({deck.color_identity.join('/')}){/if}
				</span>
				{#if deck.archidekt_url}
					<a class="ext" href={deck.archidekt_url} rel="noreferrer">archidekt</a>
				{/if}
			</li>
		{/each}
	</ul>
{/if}

<style>
	.deck-list {
		list-style: none;
		padding: 0;
	}
	.deck-list li {
		padding: 0.5rem 0;
		border-bottom: 1px solid #333;
	}
	.meta {
		opacity: 0.7;
		margin-left: 0.5rem;
	}
	.ext {
		margin-left: 0.5rem;
		font-size: 0.85em;
	}
</style>
