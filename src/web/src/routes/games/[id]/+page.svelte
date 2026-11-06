<script lang="ts">
	import type { PageProps } from './$types';
	import ErrorBanner from '#lib/components/ErrorBanner.svelte';
	import ScrollTable, { type Column } from '#lib/components/ScrollTable.svelte';
	import StatusPill from '#lib/components/StatusPill.svelte';
	import { gameResult } from '#lib/status.ts';

	let { data }: PageProps = $props();

	const { game, meta, players, cards } = $derived(data.report);
	const unavailable = $derived(meta.unavailable ?? {});
	const userCards = $derived(cards.filter((c) => c.seat === meta.user_seat));
	const title = $derived(game.deck_name ?? game.deck_id);
	const result = $derived(gameResult(game.result));

	// README D21: a column that is unavailable on every row is dropped and explained once under the table.
	const castColumns: Column[] = [
		{ key: 'card', header: 'Card' },
		{ key: 'cast_turn', header: 'Cast turn', numeric: true }
	];
	const turnColumns: Column[] = [
		{ key: 'turn', header: 'Turn', numeric: true },
		{ key: 'land_played', header: 'Land played' },
		{ key: 'spells_cast', header: 'Spells cast', numeric: true }
	];
	const allCastColumns: Column[] = [
		{ key: 'seat', header: 'Seat', numeric: true },
		{ key: 'card', header: 'Card' },
		{ key: 'cast_turn', header: 'Cast turn', numeric: true }
	];

	function reasons(values: Array<string | undefined>): string {
		return [...new Set(values.filter((v): v is string => !!v))].join('; ');
	}
</script>

<svelte:head>
	<title>Game {game.id} — {title} — Commander Lab</title>
</svelte:head>

<nav><a href="/games">← all games</a></nav>

{#if data.imported}<ErrorBanner severity="info" message="Log imported — this is the game it produced." dismissible />{/if}
{#if data.duplicate}<ErrorBanner severity="info" message="That log was already imported — this is the existing game (no duplicate created)." dismissible />{/if}

<h1>
	{title}
	<StatusPill status={result} size="md" label={result === 'neutral' ? (game.result ?? undefined) : undefined} />
</h1>
<p class="meta">
	game #{game.id} · {game.seat_count} players
	{#if game.turns !== null}· {game.turns} turns{:else}· turn count unavailable — {unavailable.turns}{/if}
	{#if meta.duration_ms}· {Math.round(meta.duration_ms / 1000)}s{/if}
</p>
<p class="meta">
	{#if game.played_at}
		played {game.played_at}
	{:else}
		played date unavailable — {unavailable.played_at}
	{/if}
	· imported {game.imported_at?.slice(0, 10)} · parser {game.parser_version}
</p>

<section>
	<h2>Analysis notes</h2>
	<p class="summary">{game.notes}</p>
</section>

<section>
	<h2>Cards cast — your seat {#if meta.user_seat}(seat {meta.user_seat}){/if}</h2>
	<ScrollTable
		caption="Casts for your seat"
		captionHidden
		columns={castColumns}
		rows={userCards}
		rowKey={(c) => c.card}
		rowHeader="card"
		stickyFirstCol
		minWidth="20rem"
		emptyTitle="No casts recorded for your seat"
	>
		{#snippet cell(card, column)}
			{#if column.key === 'card'}
				{card.card}
				{#if !card.oracle_resolved}<span class="meta">(reference unavailable)</span>{/if}
			{:else}
				{card.cast_turn ?? '—'}
			{/if}
		{/snippet}
	</ScrollTable>
	{#if userCards.length > 0}
		<p class="meta">Drawn and Stuck in hand are unavailable: {reasons(userCards.flatMap((c) => [c.unavailable.drawn, c.unavailable.stuck]))}</p>
	{/if}
</section>

<section>
	<h2>Turn curves</h2>
	{#each players as player (player.seat)}
		<h3>
			Seat {player.seat} — {player.deck}
			{#if player.is_user_seat}<StatusPill status="neutral" label="Your deck" icon={null} />{/if}
		</h3>
		<ScrollTable
			caption="Mana per turn — seat {player.seat}"
			captionHidden
			columns={turnColumns}
			rows={player.points}
			rowKey={(pt) => pt.turn}
			rowHeader="turn"
			stickyFirstCol
			minWidth="20rem"
			emptyTitle="No turns recorded"
		>
			{#snippet cell(point, column)}
				{point[column.key as 'turn' | 'land_played' | 'spells_cast'] ?? ''}
			{/snippet}
		</ScrollTable>
		{#if player.points.length > 0}
			<p class="meta">Mana spent is unavailable: {player.unavailable.mana_spent}</p>
		{/if}
	{/each}
</section>

<details>
	<summary>All cards cast (every seat)</summary>
	<ScrollTable
		caption="Casts across seats"
		captionHidden
		columns={allCastColumns}
		rows={cards}
		rowKey={(c) => `${c.seat}-${c.card}`}
		minWidth="20rem"
		emptyTitle="No casts recorded"
	/>
	{#if cards.length > 0}
		<p class="meta">Drawn and Stuck are unavailable for every card.</p>
	{/if}
</details>

<style>
	h1 {
		font-size: 1.5rem;
		margin-bottom: 0.25rem;
	}
	nav a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
	}
	summary {
		min-height: 44px;
		display: flex;
		align-items: center;
		cursor: pointer;
	}
	.summary {
		font-style: italic;
	}
</style>
