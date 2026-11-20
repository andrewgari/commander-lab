<script lang="ts">
	import Icon from '#lib/components/Icon.svelte';
	import type { Tile } from '#lib/home/stats.ts';

	let { tile, index = 0 }: { tile: Tile; index?: number } = $props();
	const ACCENT: Record<string, string> = {
		decks: 'var(--color-accent)',
		ready: 'var(--color-success)',
		collection: 'var(--mana-u)',
		games: 'var(--mana-r)',
		drafts: 'var(--mana-b)',
		power: 'var(--color-info)'
	};
	const href = $derived(tile.state === 'empty' && tile.emptyHref ? tile.emptyHref : tile.href);
	// Error tiles still link to the section: the reason is in the title, the banner below has Retry.
</script>

{#snippet face()}
	<span class="label eyebrow">{tile.label}<span class="visually-hidden">: </span></span>
	{#if tile.state === 'ok'}
		<span class="value" title={tile.valueTitle}>{tile.value}{#if tile.denominator}<small>{tile.denominator}</small>{/if}<span class="visually-hidden">, </span></span>
		<span class="chunks">
			{#each tile.chunks as c, i (i)}
				{#if tile.sublink && i === 0}
					<span class="sub-spacer" aria-hidden="true">{c.text}</span>
				{:else}
					<span>{#if i > 0}<span class="visually-hidden">, </span>{/if}{c.text}</span>{' '}
				{/if}
			{/each}
		</span>
	{:else if tile.state === 'empty'}
		<span class="value words">{tile.emptyText}</span>
		<span class="cta">{tile.emptyCta}</span>
	{:else}
		<span class="value words"><Icon name="error" size="1.1em" /> Couldn't load</span>
		<span class="chunks"><span>{tile.errorSource} · see banner below</span></span>
	{/if}
{/snippet}

<li style:--i={index} style:--accent={ACCENT[tile.key]}>
	{#if tile.sublink && tile.state === 'ok'}
		<div class="tile tile--ok tile--two" title={undefined}>
			<a class="cover" href={tile.href}><span class="visually-hidden">{tile.label}: {tile.value}</span></a>
			{@render face()}
			<a class="sublink" href={tile.sublink.href} title={tile.sublink.text}>{tile.sublink.text}</a>
		</div>
	{:else}
		<a class="tile tile--{tile.state}" {href} title={tile.state === 'error' ? tile.errorReason : undefined}>
			{@render face()}
		</a>
	{/if}
</li>

<style>
	li { list-style: none; min-width: 0; animation: var(--tile-anim, none); animation-delay: calc(var(--i) * 40ms); }
	.tile {
		position: relative; display: grid; grid-template-rows: auto 1fr calc(2 * var(--leading-xs) * var(--text-xs));
		gap: var(--space-1); height: 100%; min-height: 9rem; padding: var(--space-4);
		background: var(--color-surface); border: 1px solid var(--color-border); border-top: 3px solid var(--accent);
		border-radius: var(--radius-sm); color: var(--color-text); text-decoration: none;
		transition: transform var(--dur-fast) var(--ease-out), background var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out);
	}
	a.tile:hover, .tile--two:hover { background: var(--color-surface-raised); border-top-color: var(--color-accent); transform: translateY(-2px); }
	a.tile:active { transform: scale(0.985); }
	a.tile:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 2px; }
	.tile--two:has(> .cover:focus-visible) { outline: 2px solid var(--color-focus); outline-offset: 2px; }
	.tile--empty { background: transparent; border-style: dashed; border-top-style: solid; border-top-color: var(--color-border-control); }
	.tile--error { background: var(--color-danger-bg); border-top-color: var(--color-danger); }
	.eyebrow { font-family: var(--font-mono); font-size: var(--text-xs); text-transform: uppercase; letter-spacing: var(--tracking-label); color: var(--color-muted); }
	.value { align-self: end; font-size: var(--text-2xl); line-height: var(--leading-2xl); font-weight: var(--weight-bold); letter-spacing: -0.02em; font-variant-numeric: tabular-nums; color: var(--color-text); }
	.value small { font-size: var(--text-lg); font-weight: var(--weight-regular); color: var(--color-muted); }
	.value.words { font-size: var(--text-md); font-weight: var(--weight-medium); letter-spacing: 0; line-height: var(--leading-md); }
	.chunks, .cta { font-size: var(--text-xs); line-height: var(--leading-xs); color: var(--color-text-body); display: block; max-height: calc(2 * var(--leading-xs) * var(--text-xs)); overflow: hidden; }
	.chunks > span { white-space: nowrap; }
	.chunks > span:not(:last-child)::after { content: ' ·'; }
	.cta { color: var(--color-accent); }
	.cover { position: absolute; inset: 0; border-radius: inherit; }
	.tile--two > :not(.cover):not(.sublink) { pointer-events: none; }
	.sub-spacer { visibility: hidden; }
	.sublink {
		position: absolute; left: var(--space-4); right: var(--space-4); bottom: var(--space-3); z-index: 1;
		font-size: var(--text-xs); line-height: var(--leading-xs); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
		display: block; min-height: 1.5rem; line-height: 1.5rem;
	}
	@media (prefers-reduced-motion: reduce) {
		a.tile:hover, .tile--two:hover, a.tile:active { transform: none; }
	}
	@media (forced-colors: active) { .tile { border-color: CanvasText; } }
</style>
