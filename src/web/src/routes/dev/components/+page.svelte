<script lang="ts">
	// Index of the shared-component fixtures. src/web/tests/components_a11y.py follows every link
	// here that returns 200; fixtures for components that have not landed yet 404 and are skipped.
	const fixtures = [
		{ slug: 'primitives', label: 'Primitives: buttons, icons, meta, tints' },
		{ slug: 'empty-state', label: 'EmptyState' },
		{ slug: 'error-banner', label: 'ErrorBanner' },
		{ slug: 'status-pill', label: 'StatusPill' },
		{ slug: 'scroll-table', label: 'ScrollTable' },
		{ slug: 'form-pending', label: 'Form pending' }
	];
</script>

<svelte:head><title>Component fixtures — Commander Lab</title></svelte:head>

<div class="fixtures">
	<h1>Component fixtures</h1>
	<p class="meta">Dev-only pages that render each shared component for the browser accessibility runner.</p>
	<ul>
		{#each fixtures as f (f.slug)}
			<li><a class="text-link" href="/dev/components/{f.slug}">{f.label}</a></li>
		{/each}
	</ul>
</div>

<style>
	.fixtures { padding: var(--space-4); max-width: var(--content-max); margin: 0 auto; }
	ul { list-style: none; margin: 0; padding: 0; }
</style>
