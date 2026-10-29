<script lang="ts">
	// Commit confirmation: summary + native <dialog> (page-specs §2.6).
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import ConfirmDialog from '#lib/components/ConfirmDialog.svelte';
	import CommitSummary from './CommitSummary.svelte';
	import { describeCommitFailure } from '#lib/drafts/errors.ts';
	import type { CommitSummary as Summary } from '#lib/drafts/summary.ts';

	let { summary, draftName, namesMissing = false }: { summary: Summary; draftName: string; namesMissing?: boolean } = $props();

	let dlg: ConfirmDialog;
	let submitBtn: HTMLButtonElement;
	let submitting = $state(false);
	let failure = $state<{ message: string; stale: boolean } | null>(null);

	export function show(from?: HTMLElement | null) {
		failure = null;
		dlg.show(from);
	}

	function refresh() {
		dlg.close();
		void invalidateAll();
	}
</script>

<ConfirmDialog bind:this={dlg} title="Commit draft?" eyebrow={`Draft · ${draftName}`} variant="commit" pending={submitting}>
	{#snippet children()}
		<CommitSummary {summary} {namesMissing} />
	{/snippet}
	{#snippet alert()}
		{#if failure}
			<div class="banner" role="alert">
				<strong>Commit failed</strong>
				<span>{failure.message}</span>
				{#if failure.stale}<button type="button" class="btn-sm" onclick={refresh}>Close and refresh the draft</button>{/if}
			</div>
		{/if}
	{/snippet}
	{#snippet actions()}
		<form
			method="POST"
			action="?/commit"
			use:enhance={() => {
				submitting = true;
				failure = null;
				return async ({ result, update }) => {
					if (result.type === 'failure') {
						const data = result.data as { actionError?: string } | undefined;
						failure = describeCommitFailure(result.status, data?.actionError);
					} else if (result.type === 'error') {
						failure = { message: 'Network or server error. Your staging is untouched; try again.', stale: false };
					} else {
						await update(); // redirect -> /decks/{id}?from_draft=1
					}
					submitting = false;
					// Focus stays on Commit on failure.
					if (failure) submitBtn?.focus();
				};
			}}
		>
			<input type="hidden" name="confirmed" value="1" />
			<button
				type="submit"
				class="btn-primary"
				bind:this={submitBtn}
				aria-disabled={submitting ? 'true' : undefined}
				onclick={(e) => { if (submitting) e.preventDefault(); }}
			>{#if submitting}<span class="spin" aria-hidden="true"></span> Committing…{:else}Commit draft{/if}</button>
			<span class="visually-hidden" role="status">{submitting ? 'Committing the draft' : ''}</span>
		</form>
	{/snippet}
</ConfirmDialog>

<style>
	.banner { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2) var(--space-3); background: var(--color-danger-bg); border-left: 3px solid var(--color-danger); padding: var(--space-2) var(--space-3); color: var(--color-text); overflow-wrap: anywhere; }
	.banner strong { color: var(--color-danger); }
	.btn-sm { min-height: var(--touch-min); }
	.btn-primary { background: var(--color-accent); color: var(--color-on-accent); border-color: var(--color-accent); font-weight: var(--weight-medium); display: inline-flex; align-items: center; gap: var(--space-2); justify-content: center; }
	.btn-primary:hover { background: var(--color-accent-hover); border-color: var(--color-accent-hover); }
	.btn-primary[aria-disabled='true'] { cursor: progress; }
	.spin { display: inline-block; width: 1em; height: 1em; border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%; animation: spin 0.8s linear infinite; }
	@keyframes spin { to { transform: rotate(360deg); } }
	@media (prefers-reduced-motion: reduce) { .spin { animation: none; border-style: dotted; border-right-color: currentColor; } }
</style>
