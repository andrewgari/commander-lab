<script lang="ts">
	// Shared confirm dialog on native <dialog> + showModal() (drafts page-specs §2.6).
	// - showModal() makes the page inert, puts the dialog in the top layer and draws ::backdrop.
	// - Initial focus is Cancel (the actions are irreversible); focus returns to the opener on close.
	// - Esc closes (native `cancel`), except while `pending`; a backdrop click never closes.
	import type { Snippet } from 'svelte';

	interface Props {
		/** Heading text; becomes the dialog's accessible name. */
		title: string;
		eyebrow?: string;
		variant?: 'commit' | 'abandon';
		/** `alertdialog` for destructive confirms. */
		role?: 'dialog' | 'alertdialog';
		/** A request is running: Cancel and Esc are suppressed. */
		pending?: boolean;
		/** Body content (the dialog's accessible description). */
		children: Snippet;
		/** The submit control(s) shown after Cancel (usually a <form> with the submit button). */
		actions: Snippet;
		/** Error strip between body and foot (stays visible however far the body is scrolled). */
		alert?: Snippet;
		onclose?: () => void;
	}

	let { title, eyebrow, variant = 'commit', role = 'dialog', pending = false, children, actions, alert, onclose }: Props = $props();

	const uid = $props.id();
	const titleId = `${uid}-title`;
	/** The whole body is the dialog's accessible description (aria-describedby). */
	const descId = `${uid}-desc`;

	let dialog: HTMLDialogElement;
	let cancelBtn: HTMLButtonElement;
	let opener: HTMLElement | null = null;

	export function show(from?: HTMLElement | null) {
		opener = from ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null);
		if (!dialog.open) dialog.showModal();
		cancelBtn.focus();
	}

	export function close() {
		if (dialog?.open) dialog.close();
	}

	export function isOpen() {
		return Boolean(dialog?.open);
	}

	function onCancelEvent(e: Event) {
		// Esc: ignore while a request is running so the dialog cannot vanish mid-transaction.
		if (pending) e.preventDefault();
	}

	function onCloseEvent() {
		const target = opener;
		opener = null;
		// Restore focus ourselves; browsers differ on whether they do it after showModal().
		if (target?.isConnected) target.focus();
		onclose?.();
	}
</script>

<dialog
	bind:this={dialog}
	class="dlg dlg--{variant}"
	{role}
	aria-labelledby={titleId}
	aria-describedby={descId}
	oncancel={onCancelEvent}
	onclose={onCloseEvent}
>
	<header class="dlg__head">
		{#if eyebrow}<p class="dlg__eyebrow">{eyebrow}</p>{/if}
		<h2 id={titleId}>{title}</h2>
	</header>
	<!-- svelte-ignore a11y_no_noninteractive_tabindex: the body scrolls on small screens and must be keyboard-reachable -->
	<div class="dlg__body" id={descId} role="region" aria-labelledby={titleId} tabindex="0">{@render children()}</div>
	{#if alert}<div class="dlg__alert">{@render alert()}</div>{/if}
	<footer class="dlg__foot">
		<button type="button" class="dlg__cancel" bind:this={cancelBtn} aria-disabled={pending ? 'true' : undefined} onclick={() => { if (!pending) dialog.close(); }}>Cancel</button>
		{@render actions()}
	</footer>
</dialog>

<style>
	.dlg {
		width: min(var(--dialog-max, 40rem), calc(100vw - 2 * var(--space-4)));
		max-height: calc(100dvh - 2 * var(--space-6));
		padding: 0; margin: auto;
		flex-direction: column;
		background: var(--color-surface); color: var(--color-text-body);
		border: 1px solid var(--color-border-control); border-top: 4px solid var(--color-accent);
		border-radius: var(--radius-sm);
	}
	.dlg[open] { display: flex; animation: dlg-in var(--dur-base) var(--ease-out); }
	.dlg--abandon { border-top-color: var(--color-danger); }
	.dlg::backdrop { background: var(--color-backdrop); }
	@keyframes dlg-in { from { opacity: 0; transform: translateY(12px) scale(0.98); } }
	@media (prefers-reduced-motion: reduce) { .dlg[open] { animation: none; } }
	.dlg__head { padding: var(--space-4) var(--space-5) var(--space-3); border-bottom: 1px solid var(--color-border); }
	.dlg__head h2 { margin: 0; font-size: var(--text-xl); line-height: var(--leading-xl); }
	.dlg__eyebrow { margin: 0 0 var(--space-1); font-family: var(--font-mono); font-size: var(--text-xs); text-transform: uppercase; letter-spacing: var(--tracking-label); color: var(--color-muted); overflow-wrap: anywhere; }
	.dlg__body { flex: 1 1 auto; overflow-y: auto; overscroll-behavior: contain; padding: var(--space-4) var(--space-5); }
	.dlg__body:focus-visible { outline: 2px solid var(--color-accent); outline-offset: -2px; }
	.dlg__alert { padding: 0 var(--space-5) var(--space-3); }
	.dlg__foot { display: flex; justify-content: flex-end; gap: var(--space-3); padding: var(--space-3) var(--space-5); background: var(--color-bg); border-top: 1px solid var(--color-border); flex-wrap: wrap; }
	.dlg__foot :global(form) { display: contents; }
	.dlg__cancel[aria-disabled='true'] { color: var(--color-muted); border-color: var(--color-border); border-style: dashed; cursor: progress; }
	@media (max-width: 39.99rem) {
		.dlg { margin: auto 0 0; width: 100vw; max-width: 100vw; max-height: 92dvh; border-left: 0; border-right: 0; border-bottom: 0; }
		.dlg__foot { flex-direction: column-reverse; }
		.dlg__foot :global(button) { width: 100%; justify-content: center; }
	}
</style>
