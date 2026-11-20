<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';
	import Icon from './Icon.svelte';

	type Variant = 'primary' | 'secondary' | 'success' | 'danger' | 'link-danger';

	interface Props extends Omit<HTMLButtonAttributes, 'children' | 'type'> {
		/** Locks the button (pass `p.submitting`). */
		pending: boolean;
		/** Shows the pending label and spinner. Defaults to `pending`; in multi-button forms pass `p.action === 'commit'`. */
		busy?: boolean;
		/** Required so no button silently keeps its idle label. */
		pendingLabel: string;
		/** The idle label. */
		children: Snippet;
		variant?: Variant;
		type?: 'submit' | 'button';
	}

	let {
		pending,
		busy,
		pendingLabel,
		children,
		variant = 'secondary',
		disabled,
		formaction,
		type = 'submit',
		class: className,
		onclick,
		...rest
	}: Props = $props();

	const isBusy = $derived(busy ?? pending);

	// While pending the button stays focusable (aria-disabled, not disabled), so the click has to be
	// stopped here. The createPending lock is the real guard; this covers the native-submit race.
	function guard(event: MouseEvent & { currentTarget: EventTarget & HTMLButtonElement }) {
		if (pending) {
			event.preventDefault();
			return;
		}
		(onclick as ((e: typeof event) => void) | null | undefined)?.(event);
	}
</script>

<button
	{...rest}
	{type}
	class={['btn', `btn--${variant}`, pending && 'is-pending', className]}
	aria-disabled={pending || undefined}
	{disabled}
	{formaction}
	onclick={guard}
>
	<span class="sb__labels">
		<span class:sb__hidden={isBusy} aria-hidden={isBusy || undefined}>{@render children()}</span>
		<span class="sb__pending" class:sb__hidden={!isBusy} aria-hidden={!isBusy}>
			<Icon name="spinner" class="sb__spin" />
			<span class="sb__dots" aria-hidden="true">…</span>
			{pendingLabel}
		</span>
	</span>
</button><span class="visually-hidden" role="status">{isBusy ? pendingLabel : ''}</span>

<style>
	/* Both labels share one grid cell, so the button is as wide as the wider one and never jumps. */
	.sb__labels {
		display: inline-grid;
		align-items: center;
		justify-items: center;
	}
	.sb__labels > span {
		grid-area: 1 / 1;
	}
	.sb__pending {
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
	}
	.sb__hidden {
		visibility: hidden;
	}
	.sb__dots {
		display: none;
	}
	/* Icon.svelte spins at 0.9s and always; here it spins at 600ms, and only while visible. */
	.sb__pending :global(.sb__spin.icon--spin) {
		animation: none;
	}
	@media (prefers-reduced-motion: no-preference) {
		.sb__pending:not(.sb__hidden) :global(.sb__spin.icon--spin) {
			animation: sb-spin 600ms linear infinite;
		}
	}
	/* Reduced motion: no rotation, a static "…" instead of the spinner. */
	@media (prefers-reduced-motion: reduce) {
		.sb__pending :global(.sb__spin) {
			display: none;
		}
		.sb__dots {
			display: inline;
		}
	}
	@keyframes sb-spin {
		to {
			transform: rotate(360deg);
		}
	}
</style>
