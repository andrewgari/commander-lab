<script lang="ts">
	import type { ActionData, PageProps } from './$types';

	let { data, form }: PageProps & { form: ActionData } = $props();

	function fillFromFile(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		const textarea = document.getElementById('log_text') as HTMLTextAreaElement | null;
		if (!file || !textarea) return;
		file.text().then((text) => {
			textarea.value = text;
		});
	}
</script>

<svelte:head>
	<title>Games — Commander Lab</title>
</svelte:head>

<nav><a href="/">← home</a></nav>
<h1>Games</h1>

<section class="import">
	<h2>Import a Forge log</h2>
	<p class="meta">
		Paste a verbose Forge log (or pick the file). Import is on demand: the log is parsed into
		games and telemetry, a report is rendered, and your profile is updated. Re-importing the
		same log is a no-op.
	</p>
	{#if data.decks.length === 0 && !data.error}
		<p class="meta">No imported decks yet — <a href="/decks">import a deck first</a>.</p>
	{:else}
		<form method="POST">
			<label>
				Deck
				<select name="deck_id" required>
					{#each data.decks as deck (deck.id)}
						<option value={deck.id}>{deck.name}</option>
					{/each}
				</select>
			</label>
			<label>
				Seat
				<input name="seat" type="number" min="1" max="8" value="1" />
			</label>
			<label>
				Played at (optional — Forge logs carry no timestamps)
				<input name="played_at" type="datetime-local" />
			</label>
			<label>
				Log file
				<input type="file" accept=".log,.txt" onchange={fillFromFile} />
			</label>
			<textarea id="log_text" name="log_text" rows="10" required
				placeholder="Turn: Turn 1 (Ai(1)-…"></textarea>
			<button type="submit">Import &amp; analyze</button>
			{#if form?.importError}<p class="error">Import failed: {form.importError}</p>{/if}
		</form>
	{/if}
</section>

<section>
	<h2>Imported games</h2>
	{#if data.error}
		<p class="error">Game data unavailable: {data.error}</p>
	{:else if data.games.length === 0}
		<p>No games imported yet.</p>
	{:else}
		<ul class="game-list">
			{#each data.games as game (game.id)}
				<li>
					<a href="/games/{game.id}">{game.deck_name ?? game.deck_id}</a>
					<span class="meta">
						{#if game.result}{game.result}{:else}result unavailable{/if}
						· {game.seat_count} players
						{#if game.turns !== null}· {game.turns} turns{/if}
						{#if game.played_at}· played {game.played_at.slice(0, 10)}{/if}
						· imported {game.imported_at?.slice(0, 10)}
					</span>
				</li>
			{/each}
		</ul>
	{/if}
</section>

<style>
	h1 {
		font-size: 1.5rem;
	}
	form {
		display: grid;
		gap: 0.5rem;
		max-width: 42rem;
	}
	label {
		display: grid;
		gap: 0.15rem;
		font-size: 0.85rem;
	}
	textarea {
		font-family: monospace;
		font-size: 0.8rem;
	}
	button {
		width: fit-content;
		padding: 0.4rem 1rem;
	}
	.game-list {
		list-style: none;
		padding: 0;
		display: grid;
		gap: 0.5rem;
	}
	.game-list li {
		display: flex;
		flex-direction: column;
	}
	.meta {
		font-size: 0.85rem;
		opacity: 0.75;
	}
	.error {
		color: #c0392b;
	}
</style>
