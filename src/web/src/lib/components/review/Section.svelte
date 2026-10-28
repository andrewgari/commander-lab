<script lang="ts">
 import type { Snippet } from 'svelte';
 let { title, code, children }: { title: string; code?: string; children: Snippet } = $props();
</script>
<section class="review-section"><h2>{#if code}<span class="eyebrow">{code}</span>{/if}{title}</h2>{@render children()}</section>
