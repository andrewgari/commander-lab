<script lang="ts">
	import '../app.css';
	import favicon from '#lib/assets/favicon.svg';
	import { page } from '$app/state';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();

	const nav = [
		{ href: '/', label: 'Home' },
		{ href: '/decks', label: 'Decks' },
		{ href: '/inventory', label: 'Inventory' },
		{ href: '/games', label: 'Games' },
		{ href: '/drafts', label: 'Drafts' },
		{ href: '/profile', label: 'Profile' }
	];
	const isActive = (href: string) =>
		href === '/' ? page.url.pathname === '/' : page.url.pathname === href || page.url.pathname.startsWith(href + '/');
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<a class="skip-link" href="#main">Skip to content</a>
<header class="site-header">
	<a class="brand" href="/">Commander Lab</a>
	<nav aria-label="Primary">
		<ul>
			{#each nav as item (item.href)}
				<li>
					<a href={item.href} aria-current={isActive(item.href) ? 'page' : undefined}>{item.label}</a>
				</li>
			{/each}
		</ul>
	</nav>
</header>
<main id="main" tabindex="-1">
	{@render children()}
</main>

<style>
	.skip-link {
		position: absolute; left: var(--space-2); top: -4rem; z-index: 100;
		background: var(--color-accent); color: var(--color-on-accent);
		padding: var(--space-2) var(--space-4); border-radius: var(--radius-sm); font-weight: var(--weight-bold);
		transition: top var(--dur-fast) var(--ease-out);
	}
	.skip-link:focus { top: var(--space-2); }
	.site-header {
		display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2) var(--space-6);
		padding: var(--space-2) var(--space-4);
		background: var(--color-surface); border-bottom: 1px solid var(--color-border);
	}
	.brand { color: var(--color-text); font-weight: var(--weight-bold); letter-spacing: var(--tracking-tight); text-decoration: none; }
	nav ul { display: flex; flex-wrap: wrap; gap: var(--space-1); list-style: none; margin: 0; padding: 0; }
	nav a {
		display: inline-flex; align-items: center; min-height: var(--touch-min);
		padding: 0 var(--space-3); color: var(--color-text-body); text-decoration: none;
		border-bottom: 2px solid transparent;
		transition: color var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out);
	}
	nav a:hover { color: var(--color-text); }
	nav a[aria-current='page'] { color: var(--color-text); border-bottom-color: var(--color-accent); }
	main { max-width: var(--content-max); margin: 0 auto; padding: var(--space-6) var(--space-4); }
	main:focus { outline: none; } /* programmatic target of skip link only */
	@media (min-width: 40rem) { main { padding: var(--space-8) var(--space-6); } }
	@media print { .skip-link, .site-header { display: none; } }
</style>
