<script lang="ts">
	// Archetype/category mix as horizontal share bars (metrics.archetype_mix).
	interface Category {
		category: string;
		cards: number;
		share: number;
	}

	interface Props {
		categories: Category[];
		limit?: number;
	}

	let { categories, limit = 8 }: Props = $props();

	const shown = $derived(categories.slice(0, limit));
	const maxShare = $derived(Math.max(0.0001, ...shown.map((c) => c.share)));
</script>

{#if shown.length === 0}
	<p class="empty">No category data.</p>
{:else}
	<ul class="mix">
		{#each shown as cat (cat.category)}
			<li>
				<span class="name">{cat.category}</span>
				<span class="track">
					<span class="fill" style:width="{(cat.share / maxShare) * 100}%"></span>
				</span>
				<span class="value">{cat.cards} · {(cat.share * 100).toFixed(0)}%</span>
			</li>
		{/each}
	</ul>
{/if}

<style>
	.mix {
		list-style: none;
		padding: 0;
		margin: 0;
		display: grid;
		gap: 0.3rem;
	}
	li {
		display: grid;
		grid-template-columns: minmax(6rem, 10rem) 1fr auto;
		gap: 0.5rem;
		align-items: center;
		font-size: 0.85rem;
	}
	.name {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.track {
		display: block;
		height: 0.7rem;
		background: rgba(127, 127, 127, 0.18);
		border-radius: 3px;
		overflow: hidden;
	}
	.fill {
		display: block;
		height: 100%;
		background: #4a7dbd;
		border-radius: 3px;
	}
	.value {
		font-size: 0.75rem;
		color: var(--text-3, var(--color-muted));
		white-space: nowrap;
	}
	.empty {
		color: var(--text-3, var(--color-muted));
		font-size: 0.85rem;
	}
</style>
