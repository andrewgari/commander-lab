<script lang="ts">
	// "There is nothing here yet": says what is missing, what to do next, and optionally offers one action.
	// Root is a plain <div> for both variants: an empty state must not add a landmark (README D9).
	import type { Snippet } from 'svelte';
	import Icon from '#lib/components/Icon.svelte';
	import type { IconName } from '#lib/icons.ts';

	interface Props {
		/** Sentence case, no trailing period. */
		title: string;
		/** One sentence. The `children` snippet replaces it for rich content. */
		description?: string;
		variant?: 'page' | 'inline';
		/** Never h1. Defaults to 2 for page and 3 for inline. */
		headingLevel?: 2 | 3 | 4;
		/** Inline only: the title is a <p>, with no heading element. */
		compact?: boolean;
		ctaLabel?: string;
		/** Renders a link; wins over `onCta` when both are set. */
		ctaHref?: string;
		/** Renders a type="button" button. */
		onCta?: () => void;
		/** Defaults to primary for page and secondary for inline. */
		ctaVariant?: 'primary' | 'secondary';
		/** The empty state is the result of a search the user just ran: adds role="status". */
		filtered?: boolean;
		/** Decorative; 48px for page, 24px for inline. */
		icon?: IconName;
		/** Rich content in place of `description`. */
		children?: Snippet;
		/** Replaces the built-in CTA. At most 2 actions. */
		actions?: Snippet;
		/** Margins only. */
		class?: string;
	}

	let {
		title,
		description,
		variant = 'page',
		headingLevel,
		compact = false,
		ctaLabel,
		ctaHref,
		onCta,
		ctaVariant,
		filtered = false,
		icon,
		children,
		actions,
		class: className
	}: Props = $props();

	const uid = $props.id();
	const level = $derived(headingLevel ?? (variant === 'page' ? 2 : 3));
	const tag = $derived(`h${level}` as 'h2' | 'h3' | 'h4');
	const btnVariant = $derived(ctaVariant ?? (variant === 'page' ? 'primary' : 'secondary'));
	const asParagraph = $derived(variant === 'inline' && compact);

	$effect(() => {
		if (import.meta.env.DEV && ctaLabel && !ctaHref && !onCta) {
			console.warn(`EmptyState: ctaLabel "${ctaLabel}" needs ctaHref or onCta, so no action is rendered.`);
		}
	});
</script>

<div class={['empty-state', `empty-state--${variant}`, className]} role={filtered ? 'status' : undefined}>
	{#if icon}
		<span class="empty-state__icon" aria-hidden="true"><Icon name={icon} size={variant === 'page' ? '48px' : '24px'} /></span>
	{/if}
	{#if asParagraph}
		<p class="empty-state__title" id="{uid}-title">{title}</p>
	{:else}
		<svelte:element this={tag} class="empty-state__title" id="{uid}-title">{title}</svelte:element>
	{/if}
	{#if children}
		<div class="empty-state__desc">{@render children()}</div>
	{:else if description}
		<p class="empty-state__desc">{description}</p>
	{/if}
	{#if actions}
		<div class="empty-state__actions">{@render actions()}</div>
	{:else if ctaLabel && ctaHref}
		<div class="empty-state__actions">
			<a class="btn btn--{btnVariant}" href={ctaHref}>{ctaLabel}</a>
		</div>
	{:else if ctaLabel && onCta}
		<div class="empty-state__actions">
			<button type="button" class="btn btn--{btnVariant}" onclick={() => onCta()}>{ctaLabel}</button>
		</div>
	{/if}
</div>

<style>
	.empty-state { display: flex; flex-direction: column; gap: var(--space-3); }
	.empty-state__icon { display: inline-flex; color: var(--color-muted); }
	.empty-state__title { margin: 0; color: var(--color-text); font-weight: var(--weight-bold); }
	.empty-state__desc { margin: 0; color: var(--color-muted); font-size: var(--text-md); line-height: var(--leading-md); }
	.empty-state__actions { display: flex; flex-wrap: wrap; gap: var(--space-3); }

	.empty-state--page {
		align-items: center;
		text-align: center;
		padding: var(--space-8);
		background: var(--color-surface-raised);
		border-radius: var(--radius-md);
	}
	.empty-state--page .empty-state__title { font-size: var(--text-xl); line-height: var(--leading-xl); }
	.empty-state--page .empty-state__desc { max-width: 40ch; }
	.empty-state--page .empty-state__actions { justify-content: center; }

	.empty-state--inline {
		align-items: flex-start;
		text-align: left;
		padding: var(--space-4);
		border: 1px dashed var(--color-border);
		border-radius: var(--radius-sm);
	}
	.empty-state--inline .empty-state__title { font-size: var(--text-md); line-height: var(--leading-md); }
	.empty-state--inline .empty-state__desc { font-size: var(--text-sm); line-height: var(--leading-sm); }

	@media (forced-colors: active) {
		.empty-state--page { border: 1px solid CanvasText; }
	}
</style>
