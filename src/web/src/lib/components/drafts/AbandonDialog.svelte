<script lang="ts">
	// Abandon confirmation: alertdialog with the consequence sentence (page-specs §2.6).
	import { enhance } from '$app/forms';
	import ConfirmDialog from '#lib/components/ConfirmDialog.svelte';

	let { sentence, draftName }: { sentence: string; draftName: string } = $props();

	let dlg: ConfirmDialog;
	let submitBtn: HTMLButtonElement;
	let submitting = $state(false);
	let failure = $state<string | null>(null);

	export function show(from?: HTMLElement | null) {
		failure = null;
		dlg.show(from);
	}
</script>

<ConfirmDialog bind:this={dlg} title="Abandon draft?" eyebrow={`Draft · ${draftName}`} variant="abandon" role="alertdialog" pending={submitting}>
	{#snippet children()}
		<p class="consequence">{sentence}</p>
	{/snippet}
	{#snippet alert()}
		{#if failure}<div class="banner" role="alert"><strong>Abandon failed</strong> <span>{failure}</span></div>{/if}
	{/snippet}
	{#snippet actions()}
		<form
			method="POST"
			action="?/abandon"
			use:enhance={() => {
				submitting = true;
				failure = null;
				return async ({ result, update }) => {
					if (result.type === 'failure') {
						failure = (result.data as { actionError?: string } | undefined)?.actionError ?? 'The server rejected the request.';
					} else if (result.type === 'error') {
						failure = 'Network or server error. Try again.';
					} else {
						await update(); // redirect -> /drafts
					}
					submitting = false;
					if (failure) submitBtn?.focus();
				};
			}}
		>
			<input type="hidden" name="confirmed" value="1" />
			<button type="submit" class="btn-danger" bind:this={submitBtn} aria-disabled={submitting ? 'true' : undefined} onclick={(e) => { if (submitting) e.preventDefault(); }}>{submitting ? 'Abandoning…' : 'Abandon draft'}</button>
			<span class="visually-hidden" role="status">{submitting ? 'Abandoning the draft' : ''}</span>
		</form>
	{/snippet}
</ConfirmDialog>

<style>
	.consequence { margin: 0; color: var(--color-text); font-size: var(--text-md); }
	.banner { background: var(--color-danger-bg); border-left: 3px solid var(--color-danger); padding: var(--space-2) var(--space-3); color: var(--color-text); overflow-wrap: anywhere; }
	.banner strong { color: var(--color-danger); }
	.btn-danger { background: var(--color-danger-solid); color: var(--color-on-solid); border-color: var(--color-danger-solid); font-weight: var(--weight-medium); }
	.btn-danger[aria-disabled='true'] { cursor: progress; }
</style>
