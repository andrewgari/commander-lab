<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import type { ActionData, PageProps } from './$types';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import ErrorBanner from '#lib/components/ErrorBanner.svelte';
	import StatusPill from '#lib/components/StatusPill.svelte';
	import SubmitButton from '#lib/components/SubmitButton.svelte';
	import { createPending } from '#lib/forms/pending.svelte.ts';
	import { gameResult } from '#lib/status.ts';

	let { data, form }: PageProps & { form: ActionData } = $props();

	const p = createPending();
	let formEl = $state<HTMLFormElement>();

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
{#if data.error}
	<ErrorBanner message="Game data unavailable" detail={data.error} onRetry={() => invalidateAll()} retryLabel="Reload games" />
{/if}

<section class="import">
	<h2>Import a Forge log</h2>
	<p class="meta">
		Paste a verbose Forge log (or pick the file). Import is on demand: the log is parsed into
		games and telemetry, a report is rendered, and your profile is updated. Re-importing the
		same log is a no-op.
	</p>
	{#if data.decks.length === 0 && !data.error}
		<EmptyState variant="inline" title="No decks to import for" description="Import a deck first." ctaLabel="Go to Decks" ctaHref="/decks" />
	{:else}
		<form method="POST" use:enhance={p.enhance} bind:this={formEl} aria-busy={p.submitting}>
			<fieldset class="fields" disabled={p.submitting}>
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
			<label for="log_text">Forge log</label>
			<textarea id="log_text" name="log_text" rows="10" required
				placeholder="Turn: Turn 1 (Ai(1)-…"></textarea>
			</fieldset>
			<SubmitButton pending={p.submitting} pendingLabel="Importing…" variant="primary">Import &amp; analyze</SubmitButton>
			<p class="meta">Parsing the log can take a few seconds.</p>
			{#if p.transientError}
				<ErrorBanner message="Import failed" detail={p.transientError} onRetry={() => formEl?.requestSubmit()} />
			{:else if form?.importError}
				<ErrorBanner message="Import failed" detail={form.importError} dismissible />
			{/if}
		</form>
	{/if}
</section>

<section>
	<h2>Imported games</h2>
	{#if data.error}
		<!-- the page-level banner above carries the error -->
	{:else if data.games.length === 0}
		<EmptyState variant="inline" title="No games imported yet" description="Import a Forge log using the form above." />
	{:else}
		<ul class="game-list">
			{#each data.games as game (game.id)}
				<li>
					<a href="/games/{game.id}">{game.deck_name ?? game.deck_id}</a>
					<span class="meta">
						<StatusPill status={gameResult(game.result)} label={gameResult(game.result) === 'neutral' ? (game.result ?? undefined) : undefined} />
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
		font-family: var(--font-mono);
		font-size: 0.8rem;
	}
	.fields {
		display: grid;
		gap: 0.5rem;
		border: 0;
		padding: 0;
		margin: 0;
		min-width: 0;
	}
	select,
	input:not([type="file"]) {
		min-height: 44px;
		box-sizing: border-box;
	}
	input[type="file"] {
		min-height: 44px;
	}
	nav a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
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
	.game-list li > a {
		display: block;
		padding: 0.4rem 0;
		min-height: 44px;
		box-sizing: border-box;
	}
</style>
