<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '#lib/components/Icon.svelte';
	import type { IconName } from '#lib/icons.ts';

	type Severity = 'error' | 'warning' | 'info';

	interface Props {
		message: string; // says what failed: "Deck data unavailable"
		detail?: string; // always rendered as text, never HTML
		detailCollapsed?: boolean;
		severity?: Severity;
		onRetry?: () => void | Promise<void>;
		retryLabel?: string;
		retrying?: boolean; // controlled; an async onRetry also tracks it internally
		dismissible?: boolean;
		onDismiss?: () => void;
		open?: boolean;
		actions?: Snippet;
		children?: Snippet; // replaces `detail`
		class?: string; // margins only
	}

	let {
		message,
		detail,
		detailCollapsed = false,
		severity = 'error',
		onRetry,
		retryLabel = 'Try again',
		retrying = false,
		dismissible = false,
		onDismiss,
		open = $bindable(true),
		actions,
		children,
		class: className
	}: Props = $props();

	const LONG_DETAIL = 120;
	const SEVERITIES: Record<Severity, { family: string; word: string; icon: IconName; role: 'alert' | 'status' }> = {
		error: { family: 'danger', word: 'Error', icon: 'error', role: 'alert' },
		warning: { family: 'warning', word: 'Warning', icon: 'alert', role: 'status' },
		info: { family: 'info', word: 'Notice', icon: 'info', role: 'status' }
	};

	const sev = $derived(SEVERITIES[severity]);
	const collapsed = $derived(detailCollapsed || (detail?.length ?? 0) > LONG_DETAIL);

	// Exit: opacity/transform on the container only, <= --dur-base (200ms), and only when motion is allowed.
	function exit(_node?: Element) {
		const motion = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: no-preference)').matches;
		return {
			duration: motion ? 150 : 0,
			css: (t: number) => `opacity: ${t}; transform: translateY(${(1 - t) * -0.5}rem)`
		};
	}

	let pending = $state(false);
	const busy = $derived(retrying || pending);

	async function handleRetry() {
		if (busy || !onRetry) return;
		try {
			const result = onRetry();
			if (result && typeof (result as Promise<void>).then === 'function') {
				pending = true;
				await result;
			}
		} catch {
			// The caller owns error reporting; a rejection must not escape as unhandled.
		} finally {
			pending = false;
		}
	}

	// Focus restore: remember where focus came from when it enters the banner.
	let banner = $state<HTMLElement>();
	let cameFrom: Element | null = null;

	function onFocusIn(e: FocusEvent) {
		const from = e.relatedTarget;
		// Moves between controls inside the banner must not overwrite the real origin.
		if (from instanceof Element && !banner?.contains(from)) cameFrom = from;
		else if (!from) cameFrom = null;
	}

	function dismiss() {
		const restore = cameFrom;
		const hadFocus = !!banner?.contains(document.activeElement);
		open = false;
		onDismiss?.();
		if (!hadFocus) return;
		// The banner (and the focused control in it) is removed from the DOM, so move focus first.
		const target =
			restore instanceof HTMLElement && restore.isConnected ? restore : document.getElementById('main');
		target?.focus();
	}

	function onKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape' && dismissible && !e.defaultPrevented) {
			e.preventDefault();
			dismiss();
		}
	}
</script>

{#if open}
	<!-- svelte-ignore a11y_no_noninteractive_element_interactions: Esc dismisses from anywhere inside the live region -->
	<div
		bind:this={banner}
		class={['banner', `banner--${sev.family}`, className]}
		role={sev.role}
		aria-atomic="true"
		onfocusin={onFocusIn}
		onkeydown={onKeydown}
		out:exit
	>
		<span class="banner__icon"><Icon name={sev.icon} size="1.25em" /></span>
		<div class="banner__body">
			<p class="banner__msg"><strong class="banner__sev">{sev.word}:</strong> {message}</p>
			{#if children}
				<div class="banner__detail">{@render children()}</div>
			{:else if detail}
				{#if collapsed}
					<details class="banner__detail">
						<summary>Details</summary>
						<p>{detail}</p>
					</details>
				{:else}
					<p class="banner__detail">{detail}</p>
				{/if}
			{/if}
		</div>
		{#if onRetry || actions || dismissible}
			<div class="banner__actions">
				{#if onRetry}
					<button type="button" class="btn btn--secondary" aria-disabled={busy ? 'true' : undefined} onclick={handleRetry}>
						{#if busy}
							<Icon name="spinner" /><span class="banner__dots" aria-hidden="true">…</span>
							Retrying…
						{:else}
							{retryLabel}
						{/if}
					</button>
				{/if}
				{@render actions?.()}
				{#if dismissible}
					<button type="button" class="banner__close" aria-label="Dismiss {sev.word.toLowerCase()} message" onclick={dismiss}>
						<Icon name="close" size="1.25em" />
					</button>
				{/if}
			</div>
		{/if}
	</div>
{/if}

<style>
	.banner {
		--banner-fam: var(--color-danger);
		--banner-bg: var(--color-danger-bg);
		display: flex;
		flex-wrap: wrap;
		align-items: flex-start;
		gap: var(--space-3) var(--space-4);
		padding: var(--space-3) var(--space-4);
		background: var(--banner-bg);
		border-left: 4px solid var(--banner-fam);
		border-radius: var(--radius-sm);
		color: var(--color-text);
	}
	.banner--warning { --banner-fam: var(--color-warning); --banner-bg: var(--color-warning-bg); }
	.banner--info { --banner-fam: var(--color-info); --banner-bg: var(--color-info-bg); }

	.banner__icon { display: inline-flex; flex: none; padding-top: 0.125em; color: var(--banner-fam); }
	.banner__body { flex: 1 1 14rem; min-width: 0; }
	.banner__msg { margin: 0; color: var(--color-text); }
	.banner__sev { font-weight: var(--weight-bold); }
	.banner__detail { margin: var(--space-1) 0 0; color: var(--color-text-body); overflow-wrap: anywhere; }
	details.banner__detail > p { margin: var(--space-2) 0 0; }
	summary { cursor: pointer; color: var(--color-text); }

	/* At 375px the actions drop below the message and take the full row. */
	.banner__actions {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
		margin-left: auto;
	}
	@media (max-width: 40rem) {
		.banner__actions { flex: 1 1 100%; margin-left: 0; }
	}

	.banner__close {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-width: var(--touch-min);
		padding: var(--space-2);
		background: transparent;
		border-color: transparent;
		color: var(--color-text);
	}
	.banner__close:hover { border-color: var(--banner-fam); }

	.banner__dots { display: none; }
	@media (prefers-reduced-motion: reduce) {
		/* The spinner becomes a static "…". */
		.banner__actions :global(.icon--spin) { display: none; }
		.banner__dots { display: inline; }
	}

	@media (forced-colors: active) {
		.banner { border: 1px solid CanvasText; border-left-width: 4px; }
		.banner__icon { color: CanvasText; }
	}

	@media (prefers-reduced-motion: no-preference) {
		.banner { animation: banner-in var(--dur-base) var(--ease-out); }
		@keyframes banner-in {
			from { opacity: 0; transform: translateY(calc(-1 * var(--space-2))); }
			to { opacity: 1; transform: none; }
		}
	}
</style>
