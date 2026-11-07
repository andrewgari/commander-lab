<script lang="ts">
	// Harness for src/web/tests/a11y_shared.py: renders every shared a11y primitive on one page
	// so the 375px no-horizontal-scroll check and contrast/target assertions run against real CSS.
	import ArchetypeMixChart from '#lib/components/ArchetypeMixChart.svelte';
	import CurveChart from '#lib/components/CurveChart.svelte';
	import PowerPanel from '#lib/components/PowerPanel.svelte';
	import TableScroll from '#lib/components/TableScroll.svelte';

	const cols = ['Card', 'Board', 'Need', 'Have', 'Buy', 'In mail', 'Elsewhere', 'Decks'];
	const rows = [
		['Sol Ring', 'main', 1, 1, 0, 0, 'Trade binder', 'Atraxa; Korvold'],
		['Command Tower', 'main', 1, 0, 1, 0, '—', 'Atraxa']
	];
</script>

<svelte:head><title>Shared a11y primitives — Commander Lab</title></svelte:head>

<div>
	<h1>Shared a11y primitives</h1>
	<nav><a class="text-link" href="/decks" data-testid="text-link">← Decks</a></nav>

	<TableScroll label="Component table" >
		<table data-testid="component-table">
			<caption class="visually-hidden">Wide table in component</caption>
			<thead><tr>{#each cols as c}<th scope="col">{c}</th>{/each}</tr></thead>
			<tbody>{#each rows as r}<tr>{#each r as cell}<td>{cell}</td>{/each}</tr>{/each}</tbody>
		</table>
	</TableScroll>

	<!-- svelte-ignore a11y_no_noninteractive_tabindex: scrollable regions must be keyboard-focusable -->
	<div class="table-scroll" role="region" aria-label="Wide table via class" tabindex="0" data-testid="class-wrapper" style="--table-min-width: 48rem">
		<table>
			<caption class="visually-hidden">Wide table via class</caption>
			<thead><tr>{#each cols as c}<th scope="col">{c}</th>{/each}</tr></thead>
			<tbody>{#each rows as r}<tr>{#each r as cell}<td>{cell}</td>{/each}</tr>{/each}</tbody>
		</table>
	</div>

	<section data-testid="charts">
		<CurveChart buckets={[2, 8, 14, 10, 6, 3, 1, 0]} lands={36} avgMv={2.9} />
		<ArchetypeMixChart categories={[{ category: 'Ramp', cards: 12, share: 0.12 }]} />
		<ArchetypeMixChart categories={[]} />
		<PowerPanel power={{ available: true, estimated_bracket: 3, bracket_name: 'Upgraded', power_level: 7, game_changers: 2, notes: ['Two Game Changers.'] }} />
		<PowerPanel power={null} />
	</section>
</div>
