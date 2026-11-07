<script lang="ts">
	import type { DeckSummary } from '#lib/api.ts';
	import ManaPips from '#lib/components/ManaPips.svelte';
	import StatusPill from '#lib/components/StatusPill.svelte';
	import { fallback, type StatusKey } from '#lib/status.ts';
	import type { RecentReason } from '#lib/home/stats.ts';

	let { deck, reason }: { deck: DeckSummary; reason: RecentReason | null } = $props();

	// Deck status has no catalog entry of its own; map onto the generic roles + a plain-word label.
	const STATUS: Record<string, { key: StatusKey; label: string }> = {
		have: { key: 'success', label: 'Built' },
		virtual: { key: 'info', label: 'Virtual' },
		pending: { key: 'warning', label: 'Pending' },
		possible: { key: 'neutral', label: 'Possible' }
	};
	const st = $derived(STATUS[deck.status] ?? { key: fallback(deck.status), label: deck.status });
	const hb = $derived(deck.have_buy);
</script>

<div class="row">
	<div class="pips"><ManaPips identity={deck.color_identity} /></div>
	<div class="main">
		<a class="name" href="/decks/{encodeURIComponent(deck.id)}" title={deck.name}>{deck.name}</a>
		{#if deck.commanders.length}<p class="cmd">{deck.commanders.join(' + ')}</p>{/if}
		<p class="meta line">
			<StatusPill status={st.key} label={st.label} icon={null} variant="text" />
			{#if reason}
				<span>
					{reason.label}
					{#if reason.when?.datetime}<time datetime={reason.when.datetime} title={reason.when.title}>{reason.when.text}</time>{:else if reason.when}{reason.when.text}{/if}
				</span>
			{/if}
		</p>
	</div>
	<div class="have">
		{#if hb}
			<span class="count">{hb.have}/{hb.need}</span>
			{#if hb.buy > 0}
				<StatusPill status="short" label="Buy {hb.buy}" variant="text" />
			{:else}
				<StatusPill status="success" label="Complete" variant="text" />
			{/if}
		{:else}
			<span class="meta nc">Not computed</span>
		{/if}
	</div>
	<div class="act">
		<a class="btn btn--secondary" href="/decks/{encodeURIComponent(deck.id)}/review">
			<span aria-hidden="true">Review</span><span class="visually-hidden">Review {deck.name}</span>
		</a>
	</div>
</div>

<style>
	.row {
		display: grid; gap: var(--space-2) var(--space-4); align-items: center;
		grid-template-columns: minmax(0, 1fr) auto;
		grid-template-areas: 'pips pips' 'main main' 'have act';
		padding: var(--space-3) 0; border-bottom: 1px solid var(--color-border);
	}
	.pips { grid-area: pips; }
	.main { grid-area: main; min-width: 0; }
	.have { grid-area: have; display: grid; grid-template-columns: 4.5rem auto; align-items: center; justify-content: start; gap: var(--space-2); }
	.act { grid-area: act; }
	.nc { grid-column: 1 / -1; white-space: nowrap; }
	.name {
		display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; line-clamp: 2; overflow: hidden;
		font-size: var(--text-md); font-weight: var(--weight-bold); color: var(--color-text); text-decoration: none;
		overflow-wrap: anywhere;
	}
	.name:hover { color: var(--color-accent-hover); text-decoration: underline; }
	.cmd {
		margin: 0; color: var(--color-text-body);
		display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; line-clamp: 2; overflow: hidden;
	}
	.line { margin: var(--space-1) 0 0; display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); font-family: var(--font-mono); font-size: var(--text-xs); }
	.count { font-family: var(--font-mono); text-align: right; font-variant-numeric: tabular-nums; color: var(--color-text); }
	.have :global(.pill) { white-space: nowrap; }
	.act :global(.btn) { text-decoration: none; }
	@media (min-width: 40rem) {
		.row {
			grid-template-columns: 6.75rem minmax(0, 1fr) 12rem auto;
			grid-template-areas: 'pips main have act';
		}
		.name { display: block; -webkit-line-clamp: unset; line-clamp: unset; white-space: nowrap; text-overflow: ellipsis; }
	}
</style>
