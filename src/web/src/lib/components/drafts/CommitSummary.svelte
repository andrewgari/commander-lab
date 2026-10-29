<script lang="ts">
	// Read-only commit summary, shared by the dialog and the no-JS inline confirm (page-specs §2.6).
	import { donorSentence, type CommitSummary } from '#lib/drafts/summary.ts';

	let { summary, namesMissing = false }: { summary: CommitSummary; namesMissing?: boolean } = $props();

	const COLOR_NAMES: Record<string, string> = { W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green', C: 'Colorless' };
	const identityLabel = $derived(summary.identity.length ? summary.identity.map((c) => COLOR_NAMES[c] ?? c).join(', ') : 'Colorless');
	const pullSentence = $derived(donorSentence(summary.donors));
	const MAX_INLINE = 6;
</script>

<dl class="sum">
	<dt>New deck</dt>
	<dd class="hero">Creates <strong>{summary.newDeckName}</strong></dd>

	<dt>Cards</dt>
	<dd>{summary.cardsLine}</dd>

	<dt>{summary.commanders.length === 1 ? 'Commander' : 'Commanders'}</dt>
	<dd>{summary.commanders.length ? summary.commanders.join(' + ') : 'None staged'}</dd>

	<dt>Colour identity</dt>
	<dd>
		{#if summary.identity.length}
			<span class="pips" role="img" aria-label={identityLabel}>{#each summary.identity as c (c)}<span class="pip pip--{c.toLowerCase()}" aria-hidden="true">{c}</span>{/each}</span>
		{:else}
			<span>Colorless</span>
		{/if}
	</dd>

	<dt>Copy moves</dt>
	<dd>{summary.movesLine ?? 'No copy moves staged. Commit without moves creates a virtual deck.'}</dd>

	{#if pullSentence}
		<dt>Donor pulls</dt>
		<dd>
			{pullSentence}
			{#each summary.donors as d (d.deckId)}
				{#if d.cards.length > MAX_INLINE}
					<details class="donors-more">
						<summary>Show all {d.cards.length} cards pulled from {d.deckName}</summary>
						<ul class="donors">{#each d.cards as c, i (i)}<li>{c}</li>{/each}</ul>
					</details>
				{:else}
					<ul class="donors" aria-label={`Cards pulled from ${d.deckName}`}>{#each d.cards as c, i (i)}<li>{c}</li>{/each}</ul>
				{/if}
			{/each}
		</dd>
	{/if}

	<dt>Expected status</dt>
	<dd><span class="pill">{summary.expectedStatusLabel}</span> <span class="meta">expected</span></dd>
</dl>

{#if summary.warnings.length || namesMissing}
	<ul class="warns" aria-label="Warnings">
		{#each summary.warnings as w (w)}<li><span aria-hidden="true">⚠</span> {w}</li>{/each}
		{#if namesMissing}<li><span aria-hidden="true">⚠</span> Deck names unavailable.</li>{/if}
	</ul>
{/if}

<p class="note"><span aria-hidden="true">ⓘ</span> {summary.irreversibleNote}</p>

<style>
	.sum { display: grid; grid-template-columns: minmax(0, 1fr); gap: 0; margin: 0; }
	@media (min-width: 40rem) { .sum { grid-template-columns: max-content minmax(0, 1fr); column-gap: var(--space-4); } }
	dt { font-family: var(--font-mono); font-size: var(--text-xs); text-transform: uppercase; letter-spacing: var(--tracking-label); color: var(--color-muted); padding-top: var(--space-3); }
	dd { margin: 0; padding-top: var(--space-1); color: var(--color-text); overflow-wrap: anywhere; }
	@media (min-width: 40rem) { dt, dd { padding-top: var(--space-3); border-top: 1px solid var(--color-border); } dt:first-of-type, dt:first-of-type + dd { border-top: 0; padding-top: 0; } }
	.hero { font-size: var(--text-lg); font-weight: var(--weight-bold); }
	.pips { display: inline-flex; gap: var(--space-1); }
	.pip { display: inline-grid; place-items: center; width: 1.5rem; height: 1.5rem; border-radius: 50%; font-family: var(--font-mono); font-size: var(--text-xs); font-weight: var(--weight-bold); color: var(--color-on-accent); background: var(--mana-c); }
	.pip--w { background: var(--mana-w); } .pip--u { background: var(--mana-u); } .pip--b { background: var(--mana-b); }
	.pip--r { background: var(--mana-r); } .pip--g { background: var(--mana-g); }
	.pill { display: inline-block; padding: 0 var(--space-2); border: 1px solid var(--color-success); border-radius: var(--radius-sm); color: var(--color-success); font-weight: var(--weight-medium); }
	.pill::before { content: '✓ '; }
	.donors { list-style: none; margin: var(--space-2) 0 0; padding: 0; font-size: var(--text-sm); color: var(--color-text-body); }
	.donors li { padding: var(--space-1) 0; border-bottom: 1px dotted var(--color-border-control); }
	.donors-more summary { min-height: var(--touch-min); display: flex; align-items: center; cursor: pointer; color: var(--color-accent); font-size: var(--text-sm); }
	.warns { list-style: none; margin: var(--space-4) 0 0; padding: 0; display: grid; gap: var(--space-2); }
	.warns li { background: var(--color-warning-bg); border-left: 3px solid var(--color-warning); padding: var(--space-2) var(--space-3); color: var(--color-text); }
	.warns li span { color: var(--color-warning); }
	.note { margin: var(--space-4) 0 0; color: var(--color-text-body); }
	.meta { color: var(--color-muted); }
</style>
