<script lang="ts">
 import type { Snippet } from 'svelte';
 let { title, code, id, children }: { title: string; code?: string; id?: string; children: Snippet } = $props();
</script>
<section class="review-section" {id}><h2>{#if code}<span class="eyebrow">{code}</span>{/if}{title}</h2>{@render children()}</section>
