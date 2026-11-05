<script lang="ts" module>
	export interface Column {
		key: string;
		header: string;
		align?: 'start' | 'end' | 'center';
		numeric?: boolean;
		/** Header text is screen-reader-only; never an empty <th>. */
		headerHidden?: boolean;
		/** Any CSS width. */
		width?: string;
	}
</script>

<script lang="ts" generics="T">
	// Column-driven data table inside a focusable, named scroll region (TableScroll).
	// ScrollTable owns every <tr> and cell (README D5); callers customise cell content via `cell`.
	// An empty table renders no <table> and no region, only the empty content (README D4-D8).
	import type { Snippet } from 'svelte';
	import TableScroll from '#lib/components/TableScroll.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';

	interface Props {
		/** Becomes the <caption> and the region's accessible name. */
		caption: string;
		captionHidden?: boolean;
		columns: Column[];
		rows: T[];
		/** Defaults to the row index. */
		rowKey?: (row: T, index: number) => string | number;
		/** Key of the column that renders <th scope="row">. */
		rowHeader?: string;
		/** Requires `rowHeader` to be the first column. */
		stickyFirstCol?: boolean;
		density?: 'comfortable' | 'compact';
		minWidth?: string;
		cell?: Snippet<[T, Column]>;
		empty?: Snippet;
		emptyTitle?: string;
		emptyDescription?: string;
		/** Outer wrapper only. */
		class?: string;
	}

	let {
		caption,
		captionHidden = false,
		columns,
		rows,
		rowKey,
		rowHeader,
		stickyFirstCol = false,
		density = 'comfortable',
		minWidth = '40rem',
		cell,
		empty,
		emptyTitle = 'Nothing to show',
		emptyDescription,
		class: className
	}: Props = $props();

	$effect(() => {
		if (stickyFirstCol && (!rowHeader || columns[0]?.key !== rowHeader)) {
			console.warn('ScrollTable: stickyFirstCol requires rowHeader to be the first column; the sticky column is not applied.');
		}
	});

	const sticky = $derived(stickyFirstCol && !!rowHeader && columns[0]?.key === rowHeader);

	function value(row: T, key: string): unknown {
		return (row as Record<string, unknown>)[key];
	}
</script>

{#if rows.length === 0}
	<div class="st-empty {className ?? ''}">
		<p class="visually-hidden">{caption}</p>
		{#if empty}
			{@render empty()}
		{:else}
			<EmptyState variant="inline" headingLevel={3} title={emptyTitle} description={emptyDescription} />
		{/if}
	</div>
{:else}
	<div class="st {className ?? ''}" class:st--compact={density === 'compact'} class:st--sticky={sticky}>
		<TableScroll label={caption} {minWidth}>
			<table>
				<caption class:visually-hidden={captionHidden}>{caption}</caption>
				<thead>
					<tr>
						{#each columns as column (column.key)}
							<th
								scope="col"
								class:st-num={column.numeric}
								style:text-align={column.align === 'end' || column.numeric ? 'end' : column.align}
								style:width={column.width}
							>
								{#if column.headerHidden}<span class="visually-hidden">{column.header}</span>{:else}{column.header}{/if}
							</th>
						{/each}
					</tr>
				</thead>
				<tbody>
					{#each rows as row, i (rowKey ? rowKey(row, i) : i)}
						<tr>
							{#each columns as column (column.key)}
								{@const v = value(row, column.key)}
								{#snippet content()}
									{#if cell}
										{@render cell(row, column)}
									{:else if v === null || v === undefined}
										—<span class="visually-hidden">no value</span>
									{:else}
										{v}
									{/if}
								{/snippet}
								{#if column.key === rowHeader}
									<th scope="row" class:st-num={column.numeric} style:text-align={column.align === 'end' || column.numeric ? 'end' : column.align}>
										{@render content()}
									</th>
								{:else}
									<td class:st-num={column.numeric} style:text-align={column.align === 'end' || column.numeric ? 'end' : column.align}>
										{@render content()}
									</td>
								{/if}
							{/each}
						</tr>
					{/each}
				</tbody>
			</table>
		</TableScroll>
	</div>
{/if}

<style>
	.st { min-width: 0; max-width: 100%; }
	caption {
		caption-side: top;
		text-align: start;
		padding-block-end: var(--space-2);
		font-size: var(--text-sm);
		font-weight: var(--weight-bold);
		letter-spacing: var(--tracking-label);
		color: var(--color-text-body);
	}
	thead th { background: var(--color-surface-raised); white-space: nowrap; }
	td, th[scope='row'] { color: var(--color-text-body); }
	th[scope='row'] { font-weight: var(--weight-medium); text-align: start; }
	.st-num { font-variant-numeric: tabular-nums; text-align: end; }

	.st--compact th, .st--compact td { padding: var(--space-1) var(--space-2); }
	@media (max-width: 480px) {
		.st th, .st td { padding: var(--space-1) var(--space-2); }
	}

	/* The scroll region's focus ring sits inside it so overflow clipping cannot hide it. */
	/* Containing block for the absolutely positioned .visually-hidden text inside the table;
	   without it that text escapes the region and widens the page. */
	.st :global(.table-scroll) { position: relative; }
	.st :global(.table-scroll:focus-visible) { outline-offset: -2px; }

	/* border-collapse drops borders on sticky cells, so the trailing edge is an inset shadow. */
	.st--sticky th:first-child, .st--sticky td:first-child {
		position: sticky; inset-inline-start: 0; z-index: 1;
		background: var(--color-surface);
		box-shadow: inset -1px 0 0 var(--color-border-control);
	}
	.st--sticky thead th:first-child { background: var(--color-surface-raised); z-index: 2; }

	@media (forced-colors: active) {
		th, td { border-color: CanvasText; }
		.st--sticky th:first-child, .st--sticky td:first-child {
			background: Canvas;
			box-shadow: inset -1px 0 0 CanvasText;
		}
	}
</style>
