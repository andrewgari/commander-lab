<script lang="ts">
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const { game, meta, players, cards } = $derived(data.report);
	const unavailable = $derived(meta.unavailable ?? {});
	const userCards = $derived(cards.filter((c) => c.seat === meta.user_seat));
	const title = $derived(game.deck_name ?? game.deck_id);
</script>

<svelte:head>
	<title>Game {game.id} — {title} — Commander Lab</title>
</svelte:head>

<nav><a href="/games">← all games</a></nav>

{#if data.imported}<p class="notice">Log imported — this is the game it produced.</p>{/if}
{#if data.duplicate}<p class="notice">That log was already imported — this is the existing game (no duplicate created).</p>{/if}

<h1>
	{title}
	{#if game.result}
		<span class:win={game.result === 'win'} class:loss={game.result === 'loss'} class="result">
			{game.result}
		</span>
	{/if}
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
	{#if userCards.length === 0}
		<p class="meta">No casts recorded for your seat.</p>
	{:else}
		<!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard-scrollable region) -->
		<div class="table-scroll" role="region" aria-label="Casts for your seat" tabindex="0">
		<table>
			<caption class="visually-hidden">Casts for your seat</caption>
			<thead>
				<tr><th scope="col">Card</th><th scope="col">Cast turn</th><th scope="col">Drawn</th><th scope="col">Stuck in hand</th></tr>
			</thead>
			<tbody>
				{#each userCards as card (card.card)}
					<tr>
						<td>
							{card.card}
							{#if !card.oracle_resolved}<span class="meta">(reference unavailable)</span>{/if}
						</td>
						<td>{card.cast_turn ?? '—'}</td>
						<td class="unavailable">unavailable — {card.unavailable.drawn}</td>
						<td class="unavailable">unavailable — {card.unavailable.stuck}</td>
					</tr>
				{/each}
			</tbody>
		</table>
		</div>
	{/if}
</section>

<section>
	<h2>Turn curves</h2>
	{#each players as player (player.seat)}
		<h3>
			Seat {player.seat} — {player.deck}
			{#if player.is_user_seat}<span class="you">your deck</span>{/if}
		</h3>
		<!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard-scrollable region) -->
		<div class="table-scroll" role="region" aria-label="Mana per turn, seat {player.seat}" tabindex="0">
		<table>
			<caption class="visually-hidden">Mana per turn — seat {player.seat}</caption>
			<thead>
				<tr><th scope="col">Turn</th><th scope="col">Land played</th><th scope="col">Spells cast</th><th scope="col">Mana spent</th></tr>
			</thead>
			<tbody>
				{#each player.points as point (point.turn)}
					<tr>
						<td>{point.turn}</td>
						<td>{point.land_played ?? ''}</td>
						<td>{point.spells_cast}</td>
						<td class="unavailable">unavailable — {player.unavailable.mana_spent}</td>
					</tr>
				{/each}
			</tbody>
		</table>
		</div>
	{/each}
</section>

<details>
	<summary>All cards cast (every seat)</summary>
	<!-- svelte-ignore a11y_no_noninteractive_tabindex (keyboard-scrollable region) -->
	<div class="table-scroll" role="region" aria-label="Casts across seats" tabindex="0">
	<table>
		<caption class="visually-hidden">Casts across seats</caption>
		<thead>
			<tr><th scope="col">Seat</th><th scope="col">Card</th><th scope="col">Cast turn</th><th scope="col">Drawn</th><th scope="col">Stuck</th></tr>
		</thead>
		<tbody>
			{#each cards as card (`${card.seat}-${card.card}`)}
				<tr>
					<td>{card.seat}</td>
					<td>{card.card}</td>
					<td>{card.cast_turn ?? '—'}</td>
					<td class="unavailable">unavailable</td>
					<td class="unavailable">unavailable</td>
				</tr>
			{/each}
		</tbody>
	</table>
	</div>
</details>

<style>
	h1 {
		font-size: 1.5rem;
		margin-bottom: 0.25rem;
	}
	.result {
		font-size: 0.9rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.win {
		color: var(--color-success);
	}
	.loss {
		color: var(--color-danger);
	}
	.meta {
		font-size: 0.85rem;
		color: var(--color-muted);
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
	.table-scroll {
		overflow-x: auto;
		-webkit-overflow-scrolling: touch;
		margin-bottom: 1rem;
	}
	.table-scroll:focus-visible {
		outline: 2px solid currentColor;
		outline-offset: 2px;
	}
	.table-scroll table {
		min-width: 28rem;
		margin-bottom: 0;
	}
	.visually-hidden {
		position: absolute;
		width: 1px;
		height: 1px;
		padding: 0;
		margin: -1px;
		overflow: hidden;
		clip: rect(0, 0, 0, 0);
		white-space: nowrap;
		border: 0;
	}

	.summary {
		font-style: italic;
	}
	.notice {
		background: color-mix(in srgb, var(--color-success) 14%, var(--color-surface));
		border: 1px solid color-mix(in srgb, var(--color-success) 33%, transparent);
		padding: 0.4rem 0.6rem;
		font-size: 0.85rem;
	}
	table {
		border-collapse: collapse;
		font-size: 0.85rem;
		margin-bottom: 1rem;
	}
	th,
	td {
		padding: 0.15rem 0.6rem 0.15rem 0;
		text-align: left;
	}
	.unavailable {
		color: var(--color-muted);
		font-style: italic;
		font-size: 0.8rem;
	}
	.you {
		font-size: 0.75rem;
		color: var(--color-success);
	}
</style>
