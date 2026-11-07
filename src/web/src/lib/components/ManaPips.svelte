<script lang="ts">
	// Colour identity as lettered pips: the letter always shows, so meaning is never colour-only.
	interface Props {
		identity: string[];
		size?: 'sm' | 'md';
	}
	let { identity, size = 'md' }: Props = $props();

	const NAMES: Record<string, string> = { W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green', C: 'Colorless' };
	const ORDER = ['W', 'U', 'B', 'R', 'G'];

	const pips = $derived.by(() => {
		const known = ORDER.filter((c) => identity.map((i) => i.toUpperCase()).includes(c));
		return known.length > 0 ? known : ['C'];
	});
	const label = $derived(`Colour identity: ${pips.map((c) => NAMES[c]).join(', ')}`);
</script>

<span class="pips pips--{size}" role="img" aria-label={label}>
	{#each pips as c (c)}
		<span class="pip" style:background="var(--mana-{c.toLowerCase()})" aria-hidden="true">{c}</span>
	{/each}
</span>

<style>
	.pips { display: inline-flex; flex-wrap: wrap; gap: 2px; }
	.pip {
		display: inline-flex; align-items: center; justify-content: center;
		width: 1.25rem; height: 1.25rem; border-radius: 50%;
		font-family: var(--font-mono); font-size: 11px; font-weight: var(--weight-bold); line-height: 1;
		color: var(--color-on-accent);
	}
	.pips--sm .pip { width: 1rem; height: 1rem; font-size: 9px; }
	@media (forced-colors: active) { .pip { border: 1px solid CanvasText; background: Canvas !important; color: CanvasText; } }
</style>
