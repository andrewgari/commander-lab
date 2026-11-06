<script lang="ts">
	// Reusable overflow-x wrapper: a wide table scrolls inside this region, not the page.
	// A scrollable region must be keyboard-reachable (axe: scrollable-region-focusable), so it
	// is focusable and named. Pages that cannot use the component may use the plain
	// `<div class="table-scroll">` class from app.css; this component adds the a11y wiring.
	import type { Snippet } from 'svelte';

	interface Props {
		label: string; // accessible name of the region, e.g. "Inventory by card"
		minWidth?: string; // optional CSS length for the table's min-width, e.g. "40rem"
		children: Snippet;
	}

	let { label, minWidth, children }: Props = $props();
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex: scrollable regions must be keyboard-focusable -->
<div class="table-scroll" role="region" aria-label={label} tabindex="0" style:--table-min-width={minWidth}>
	{@render children()}
</div>
