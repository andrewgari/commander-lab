<script lang="ts">
 import ReviewReport from '#lib/components/review/ReviewReport.svelte';
 import type { PageProps } from './$types';
 let { data }: PageProps = $props();
</script>
<svelte:head><title>Review preview — Commander Lab</title></svelte:head>
{#if !data.print}<form class="review-preview" method="GET"><strong>Fixture preview · not live advice</strong><label>Scenario <select name="variant" value={data.variant}>{#each data.variants as v}<option value={v}>{v}</option>{/each}</select></label><label>Theme <select name="theme" value={data.theme}><option value="dark">Dark</option><option value="light">Light</option></select></label><button type="submit">View</button><button type="button" onclick={() => window.print()}>Print</button><a href={`?variant=${data.variant}&print=1`}>Print view</a></form>{/if}
<main><ReviewReport document={data.document} theme={data.theme} print={data.print}/></main>
<style>:global(body:has(.review-report)) { margin:0; background:var(--color-bg); } :global(body:has(.print-mode)), :global(body:has(.review-report[data-theme='light'])) { background:white; } @media print { :global(body:has(.review-report)) { background:white; } }</style>
