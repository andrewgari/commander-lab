<script lang="ts">
	// Fixture for C3 StatusPill, exercised by src/web/tests/components_a11y.py.
	import TableScroll from '#lib/components/TableScroll.svelte';
	import StatusPill from '#lib/components/StatusPill.svelte';
	import { STATUS_CATALOG, draftStatus, gameResult, type StatusKey } from '#lib/status.ts';

	const keys = Object.keys(STATUS_CATALOG) as StatusKey[];
	const variants = ['solid', 'text'] as const;
	const sizes = ['sm', 'md'] as const;
	const surfaces = [
		{ id: 'bg', label: 'On background', cls: 'on-bg' },
		{ id: 'surface', label: 'On surface', cls: 'on-surface' },
		{ id: 'raised', label: 'On raised surface', cls: 'on-raised' }
	];
	// What an API can return that the catalog does not know: renders neutral with the raw string.
	const unknownDraft = 'archived';
	const unknownGame = 'forfeit';
</script>

<svelte:head><title>StatusPill — Component fixtures — Commander Lab</title></svelte:head>

<div class="fixture">
	<h1>StatusPill</h1>

	<section aria-labelledby="h-list">
		<h2 id="h-list">Every status in a list (catalog default variant)</h2>
		<ul class="list" data-testid="list">
			{#each keys as key (key)}
				<li><StatusPill status={key} /></li>
			{/each}
		</ul>
	</section>

	<section aria-labelledby="h-matrix">
		<h2 id="h-matrix">Variants and sizes</h2>
		{#each variants as variant (variant)}
			{#each sizes as size (size)}
				<h3 id="h-{variant}-{size}">{variant} / {size}</h3>
				<ul class="list" aria-labelledby="h-{variant}-{size}" data-testid="matrix-{variant}-{size}">
					{#each keys as key (key)}
						<li><StatusPill status={key} {variant} {size} /></li>
					{/each}
				</ul>
			{/each}
		{/each}
	</section>

	<section aria-labelledby="h-surfaces">
		<h2 id="h-surfaces">Text variant on each surface</h2>
		{#each surfaces as s (s.id)}
			<div class="panel {s.cls}" data-testid="surface-{s.id}">
				<h3>{s.label}</h3>
				<ul class="list">
					{#each keys as key (key)}
						<li><StatusPill status={key} variant="text" /></li>
					{/each}
				</ul>
			</div>
		{/each}
	</section>

	<section aria-labelledby="h-table">
		<h2 id="h-table">In a table cell</h2>
		<TableScroll label="Game results">
			<table data-testid="table">
				<caption class="visually-hidden">Game results</caption>
				<thead>
					<tr><th scope="col">Game</th><th scope="col">Result</th><th scope="col">Deck</th></tr>
				</thead>
				<tbody>
					{#each ['game-win', 'game-loss', 'game-draw', 'game-unknown'] as key, i (key)}
						<tr>
							<td>Game {i + 1}</td>
							<td>
								<span class="visually-hidden">Result: </span><StatusPill status={key as StatusKey} />
							</td>
							<td>
								<StatusPill status={['short', 'owned', 'in-mail', 'unavailable'][i] as StatusKey} />
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</TableScroll>
	</section>

	<section aria-labelledby="h-heading">
		<h2 id="h-heading">In headings</h2>
		<h3 data-testid="heading-sm">Game 7 <StatusPill status="game-win" /></h3>
		<h3 data-testid="heading-md">Game 8 <StatusPill status="game-loss" size="md" /></h3>
	</section>

	<section aria-labelledby="h-fallback">
		<h2 id="h-fallback">Unknown values and overrides</h2>
		<ul class="list" data-testid="fallback">
			<li><StatusPill status={draftStatus(unknownDraft)} label={unknownDraft} /></li>
			<li><StatusPill status={gameResult(unknownGame)} label={unknownGame} /></li>
			<li><StatusPill status="short" label="Buy 2" /></li>
			<li><StatusPill status="owned" label="Owned" variant="text" /></li>
			<li><StatusPill status="game-win" label="Won 2–1" icon={null} /></li>
			<li><StatusPill status="unavailable" describedby="why-unavailable" title="Price source offline" /></li>
		</ul>
		<p id="why-unavailable" class="meta">Unavailable: the price source was offline when this page loaded.</p>
	</section>

	<section aria-labelledby="h-wrap">
		<h2 id="h-wrap">Narrow container (wraps, never truncates)</h2>
		<div class="narrow" data-testid="narrow">
			<StatusPill status="game-unknown" size="md" />
			<StatusPill status="neutral" label="A very long unrecognised status value from the API" />
		</div>
	</section>
</div>

<style>
	.fixture { padding: var(--space-4); max-width: var(--content-max); margin: 0 auto; }
	.list { display: flex; flex-wrap: wrap; gap: var(--space-3); list-style: none; margin: 0 0 var(--space-4); padding: 0; }
	.panel { padding: var(--space-4); margin-bottom: var(--space-3); border-radius: var(--radius-sm); }
	.on-bg { background: var(--color-bg); border: 1px solid var(--color-border); }
	.on-surface { background: var(--color-surface); }
	.on-raised { background: var(--color-surface-raised); }
	.narrow { width: 20rem; max-width: 100%; padding: var(--space-2); border: 1px solid var(--color-border); }
</style>
