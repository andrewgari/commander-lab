<script lang="ts">
	import type { ActionData, PageProps } from './$types';

	let { data, form }: PageProps & { form: ActionData } = $props();
</script>

<svelte:head>
	<title>Drafts — Commander Lab</title>
</svelte:head>

<nav><a href="/">← home</a></nav>
<h1>Draft mode</h1>
<p class="meta">
	Combine decks or dismantle one to build another. Everything stays in staging — nothing
	touches your decks or collection until you press <strong>Commit</strong>. Abandoning a
	draft leaves zero residue.
</p>

<section>
	<h2>Start a draft</h2>
	{#if data.decks.length < 1 && !data.error}
		<p class="meta">No imported decks yet — <a href="/decks">import a deck first</a>.</p>
	{:else}
		<form method="POST">
			<label>
				Kind
				<select name="kind">
					<option value="combine">Combine decks (2+ sources)</option>
					<option value="dismantle">Dismantle one deck to build another</option>
				</select>
			</label>
			<fieldset>
				<legend>Source decks</legend>
				{#each data.decks as deck (deck.id)}
					<label class="check">
						<input type="checkbox" name="source_deck_ids" value={deck.id} />
						{deck.name} <span class="meta">({deck.card_qty} cards, {deck.status})</span>
					</label>
				{/each}
			</fieldset>
			<label>
				Draft name
				<input name="name" required placeholder="e.g. Gruul rebuild" />
			</label>
			<label>
				New deck name
				<input name="target_name" required placeholder="e.g. Gruul Smash" />
			</label>
			<label class="check">
				<input type="checkbox" name="prefill" checked />
				Prefill staging from the source decks' lists (you can edit freely afterwards)
			</label>
			<button type="submit">Open draft</button>
			{#if form?.createError}<p class="error">{form.createError}</p>{/if}
		</form>
	{/if}
</section>

<section>
	<h2>Draft sessions</h2>
	{#if data.error}
		<p class="error">Draft data unavailable: {data.error}</p>
	{:else if data.drafts.length === 0}
		<p>No drafts yet.</p>
	{:else}
		<ul class="draft-list">
			{#each data.drafts as draft (draft.id)}
				<li>
					<a href="/drafts/{draft.id}">{draft.name}</a>
					<span class="meta">
						{draft.kind} → {draft.target_name} ·
						<span class:open={draft.status === 'open'} class:committed={draft.status === 'committed'}>
							{draft.status}
						</span>
						{#if draft.result_deck_id}· built {draft.result_deck_id}{/if}
						· opened {draft.created_at?.slice(0, 10)}
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
	label.check {
		display: flex;
		gap: 0.4rem;
		align-items: center;
	}
	fieldset {
		display: grid;
		gap: 0.25rem;
		font-size: 0.85rem;
	}
	button {
		width: fit-content;
		padding: 0.4rem 1rem;
	}
	.draft-list {
		list-style: none;
		padding: 0;
		display: grid;
		gap: 0.5rem;
	}
	.draft-list li {
		display: flex;
		flex-direction: column;
	}
	.meta {
		font-size: 0.85rem;
		opacity: 0.75;
	}
	.open {
		color: #b9770e;
	}
	.committed {
		color: #1e8449;
	}
	.error {
		color: #c0392b;
	}
</style>
