<script lang="ts">
	// Abandon confirmation: alertdialog with the consequence sentence (page-specs §2.6, D5).
	import { enhance } from '$app/forms';
	import ConfirmDialog from '#lib/components/ConfirmDialog.svelte';
	import ErrorBanner from '#lib/components/ErrorBanner.svelte';
	import SubmitButton from '#lib/components/SubmitButton.svelte';
	import { createPending, NETWORK_ERROR_MESSAGE } from '#lib/forms/pending.svelte.ts';
	import { abandonSentence } from '#lib/drafts/summary.ts';

	let {
		draftName,
		cards,
		moves
	}: { draftName: string; cards: readonly { qty: number }[]; moves: readonly unknown[] } = $props();

	const p = createPending();
	let dlg: ConfirmDialog;
	let form: HTMLFormElement;
	let failure = $state<string | null>(null);

	export function show(opener?: HTMLElement | null) {
		failure = null;
		dlg.show(opener);
	}

	const onSubmit: typeof p.enhance = async (input) => {
		failure = null;
		const done = await p.enhance(input);
		if (!done) return;
		return async (opts) => {
			if (opts.result.type === 'failure') {
				const data = opts.result.data as { actionError?: string } | undefined;
				failure = data?.actionError?.trim() || 'The server rejected the request.';
			} else if (opts.result.type === 'error') {
				failure = NETWORK_ERROR_MESSAGE;
			}
			await done(opts);
			if (failure) form?.querySelector<HTMLButtonElement>('button[type="submit"]')?.focus();
		};
	};
</script>

<ConfirmDialog bind:this={dlg} title="Abandon draft?" eyebrow={`Draft · ${draftName}`} variant="abandon" role="alertdialog" pending={p.submitting}>
	{#snippet children()}
		<p class="consequence">{abandonSentence(cards, moves)}</p>
	{/snippet}
	{#snippet alert()}
		{#if failure}<ErrorBanner message="Abandon failed" detail={failure} />{/if}
	{/snippet}
	{#snippet actions()}
		<form bind:this={form} method="POST" action="?/abandon" use:enhance={onSubmit}>
			<input type="hidden" name="confirmed" value="1" />
			<SubmitButton pending={p.submitting} pendingLabel="Abandoning…" variant="danger">Abandon draft</SubmitButton>
		</form>
	{/snippet}
</ConfirmDialog>

<style>
	.consequence { margin: 0; color: var(--color-text); font-size: var(--text-md); }
</style>
