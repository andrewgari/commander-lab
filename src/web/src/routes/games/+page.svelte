<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import { navigating } from '$app/state';
	import { tick } from 'svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import ErrorBanner from '#lib/components/ErrorBanner.svelte';
	import ScrollTable, { type Column } from '#lib/components/ScrollTable.svelte';
	import StatusPill from '#lib/components/StatusPill.svelte';
	import SubmitButton from '#lib/components/SubmitButton.svelte';
	import { createPending } from '#lib/forms/pending.svelte.ts';
	import { gameResult } from '#lib/status.ts';
	import type { GameRecord } from '#lib/api.ts';
	import type { ActionData, PageProps } from './$types';

	let { data, form }: PageProps & { form: ActionData } = $props();

	const pending = createPending();
	const loading = $derived(navigating.to?.url.pathname === '/games');
	const noGames = $derived(data.games.length === 0 && !data.gamesError);
	const noDecks = $derived(data.decks.length === 0 && !data.decksError);

	// Open on ?import=1, after a failed import, or when there is nothing else to show (spec 4.3).
	let userToggled: boolean | null = $state(null);
	const autoOpen = $derived(data.importRequested || !!form?.importError || noGames);
	let importOpen = $derived(userToggled ?? autoOpen);
	let details: HTMLDetailsElement | undefined = $state();
	let deckSelect: HTMLSelectElement | undefined = $state();

	function ontoggle() {
		if (details) userToggled = details.open;
	}
	async function openImport() {
		userToggled = true;
		await tick();
		(deckSelect ?? details?.querySelector('summary'))?.focus();
	}

	let logText = $state('');
	function fillFromFile(event: Event) {
		const file = (event.currentTarget as HTMLInputElement).files?.[0];
		if (file) file.text().then((text) => (logText = text));
	}

	const columns: Column[] = [
		{ key: 'deck', header: 'Deck' },
		{ key: 'result', header: 'Result' },
		{ key: 'seat_count', header: 'Players', numeric: true },
		{ key: 'turns', header: 'Turns', numeric: true },
		{ key: 'played', header: 'Played' },
		{ key: 'imported', header: 'Imported' }
	];
	const date = (raw: string | null) => (raw && /^\d{4}-\d{2}-\d{2}/.test(raw) ? raw.slice(0, 10) : null);
</script>

<svelte:head>
	<title>Games — Commander Lab</title>
</svelte:head>

<div class="head">
	<h1>Games</h1>
	{#if data.games.length > 0}<span class="count meta">{data.games.length} imported</span>{/if}
</div>

<details id="import" class="import-disc" bind:this={details} open={importOpen} {ontoggle}>
	<summary>
		<span class="plus" aria-hidden="true">+</span>
		<h2>Import a Forge log</h2>
		<span class="hint meta">Paste or pick a verbose log</span>
		<span class="chev" aria-hidden="true"></span>
	</summary>
	<div class="import-body">
		<p class="meta explainer">
			Paste a verbose Forge log (or pick the file). Import is on demand: the log is parsed into games and
			telemetry, a report is rendered, and your profile is updated. Re-importing the same log is a no-op.
		</p>
		{#if data.decksError}
			<ErrorBanner message="Deck list unavailable, can't import right now" detail={data.decksError} onRetry={() => invalidateAll()} />
		{:else if noDecks}
			<EmptyState variant="inline" title="Import a deck first" description="A log is imported against one of your decks." ctaLabel="Go to decks" ctaHref="/decks" />
		{:else}
			<form method="POST" use:enhance={pending.enhance} class="import-form">
				<div class="side">
					<label>
						Deck
						<select name="deck_id" required bind:this={deckSelect}>
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
						Played at (optional)
						<input name="played_at" type="datetime-local" aria-describedby="played-help" />
						<span id="played-help" class="meta">Forge logs carry no timestamps.</span>
					</label>
				</div>
				<div class="logbox">
					<label class="file">
						Log file
						<input type="file" accept=".log,.txt" onchange={fillFromFile} />
					</label>
					<label for="log_text">Forge log</label>
					<textarea id="log_text" name="log_text" rows="10" required readonly={pending.submitting} bind:value={logText}
						placeholder="Turn: Turn 1 (Ai(1)-…"></textarea>
				</div>
				<div class="actions">
					<SubmitButton variant="primary" pending={pending.submitting} pendingLabel="Importing…">Import &amp; analyze</SubmitButton>
					{#if pending.submitting}<span class="meta">Parsing the log can take a few seconds.</span>{/if}
				</div>
				{#if form?.importError}
					<ErrorBanner message="Import failed" detail={form.importError} />
				{/if}
				{#if pending.transientError}
					<ErrorBanner message="Import failed" detail={pending.transientError} onRetry={() => { pending.clearError(); (document.querySelector('.import-form') as HTMLFormElement | null)?.requestSubmit(); }} />
				{/if}
			</form>
		{/if}
	</div>
</details>

<section aria-labelledby="games-h" aria-busy={loading}>
	<h2 id="games-h" class="visually-hidden">Imported games</h2>
	{#if loading}<p class="visually-hidden" role="status">Loading games…</p>{/if}
	{#if data.gamesError}
		<ErrorBanner message="Games couldn't load" detail={data.gamesError} onRetry={() => invalidateAll()} />
	{:else if noGames}
		<EmptyState
			icon="trophy"
			title="No games imported yet"
			description="Import a verbose Forge log to see curves and card performance."
		>
			{#snippet actions()}
				<a class="btn btn--primary" href="?import=1#import" onclick={(e) => { e.preventDefault(); openImport(); }}>Import a Forge log</a>
			{/snippet}
		</EmptyState>
	{:else}
		<ScrollTable
			caption="Imported games, newest first"
			captionHidden
			density="compact"
			{columns}
			rows={data.games}
			rowKey={(g: GameRecord) => g.id}
			minWidth="38rem"
		>
			{#snippet cell(g: GameRecord, col: Column)}
				{#if col.key === 'deck'}
					<a class="deck-link" href="/games/{g.id}">{g.deck_name ?? g.deck_id}</a>
				{:else if col.key === 'result'}
					<span class="visually-hidden">Result: </span>
					{#if g.result === null || ['win', 'loss', 'draw'].includes(g.result)}
						<StatusPill status={gameResult(g.result)} variant="text" />
					{:else}
						<StatusPill status="neutral" label={g.result} variant="text" />
					{/if}
				{:else if col.key === 'seat_count'}
					{g.seat_count}
				{:else if col.key === 'turns'}
					{#if g.turns !== null}{g.turns}{:else}<span class="dash">—</span><span class="visually-hidden">no value</span>{/if}
				{:else if col.key === 'played'}
					{@render when(g.played_at)}
				{:else}
					{@render when(g.imported_at)}
				{/if}
			{/snippet}
		</ScrollTable>
		{#if data.games.length >= data.limit}
			<p class="meta foot">Showing the latest {data.limit} games</p>
		{/if}
	{/if}
</section>

{#snippet when(raw: string | null)}
	{#if date(raw)}<time class="mono" datetime={raw} title={date(raw)}>{date(raw)}</time>{:else}<span class="dash">—</span><span class="visually-hidden">no value</span>{/if}
{/snippet}

<style>
	.head { display: flex; align-items: baseline; gap: var(--space-3); }
	.count { font-family: var(--font-mono); font-size: var(--text-xs); }
	.import-disc { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-sm); margin-bottom: var(--space-6); }
	summary { display: flex; align-items: center; gap: var(--space-3); min-height: 3rem; padding: 0 var(--space-4); cursor: pointer; list-style: none; }
	summary::-webkit-details-marker { display: none; }
	summary h2 { margin: 0; font-size: var(--text-md); line-height: var(--leading-md); }
	summary:hover h2 { color: var(--color-accent-hover); }
	.plus { display: inline-flex; align-items: center; justify-content: center; width: 1.25rem; height: 1.25rem; border: 1px solid var(--color-accent); color: var(--color-accent); font: var(--weight-bold) var(--text-sm) var(--font-mono); }
	.hint { margin-left: auto; font-size: var(--text-xs); }
	.hint + .chev { margin-left: 0; }
	summary .chev { width: 0.5rem; height: 0.5rem; border: solid var(--color-muted); border-width: 0 2px 2px 0; transform: rotate(45deg) translateY(-2px); transition: transform var(--dur-fast) var(--ease-out); }
	details[open] > summary { border-bottom: 1px solid var(--color-border); }
	details[open] .hint { display: none; }
	details[open] > summary .chev { margin-left: auto; transform: rotate(225deg); }
	.import-body { display: grid; gap: var(--space-4); padding: var(--space-4) var(--space-5) var(--space-5); }
	.explainer { max-width: 70ch; margin: 0; }
	.import-form { display: grid; gap: var(--space-4); }
	.side, .logbox, .import-body, .import-form, label { min-width: 0; }
	input, select, textarea { width: 100%; max-width: 100%; }
	.import-body, .import-form, .side, .logbox { grid-template-columns: minmax(0, 1fr); }
	.side, .logbox { display: grid; gap: var(--space-3); align-content: start; }
	label { display: grid; gap: var(--space-1); font-size: var(--text-sm); }
	textarea { font-family: var(--font-mono); font-size: var(--text-xs); min-height: 14rem; resize: vertical; }
	.actions { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-3); }
	@media (min-width: 64rem) { .import-form { grid-template-columns: 18rem minmax(0, 1fr); } .actions, .import-form > :global(.banner) { grid-column: 1 / -1; } }
	.deck-link { color: var(--color-text); font-weight: var(--weight-medium); display: inline-block; max-width: 22rem; overflow-wrap: anywhere; }
	.mono { font-family: var(--font-mono); font-size: var(--text-xs); }
	.dash { color: var(--color-muted); }
	.foot { font-family: var(--font-mono); font-size: var(--text-xs); margin-top: var(--space-2); }
</style>
