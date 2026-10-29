<script lang="ts">
	import type { SwapFeedback } from '#lib/api.ts';
	import type { Swap } from '#lib/components/review/types.js';

	type Decision = SwapFeedback['decision'];
	let { documentId, deckId, swap, saved }: { documentId: number; deckId: string; swap: Swap; saved?: SwapFeedback } = $props();

	const options: { value: Decision; label: string }[] = [
		{ value: 'accepted', label: 'Accept' },
		{ value: 'maybe', label: 'Maybe' },
		{ value: 'rejected', label: 'Reject' }
	];
	// svelte-ignore state_referenced_locally -- the parent re-keys per swap/review; the form owns edits after mount
	let decision = $state<Decision | null>(saved?.decision ?? null);
	// svelte-ignore state_referenced_locally
	let reason = $state(saved?.reason ?? '');
	// svelte-ignore state_referenced_locally
	let status = $state<'idle' | 'saving' | 'saved' | 'error'>(saved ? 'saved' : 'idle');
	let message = $state('');

	async function save() {
		if (!decision) return;
		status = 'saving';
		try {
			const res = await fetch(`/decks/${encodeURIComponent(deckId)}/review/feedback`, {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ document_id: documentId, swap_id: swap.id, decision, reason })
			});
			if (!res.ok) throw new Error((await res.json().catch(() => ({})))?.message ?? res.statusText);
			status = 'saved';
			message = '';
		} catch (e) {
			status = 'error';
			message = e instanceof Error ? e.message : 'Could not save';
		}
	}
	function pick(value: Decision) {
		decision = value;
		status = 'idle';
	}
</script>

<form class="swap-feedback" onsubmit={(e) => { e.preventDefault(); save(); }} aria-label={`Your call on ${swap.cut.name} → ${swap.add.name}`}>
	<span class="eyebrow">Your call</span>
	<div class="fb-choices" role="group" aria-label="Decision">
		{#each options as o}
			<button type="button" class="fb-choice" class:active={decision === o.value} data-decision={o.value} aria-pressed={decision === o.value} onclick={() => pick(o.value)}>{o.label}</button>
		{/each}
	</div>
	<input class="fb-reason" type="text" maxlength="500" placeholder="Why? (optional)" aria-label="Reason" bind:value={reason} oninput={() => (status = status === 'saved' ? 'idle' : status)} />
	<button type="submit" class="fb-save" disabled={!decision || status === 'saving'}>{status === 'saving' ? 'Saving…' : 'Save'}</button>
	<span class="fb-status" role="status" data-status={status}>
		{#if status === 'saved'}Saved{:else if status === 'error'}Not saved: {message}{/if}
	</span>
</form>

<style>
	.swap-feedback { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--line, #29323b); }
	.fb-choices { display: inline-flex; gap: 4px; }
	.fb-choice, .fb-save { font: inherit; font-size: 13px; padding: 4px 12px; background: transparent; color: inherit; border: 1px solid var(--line, #29323b); border-radius: 2px; cursor: pointer; }
	.fb-choice.active { background: var(--signal, #f0a63a); color: var(--text-on-signal, #17110a); border-color: var(--signal, #f0a63a); }
	.fb-choice[data-decision='rejected'].active { background: #b4443c; border-color: #b4443c; color: #fff; }
	.fb-reason { flex: 1 1 200px; min-width: 160px; font: inherit; font-size: 13px; padding: 4px 8px; background: transparent; color: inherit; border: 1px solid var(--line, #29323b); border-radius: 2px; }
	.fb-save:disabled { opacity: 0.5; cursor: not-allowed; }
	.fb-status { font-size: 12px; min-width: 4em; }
	.fb-status[data-status='error'] { color: #e5766d; }
	.fb-status[data-status='saved'] { color: var(--ok, #6cbf7a); }
</style>
