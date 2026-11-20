<script lang="ts">
	// Commit confirmation: CommitSummary inside ConfirmDialog (page-specs §2.6, D5).
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import ConfirmDialog from '#lib/components/ConfirmDialog.svelte';
	import ErrorBanner from '#lib/components/ErrorBanner.svelte';
	import SubmitButton from '#lib/components/SubmitButton.svelte';
	import CommitSummaryView from './CommitSummary.svelte';
	import { createPending, NETWORK_ERROR_MESSAGE } from '#lib/forms/pending.svelte.ts';
	import { describeCommitFailure, type CommitFailure } from '#lib/drafts/errors.ts';
	import type { CommitSummary } from '#lib/drafts/summary.ts';

	let {
		draftName,
		summary,
		namesMissing = false
	}: { draftName: string; summary: CommitSummary; namesMissing?: boolean } = $props();

	const p = createPending();
	let dlg: ConfirmDialog;
	let form: HTMLFormElement;
	let failure = $state<CommitFailure | null>(null);

	export function show(opener?: HTMLElement | null) {
		failure = null;
		dlg.show(opener);
	}

	function refresh() {
		dlg.close();
		void invalidateAll();
	}

	// Record the failure for the dialog, then let createPending run its lock/update lifecycle.
	const onSubmit: typeof p.enhance = async (input) => {
		failure = null;
		const done = await p.enhance(input);
		if (!done) return;
		return async (opts) => {
			if (opts.result.type === 'failure') {
				const data = opts.result.data as { actionError?: string } | undefined;
				failure = describeCommitFailure(opts.result.status, data?.actionError);
			} else if (opts.result.type === 'error') {
				failure = { message: NETWORK_ERROR_MESSAGE, stale: false };
			}
			await done(opts);
			// Focus stays on Commit after a failure.
			if (failure) form?.querySelector<HTMLButtonElement>('button[type="submit"]')?.focus();
		};
	};
</script>

<ConfirmDialog bind:this={dlg} title="Commit draft?" eyebrow={`Draft · ${draftName}`} variant="commit" role="dialog" pending={p.submitting}>
	{#snippet children()}
		<CommitSummaryView {summary} {namesMissing} />
	{/snippet}
	{#snippet alert()}
		{#if failure}
			<ErrorBanner message="Commit failed" detail={failure.message}>
				{#snippet actions()}
					{#if failure?.stale}
						<button type="button" class="btn btn--secondary" onclick={refresh}>Close and refresh the draft</button>
					{/if}
				{/snippet}
			</ErrorBanner>
		{/if}
	{/snippet}
	{#snippet actions()}
		<form bind:this={form} method="POST" action="?/commit" use:enhance={onSubmit}>
			<input type="hidden" name="confirmed" value="1" />
			<SubmitButton pending={p.submitting} pendingLabel="Committing…" variant="primary">Commit draft</SubmitButton>
		</form>
	{/snippet}
</ConfirmDialog>
