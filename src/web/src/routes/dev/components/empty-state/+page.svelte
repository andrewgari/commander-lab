<script lang="ts">
	// Fixture for EmptyState, exercised by src/web/tests/components_a11y.py.
	import EmptyState from '#lib/components/EmptyState.svelte';

	let clicks = $state(0);
	let query = $state('zzz');
</script>

<svelte:head><title>EmptyState — Component fixtures — Commander Lab</title></svelte:head>

<div class="fixture">
	<h1>EmptyState</h1>

	<section aria-labelledby="h-page-link">
		<h2 id="h-page-link">Page, link CTA</h2>
		<EmptyState
			icon="info"
			title="No decks yet"
			description="Import a deck from Archidekt or Moxfield to start analysing it."
			ctaLabel="Browse decks"
			ctaHref="/dev/components"
		/>
	</section>

	<section aria-labelledby="h-page-btn">
		<h2 id="h-page-btn">Page, button CTA</h2>
		<EmptyState
			title="No drafts yet"
			description="Pick a deck above and start one."
			ctaLabel="Count this click"
			onCta={() => (clicks += 1)}
		/>
		<p class="meta" data-testid="clicks">Clicks: {clicks}</p>
	</section>

	<section aria-labelledby="h-inline">
		<h2 id="h-inline">Inline</h2>
		<EmptyState
			variant="inline"
			icon="help"
			title="No games imported yet"
			description="Import a game log using the form above."
			ctaLabel="Browse decks"
			ctaHref="/dev/components"
		/>
	</section>

	<section aria-labelledby="h-compact">
		<h2 id="h-compact">Inline, compact (no heading)</h2>
		<EmptyState variant="inline" compact title="No tags yet" />
	</section>

	<section aria-labelledby="h-filtered">
		<h2 id="h-filtered">Filtered</h2>
		{#if query}
			<EmptyState
				variant="inline"
				filtered
				title="No cards match “{query}”"
				ctaLabel="Clear search"
				onCta={() => (query = '')}
			/>
		{:else}
			<p class="meta" data-testid="cleared">Search cleared.</p>
		{/if}
	</section>

	<section aria-labelledby="h-panel">
		<h2 id="h-panel">Inline inside a surface panel</h2>
		<div class="panel">
			<h3>Printings</h3>
			<EmptyState variant="inline" headingLevel={4} title="No printings data yet" description="Sync the card to fetch its printings." />
		</div>
	</section>

	<section aria-labelledby="h-actions">
		<h2 id="h-actions">Two actions</h2>
		<EmptyState variant="inline" title="Your collection is empty" description="Add cards one by one or import a list.">
			{#snippet actions()}
				<a class="btn btn--primary" href="/dev/components">Add cards</a>
				<a class="btn btn--secondary" href="/dev/components">Import a list</a>
			{/snippet}
		</EmptyState>
	</section>
</div>

<style>
	.fixture { padding: var(--space-4); max-width: var(--content-max); margin: 0 auto; }
	section { margin-bottom: var(--space-6); }
	.panel { background: var(--color-surface); padding: var(--space-4); border-radius: var(--radius-sm); }
	.panel h3 { margin-top: 0; }
</style>
