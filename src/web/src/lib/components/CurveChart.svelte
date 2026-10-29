<script lang="ts">
	// Mana-value curve as an inline SVG bar chart — deterministic rendering of
	// metrics.curve from the profile snapshot (no chart library, ARCHITECTURE.md
	// §1 leaves that choice open; SVG keeps the page dependency-free).
	interface Props {
		buckets: number[]; // index 0..6 = MV, index 7 = "7+"
		lands?: number | null;
		avgMv?: number | null;
	}

	let { buckets, lands = 0, avgMv = null }: Props = $props();

	const labels = ['0', '1', '2', '3', '4', '5', '6', '7+'];
	const width = 320;
	const height = 120;
	const padTop = 14;
	const padBottom = 18;
	const barGap = 6;

	const maxVal = $derived(Math.max(1, ...buckets));
	const barWidth = $derived((width - barGap * (buckets.length - 1)) / Math.max(1, buckets.length));
	const plotHeight = height - padTop - padBottom;
</script>

<figure class="curve">
	<svg viewBox="0 0 {width} {height}" role="img" aria-label="Mana curve bar chart">
		{#each buckets as count, i (i)}
			{@const barH = (count / maxVal) * plotHeight}
			{@const x = i * (barWidth + barGap)}
			{@const y = padTop + (plotHeight - barH)}
			<rect {x} {y} width={barWidth} height={barH} class="bar" rx="2" />
			<text x={x + barWidth / 2} y={y - 3} class="count" text-anchor="middle">
				{count || ''}
			</text>
			<text x={x + barWidth / 2} y={height - 4} class="label" text-anchor="middle">
				{labels[i] ?? i}
			</text>
		{/each}
	</svg>
	<figcaption>
		Mana curve (non-land spells)
		{#if avgMv !== null}· avg MV {avgMv}{/if}
		{#if lands !== null}· {lands} lands{/if}
	</figcaption>
</figure>

<style>
	figure {
		margin: 0;
	}
	svg {
		width: 100%;
		max-width: 20rem;
		height: auto;
	}
	.bar {
		fill: var(--data, #4a7dbd);
	}
	.count {
		font-size: 9px;
		fill: var(--text-3, var(--color-muted));
	}
	.label {
		font-size: 9px;
		fill: var(--text-3, var(--color-muted));
	}
	figcaption {
		font-size: 0.8rem;
		color: var(--text-3, var(--color-muted));
	}
</style>
