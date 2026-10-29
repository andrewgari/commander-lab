<script lang="ts">
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { printingHref } from '#lib/card-printing.ts';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import ErrorBanner from '#lib/components/ErrorBanner.svelte';
	import ScrollTable, { type Column } from '#lib/components/ScrollTable.svelte';
	import SubmitButton from '#lib/components/SubmitButton.svelte';
	import { createPending } from '#lib/forms/pending.svelte.ts';
	import type { PageProps } from './$types';

	let { data, form }: PageProps = $props();
	const dossier = $derived(data.dossier);
	const card = $derived(dossier.card);
	const selected = $derived(dossier.printing);
	const art = $derived(
		selected?.image_uri_normal || card.image_uri || ''
	);
	let selectedId = $state(data.dossier.printing?.scryfall_id ?? '');

	const pending = createPending();
	let notesForm = $state<HTMLFormElement>();

	const deckColumns: Column[] = [
		{ key: 'name', header: 'Deck' },
		{ key: 'status', header: 'Status' },
		{ key: 'board', header: 'Board' },
		{ key: 'category', header: 'Category' },
		{ key: 'qty', header: 'Qty', numeric: true }
	];

	// Client-side navigation: no full reload. reset:false (Kit 3 replacement for keepFocus/noScroll) keeps the <select> focused and the scroll position.
	function switchPrinting() {
		if (!selectedId) return;
		void goto(printingHref(card.oracle_id, selectedId), { reset: false });
	}
</script>

<svelte:head>
	<title>{card.name} — Commander Lab</title>
</svelte:head>

<nav class="back"><a href="/decks">← Decks</a></nav>

<div class="dossier">
	<section class="identity">
		{#if art}
			<img class="art" src={art} alt={card.name} />
		{:else}
			<EmptyState variant="inline" compact title="No card art yet" />
		{/if}
		<div>
			<h1>{card.name}</h1>
			<p class="meta">
				{#if card.mana_cost}<span class="mana">{card.mana_cost}</span>{/if}
				{card.type_line}
				{#if card.cmc}<span class="meta">· MV {card.cmc}</span>{/if}
			</p>
			{#if card.oracle_text}
				<p class="oracle">{card.oracle_text}</p>
			{:else}
				<EmptyState variant="inline" compact title="No oracle text on record" />
			{/if}
			{#if card.keywords.length}
				<p class="meta">Keywords: {card.keywords.join(', ')}</p>
			{/if}
		</div>
	</section>

	<section>
		<h2>Printings ({dossier.printings.length})</h2>
		{#if dossier.printings.length === 0}
			<EmptyState variant="inline" compact title="No printings data yet" />
		{:else}
			<form method="GET" class="printing-switch">
				<label for="printing">Art / printing</label>
				<select id="printing" name="printing" bind:value={selectedId} onchange={switchPrinting}>
					{#each dossier.printings as p (p.scryfall_id)}
						<option value={p.scryfall_id}>
							{p.set_name || p.set_code.toUpperCase()} #{p.collector_number}
							{p.rarity}{p.released_at ? ` · ${p.released_at}` : ''}{p.is_default ? ' (default)' : ''}
						</option>
					{/each}
				</select>
				<noscript><button type="submit">Switch</button></noscript>
			</form>
			{#if selected}
				<p class="meta">
					{selected.set_name || selected.set_code.toUpperCase()}
					· #{selected.collector_number} · {selected.rarity}
					{#if selected.released_at}· released {selected.released_at}{/if}
				</p>
			{/if}
		{/if}
	</section>

	<section>
		<h2>Tags ({dossier.tags.length})</h2>
		{#if dossier.tags.length === 0}
			<EmptyState variant="inline" compact title="No tags yet" />
		{:else}
			<ul class="pills">
				{#each dossier.tags as t (t.tag + t.source)}
					<li class="pill" class:manual={t.source === 'manual'}>{t.tag} <span class="src">{t.source}</span></li>
				{/each}
			</ul>
		{/if}
	</section>

	<section>
		<h2>In your decks ({dossier.decks.length})</h2>
		<ScrollTable
			caption="Decks containing this card"
			captionHidden
			columns={deckColumns}
			rows={dossier.decks}
			rowKey={(d) => d.deck_id + d.board}
			rowHeader="name"
			minWidth="32rem"
			emptyTitle="Not in any deck"
		>
			{#snippet cell(d, column)}
				{#if column.key === 'name'}
					<a href="/decks/{encodeURIComponent(d.deck_id)}">{d.name}</a>
				{:else if column.key === 'category'}
					{d.category ?? '—'}
				{:else}
					{d[column.key as 'status' | 'board' | 'qty']}
				{/if}
			{/snippet}
		</ScrollTable>
	</section>

	<section>
		<h2>Synergy neighbors ({dossier.synergy.length})</h2>
		{#if dossier.synergy.length === 0}
			<EmptyState variant="inline" compact title="No synergy data yet" description="Run an interaction rebuild." />
		{:else}
			<ul class="synergy">
				{#each dossier.synergy as s (s.oracle_id + s.direction)}
					<li>
						<a href="/card/{encodeURIComponent(s.oracle_id)}">{s.name}</a>
						<span class="weight">w{s.weight}</span>
						<span class="dir">{s.direction === 'out' ? 'enables →' : '← enabled by'}</span>
						{#if s.reason_text}<p class="reason">{s.reason_text}</p>{/if}
					</li>
				{/each}
			</ul>
		{/if}
	</section>

	<section>
		<h2>Collection</h2>
		{#if dossier.collection.owned}
			<p>
				Owned — {dossier.collection.copies}
				{dossier.collection.copies === 1 ? 'copy' : 'copies'}
				({dossier.collection.statuses.join(', ')})
				{#if dossier.collection.finishes.length}
					· finishes: {dossier.collection.finishes.join(', ')}
				{/if}
			</p>
		{:else}
			<EmptyState variant="inline" compact title="Not owned" />
		{/if}
	</section>

	<section>
		<h2>Affinity &amp; notes</h2>
		{#if form?.savedAffinity}<p class="meta" role="status">Saved.</p>{/if}
		<form method="POST" action="?/save_notes" class="notes-form" use:enhance={pending.enhance} bind:this={notesForm} aria-busy={pending.submitting}>
			<fieldset class="fields" disabled={pending.submitting}>
			<label for="affinity">Affinity (1–5, empty for none)</label>
			<input
				id="affinity"
				name="affinity"
				type="number"
				min="1"
				max="5"
				value={dossier.affinity?.affinity ?? ''}
			/>
			<label for="notes">Notes</label>
			<textarea id="notes" name="notes" rows="4">{dossier.affinity?.notes ?? ''}</textarea>
			</fieldset>
			<SubmitButton pending={pending.submitting} pendingLabel="Saving notes…" variant="primary">Save notes</SubmitButton>
			{#if pending.transientError}
				<ErrorBanner message="Couldn't save notes" detail={pending.transientError} onRetry={() => notesForm?.requestSubmit()} />
			{:else if form?.actionError}
				<ErrorBanner message="Couldn't save notes" detail={form.actionError} dismissible />
			{/if}
		</form>
		{#if dossier.affinity?.updated_at}
			<p class="meta">last updated {dossier.affinity.updated_at}</p>
		{/if}
	</section>
</div>

<style>
	.dossier {
		max-width: 60rem;
	}
	.identity {
		display: flex;
		gap: 1rem;
		align-items: flex-start;
	}
	.art {
		width: 16rem;
		max-width: 100%;
		border-radius: 0.5rem;
	}
	/* The text column must be allowed to shrink, or long names/oracle text force the row wider than the viewport. */
	.identity > div {
		min-width: 0;
	}
	@media (max-width: 30rem) {
		.identity {
			flex-direction: column;
		}
		.art {
			width: 100%;
			max-width: 18rem;
		}
	}
	.back a {
		display: inline-flex;
		align-items: center;
		min-height: var(--touch-min);
		min-width: var(--touch-min);
	}
	.synergy li a {
		display: inline-flex;
		align-items: center;
		min-height: var(--touch-min);
	}
	.notes-form input,
	.notes-form textarea,
		.printing-switch select {
		min-height: var(--touch-min);
		box-sizing: border-box;
	}
	.notes-form textarea {
		min-height: 6rem;
	}
	.fields {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		border: 0;
		padding: 0;
		margin: 0;
		min-width: 0;
	}
	h1 {
		font-size: 1.5rem;
		margin-bottom: 0.25rem;
	}
	h2 {
		font-size: 1.05rem;
		margin: 1.25rem 0 0.35rem;
	}
	.mana {
		font-family: var(--font-mono);
		margin-right: 0.35rem;
	}
	.oracle {
		white-space: pre-line;
		font-size: 0.9rem;
	}
	.pills {
		list-style: none;
		padding: 0;
		display: flex;
		flex-wrap: wrap;
		gap: 0.35rem;
	}
	.pill {
		border: 1px solid currentColor;
		border-radius: 999px;
		padding: 0.1rem 0.6rem;
		font-size: 0.8rem;
	}
	.pill .src {
		color: var(--color-muted);
		font-size: 0.7rem;
	}
	.pill.manual {
		border-style: dashed;
	}
	.synergy {
		list-style: none;
		padding: 0;
		font-size: 0.9rem;
	}
	.synergy li {
		margin-bottom: 0.4rem;
	}
	.weight {
		font-family: var(--font-mono);
		font-size: 0.75rem;
		margin: 0 0.4rem;
	}
	.dir {
		color: var(--color-muted);
		font-size: 0.8rem;
	}
	.reason {
		margin: 0.1rem 0 0;
		color: var(--color-muted);
		font-size: 0.8rem;
	}
	.printing-switch select {
		max-width: 100%;
	}
	.notes-form {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		max-width: 28rem;
	}
</style>
