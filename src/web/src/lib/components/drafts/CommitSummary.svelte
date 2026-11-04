<script lang="ts">
	// Read-only commit summary, shared by the commit dialog and the no-JS inline confirm (page-specs §2.6).
	import Icon from '#lib/components/Icon.svelte';
	import ManaPips from '#lib/components/ManaPips.svelte';
	import StatusPill from '#lib/components/StatusPill.svelte';
	import { donorSentence, type CommitSummary } from '#lib/drafts/summary.ts';

	let { summary, namesMissing = false }: { summary: CommitSummary; namesMissing?: boolean } = $props();

	const MAX_INLINE = 6;
	const pullSentence = $derived(donorSentence(summary.donors));
</script>

<dl class="sum">
	<dt>New deck</dt>
	<dd class="hero">Creates <strong>{summary.newDeckName}</strong></dd>

	<dt>Cards</dt>
	<dd>{summary.cardsLine}</dd>

	<dt>{summary.commanders.length === 1 ? 'Commander' : 'Commanders'}</dt>
	<dd>{summary.commanders.length ? summary.commanders.join(' + ') : 'None staged'}</dd>

	<dt>Colour identity</dt>
	<dd><ManaPips identity={summary.identity} /></dd>

	<dt>Copy moves</dt>
	<dd>{summary.movesLine ?? 'No copy moves staged. Commit without moves creates a virtual deck.'}</dd>

	{#if pullSentence}
		<dt>Donor pulls</dt>
		<dd>
			{pullSentence}
			{#each summary.donors as d (d.deckId)}
				{#if d.cards.length > MAX_INLINE}
					<details class="donors-more">
						<summary>Show all {d.cards.length} cards pulled</summary>
						<ul class="donors" aria-label={`Cards pulled from ${d.deckName}`}>
							{#each d.cards as c, i (i)}<li>{c}</li>{/each}
						</ul>
					</details>
				{:else}
					<ul class="donors" aria-label={`Cards pulled from ${d.deckName}`}>
						{#each d.cards as c, i (i)}<li>{c}</li>{/each}
					</ul>
				{/if}
			{/each}
		</dd>
	{/if}

	<dt>Expected status</dt>
	<dd>
		<StatusPill
			status={summary.expectedStatus === 'have' ? 'success' : 'neutral'}
			label={summary.expectedStatusLabel}
		/>
		<span class="meta">expected</span>
	</dd>
</dl>

{#if summary.warnings.length || namesMissing}
	<ul class="warns" aria-label="Warnings">
		{#each summary.warnings as w (w)}
			<li><Icon name="alert" class="warn-icon" /> <span>{w}</span></li>
		{/each}
		{#if namesMissing}
			<li><Icon name="alert" class="warn-icon" /> <span>Deck names unavailable.</span></li>
		{/if}
	</ul>
{/if}

<p class="note"><Icon name="info" class="note-icon" /> {summary.irreversibleNote}</p>

<style>
	.sum { display: grid; grid-template-columns: minmax(0, 1fr); margin: 0; }
	@media (min-width: 40rem) {
		.sum { grid-template-columns: max-content minmax(0, 1fr); column-gap: var(--space-4); }
	}
	dt {
		font-family: var(--font-mono);
		font-size: var(--text-xs);
		text-transform: uppercase;
		letter-spacing: var(--tracking-label);
		color: var(--color-muted);
		padding-top: var(--space-3);
	}
	dd { margin: 0; padding-top: var(--space-1); color: var(--color-text); overflow-wrap: anywhere; }
	@media (min-width: 40rem) {
		dt, dd { padding-top: var(--space-3); border-top: 1px solid var(--color-border); }
		dt:first-of-type, dt:first-of-type + dd { border-top: 0; padding-top: 0; }
	}
	.hero { font-size: var(--text-lg); line-height: var(--leading-lg); }
	.meta { color: var(--color-muted); }
	.donors { list-style: none; margin: var(--space-2) 0 0; padding: 0; font-size: var(--text-sm); color: var(--color-text-body); }
	.donors li { padding: var(--space-1) 0; border-bottom: 1px dotted var(--color-border-control); }
	.donors-more summary {
		min-height: var(--touch-min);
		display: flex;
		align-items: center;
		cursor: pointer;
		color: var(--color-accent);
		font-size: var(--text-sm);
	}
	.warns { list-style: none; margin: var(--space-4) 0 0; padding: 0; display: grid; gap: var(--space-2); }
	.warns li {
		display: flex;
		gap: var(--space-2);
		align-items: baseline;
		background: var(--color-warning-bg);
		border-left: 3px solid var(--color-warning);
		padding: var(--space-2) var(--space-3);
		color: var(--color-text);
	}
	.warns :global(.warn-icon) { color: var(--color-warning); flex: none; }
	.note { margin: var(--space-4) 0 0; color: var(--color-text-body); }
	.note :global(.note-icon) { color: var(--color-info); vertical-align: -0.125em; }
</style>
