<script lang="ts">
	import { goto } from '$app/navigation';
	import { printingHref } from '#lib/card-printing.ts';
	import type { PageProps } from './$types';

	let { data, form }: PageProps = $props();
	const dossier = $derived(data.dossier);
	const card = $derived(dossier.card);
	const selected = $derived(dossier.printing);
	const art = $derived(
		selected?.image_uri_normal || card.image_uri || ''
	);
	let selectedId = $state(data.dossier.printing?.scryfall_id ?? '');

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
			<p class="empty">no card art yet</p>
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
				<p class="empty">no oracle text on record</p>
			{/if}
			{#if card.keywords.length}
				<p class="meta">Keywords: {card.keywords.join(', ')}</p>
			{/if}
		</div>
	</section>

	<section>
		<h2>Printings ({dossier.printings.length})</h2>
		{#if dossier.printings.length === 0}
			<p class="empty">no printings data yet</p>
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
			<p class="empty">no tags yet</p>
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
		{#if dossier.decks.length === 0}
			<p class="empty">not in any deck</p>
		{:else}
			<div class="table-scroll">
			<table>
				<caption class="visually-hidden">Decks containing this card</caption>
				<thead>
					<tr><th scope="col">Deck</th><th scope="col">Status</th><th scope="col">Board</th><th scope="col">Category</th><th scope="col">Qty</th></tr>
				</thead>
				<tbody>
					{#each dossier.decks as d (d.deck_id + d.board)}
						<tr>
							<td><a href="/decks/{encodeURIComponent(d.deck_id)}">{d.name}</a></td>
							<td>{d.status}</td>
							<td>{d.board}</td>
							<td>{d.category ?? '—'}</td>
							<td>{d.qty}</td>
						</tr>
					{/each}
				</tbody>
			</table>
			</div>
		{/if}
	</section>

	<section>
		<h2>Synergy neighbors ({dossier.synergy.length})</h2>
		{#if dossier.synergy.length === 0}
			<p class="empty">no synergy data yet — run an interaction rebuild</p>
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
			<p class="empty">not owned</p>
		{/if}
	</section>

	<section>
		<h2>Affinity &amp; notes</h2>
		{#if form?.actionError}<p class="error" role="alert">{form.actionError}</p>{/if}
		{#if form?.savedAffinity}<p class="saved">Saved.</p>{/if}
		<form method="POST" action="?/save_notes" class="notes-form">
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
			<button type="submit">Save notes</button>
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
		min-height: 44px;
		min-width: 44px;
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
	.table-scroll {
		overflow-x: auto;
		-webkit-overflow-scrolling: touch;
	}
	.table-scroll table {
		min-width: 32rem;
	}
	.synergy li a {
		display: inline-flex;
		align-items: center;
		min-height: 44px;
	}
	.notes-form input,
	.notes-form textarea,
	.notes-form button,
	.printing-switch select {
		min-height: 44px;
		box-sizing: border-box;
	}
	.notes-form textarea {
		min-height: 6rem;
	}
	h1 {
		font-size: 1.5rem;
		margin-bottom: 0.25rem;
	}
	h2 {
		font-size: 1.05rem;
		margin: 1.25rem 0 0.35rem;
	}
	.meta {
		color: var(--text-muted, #404040);
		font-size: 0.85rem;
	}
	.mana {
		font-family: monospace;
		margin-right: 0.35rem;
	}
	.oracle {
		white-space: pre-line;
		font-size: 0.9rem;
	}
	.empty {
		color: var(--text-faint, #666666);
		font-style: italic;
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
		color: var(--text-faint, #666666);
		font-size: 0.7rem;
	}
	.pill.manual {
		border-style: dashed;
	}
	table {
		border-collapse: collapse;
		font-size: 0.85rem;
	}
	th,
	td {
		padding: 0.15rem 0.6rem 0.15rem 0;
		text-align: left;
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
		font-family: monospace;
		font-size: 0.75rem;
		margin: 0 0.4rem;
	}
	.dir {
		color: var(--text-muted, #404040);
		font-size: 0.8rem;
	}
	.reason {
		margin: 0.1rem 0 0;
		color: var(--text-muted, #404040);
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
	.error {
		color: var(--danger, #c0392b);
	}
	.saved {
		color: var(--ok, #1e8449);
	}
</style>
