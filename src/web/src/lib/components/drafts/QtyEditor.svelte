<script lang="ts">
	// Staged-list qty cell: a 4.5rem number input with a per-row Save (page-specs §2.4, §2.10).
	// Save always keeps its space (opacity 0, no pointer events) so the row never shifts; it shows
	// when the row is dirty or has focus. Upsert SETS qty, so the exact typed value is posted.
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
	const n = $derived(Number(value));
	const dirty = $derived(value !== '' && n !== qty);
	const valid = $derived(value !== '' && Number.isInteger(n) && n >= 1);
	let invalid = $state(false);
	$effect(() => {
		if (valid) invalid = false;
	});
</script>

<form
	method="POST"
	action="?/upsert_card"
	class="qty"
	class:is-dirty={dirty}
	use:enhance={({ cancel }) => {
		if (!valid) {
			invalid = true;
			cancel();
			return;
		}
		if (!dirty) {
			cancel();
			return;
		}
		const sent = n;
		return async ({ result, update }) => {
			await update({ reset: false });
			if (result.type === 'success') onsaved?.(`Saved ${cardName}: ${sent}`);
		};
	}}
>
	<input type="hidden" name="oracle_id" value={oracleId} />
	<input type="hidden" name="name" value={cardName} />
	<input type="hidden" name="board" value={board} />
	<label for="{uid}-qty" class="visually-hidden">Quantity of {cardName} on {boardLabel}</label>
	<input
		id="{uid}-qty"
		name="qty"
		type="number"
		min="1"
		step="1"
		inputmode="numeric"
		aria-invalid={invalid || undefined}
		bind:value
	/>
	<button type="submit" class="btn-save" aria-label={`Save quantity for ${cardName} on ${boardLabel}`}>Save</button>
</form>

<style>
	.qty {
		display: inline-flex;
		align-items: center;
		justify-content: flex-end;
		gap: var(--space-2);
	}
	.qty input[name='qty'] {
		width: 4.5rem;
		text-align: right;
		font-family: var(--font-mono);
	}
	.qty input[name='qty'][aria-invalid='true'] {
		border-color: var(--color-danger);
	}
	.is-dirty input[name='qty'] {
		border-color: var(--color-accent);
	}
	.btn-save {
		min-width: var(--touch-min);
		min-height: var(--touch-min);
		padding-inline: var(--space-3);
		/* visibility, not opacity:0 — keeps the space, drops it from the tab order, and passes the
		   a11y gate's no-opacity-dimmed-element rule */
		visibility: hidden;
	}
	.qty.is-dirty .btn-save,
	.qty:focus-within .btn-save {
		visibility: visible;
	}
</style>
