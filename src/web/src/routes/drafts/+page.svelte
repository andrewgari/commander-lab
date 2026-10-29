<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import type { ActionData, PageProps } from './$types';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import ErrorBanner from '#lib/components/ErrorBanner.svelte';
	import StatusPill from '#lib/components/StatusPill.svelte';
	import SubmitButton from '#lib/components/SubmitButton.svelte';
	import { createPending } from '#lib/forms/pending.svelte.ts';
	import { draftStatus } from '#lib/status.ts';

	let { data, form }: PageProps & { form: ActionData } = $props();

	const p = createPending();
	let formEl = $state<HTMLFormElement>();
</script>

<svelte:head>
	<title>Drafts — Commander Lab</title>
</svelte:head>

<nav><a href="/">← home</a></nav>
<h1>Draft mode</h1>
{#if data.error}
	<ErrorBanner message="Draft data unavailable" detail={data.error} onRetry={() => invalidateAll()} retryLabel="Reload drafts" />
{/if}
<p class="meta">
	Combine decks or dismantle one to build another. Everything stays in staging — nothing
	touches your decks or collection until you press <strong>Commit</strong>. Abandoning a
	draft leaves zero residue.
</p>

<section>
	<h2>Start a draft</h2>
	{#if data.decks.length < 1 && !data.error}
		<EmptyState variant="inline" title="No decks to draft from" description="Import a deck first." ctaLabel="Go to Decks" ctaHref="/decks" />
	{:else}
		<form method="POST" use:enhance={p.enhance} bind:this={formEl} aria-busy={p.submitting}>
			<fieldset class="fields" disabled={p.submitting}>
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
			</fieldset>
			<SubmitButton pending={p.submitting} pendingLabel="Opening draft…" variant="primary">Open draft</SubmitButton>
			{#if p.transientError}
				<ErrorBanner message="Couldn't create draft" detail={p.transientError} onRetry={() => formEl?.requestSubmit()} />
			{:else if form?.createError}
				<ErrorBanner message="Couldn't create draft" detail={form.createError} dismissible />
			{/if}
		</form>
	{/if}
</section>

<section>
	<h2>Draft sessions</h2>
	{#if data.error}
		<!-- the page-level banner above carries the error -->
	{:else if data.drafts.length === 0}
		<EmptyState variant="inline" title="No drafts yet" description="Pick source decks above and open one." />
	{:else}
		<ul class="draft-list">
			{#each data.drafts as draft (draft.id)}
				<li>
					<a href="/drafts/{draft.id}">{draft.name}</a>
					<span class="meta">
						{draft.kind} → {draft.target_name} ·
						<StatusPill status={draftStatus(draft.status)} label={draftStatus(draft.status) === 'neutral' ? draft.status : undefined} />
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
	.fields {
		display: grid;
		gap: 0.5rem;
		border: 0;
		padding: 0;
		margin: 0;
		min-width: 0;
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
</style>
