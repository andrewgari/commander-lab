<script lang="ts">
	// Read-only inline status: text label + icon shape + colour (colour is the third channel).
	// The visible label is the accessible name; the icon is decorative. No role, no tabindex.
	import Icon from '#lib/components/Icon.svelte';
	import { STATUS_CATALOG, type IconName, type StatusKey } from '#lib/status.ts';

	interface Props {
		status: StatusKey;
		label?: string; // defaults to the catalog label; pass the raw value for fallback() statuses
		icon?: IconName | null; // null removes the icon
		variant?: 'solid' | 'text';
		size?: 'sm' | 'md';
		title?: string; // never the sole carrier of information
		describedby?: string;
		class?: string; // layout only (margin)
	}

	let {
		status,
		label,
		icon,
		variant,
		size = 'sm',
		title,
		describedby,
		class: className
	}: Props = $props();

	const entry = $derived(STATUS_CATALOG[status]);
	const shownLabel = $derived(label ?? entry.label);
	const shownIcon = $derived(icon === undefined ? entry.icon : icon);
	const shownVariant = $derived(variant ?? entry.variant);
</script>

<span
	class={['pill', `pill--${entry.role}`, `pill--${shownVariant}`, `pill--${size}`, className]}
	data-status={status}
	{title}
	aria-describedby={describedby}
>
	{#if shownIcon}<Icon name={shownIcon} />{/if}
	<span class="pill__label">{shownLabel}</span>
</span>

<style>
	.pill {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
		padding: 0.125rem var(--space-2);
		min-height: 1.5rem;
		border: 1px solid transparent;
		border-radius: var(--radius-sm);
		font-family: var(--font-mono);
		font-size: var(--text-xs);
		font-weight: var(--weight-bold);
		line-height: 1.3;
		letter-spacing: var(--tracking-label);
		text-transform: uppercase; /* CSS only: the DOM text stays Title case */
		white-space: normal; /* wraps at 320px / 200% zoom, never truncates */
		overflow-wrap: anywhere;
		vertical-align: middle;
	}
	.pill--md {
		min-height: 1.75rem;
		font-size: var(--text-sm);
	}
	.pill__label {
		min-width: 0;
	}

	/* role -> tokens (README D10) */
	.pill--success { --pill-fg: var(--color-success); --pill-fill: var(--color-success-solid); }
	.pill--warning { --pill-fg: var(--color-warning); --pill-fill: var(--color-warning-solid); }
	.pill--danger { --pill-fg: var(--color-danger); --pill-fill: var(--color-danger-solid); }
	.pill--info { --pill-fg: var(--color-info); --pill-fill: var(--color-info-bg); }
	.pill--neutral { --pill-fg: var(--color-muted); --pill-fill: var(--color-surface-raised); }

	/* text: coloured label on the page, decorative hairline (state is carried by text + icon) */
	.pill--text {
		background: transparent;
		border-color: var(--color-border);
		color: var(--pill-fg);
	}
	/* solid: saturated fill, on-solid label; the fill is the edge */
	.pill--solid {
		background: var(--pill-fill);
		color: var(--color-on-solid);
	}

	@media (forced-colors: active) {
		.pill {
			border: 1px solid CanvasText;
			background: Canvas;
			color: CanvasText;
		}
	}
</style>
