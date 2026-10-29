<script lang="ts">
	// Staged-list qty cell: a number input with a per-row Save. Save always keeps its space
	// (opacity-free hide via visibility so it stays out of the tab order until needed — designer §2.10
	// asks for reachable-on-focus, so it is hidden visually but focusable via :focus-within).
	import { enhance } from '$app/forms';

	interface Props {
		oracleId: string;
		board: string;
		cardName: string;
		boardLabel: string;
		qty: number;
		onsaved?: (message: string) => void;
	}

	let { oracleId, board, cardName, boardLabel, qty, onsaved }: Props = $props();

	const uid = $props.id();
	let value = $state<number | string>(0);
	$effect(() => {
		value = qty;
	});
	const dirty = $derived(Number(value) !== qty);
	const valid = $derived(Number.isInteger(Number(value)) && Number(value) >= 1);
</script>

<form
	method="POST"
	action="?/upsert_card"
	class="qty"
	class:is-dirty={dirty}
	use:enhance={() => {
		return async ({ result, update }) => {
			await update({ reset: false });
			if (result.type === 'success') onsaved?.(`Set ${cardName} on ${boardLabel} to ${value}`);
		};
	}}
>
	<input type="hidden" name="oracle_id" value={oracleId} />
	<input type="hidden" name="name" value={cardName} />
	<input type="hidden" name="board" value={board} />
	<button type="submit" class="btn-save" disabled={!dirty || !valid} aria-label={`Save quantity for ${cardName} on ${boardLabel}`} tabindex={dirty ? 0 : -1}>Save</button>
	<label for="{uid}-qty" class="visually-hidden">Quantity of {cardName} on {boardLabel}</label>
	<input id="{uid}-qty" name="qty" type="number" min="1" step="1" inputmode="numeric" bind:value />
</form>

<style>
	.qty { display: inline-flex; align-items: center; justify-content: flex-end; gap: var(--space-2); }
	.qty input[name='qty'] { width: 4.5rem; text-align: right; font-family: var(--font-mono); }
	.is-dirty input[name='qty'] { border-color: var(--color-accent); }
	.btn-save { min-width: var(--touch-min); visibility: hidden; padding-inline: var(--space-3); }
	.qty.is-dirty .btn-save, .qty:focus-within .btn-save { visibility: visible; }
	.btn-save:disabled { visibility: hidden; }
	.qty:focus-within .btn-save:disabled { visibility: visible; }
</style>
