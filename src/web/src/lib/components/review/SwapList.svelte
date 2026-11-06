<script lang="ts">
 import Section from './Section.svelte';
 import SwapCard from './SwapCard.svelte';
 import type { Swap } from './types';
 import type { Snippet } from 'svelte';
 let { swaps, topIds, swapActions }: { swaps: Swap[]; topIds: string[]; swapActions?: Snippet<[Swap]> } = $props();
</script>
<section class="review-section swaps-section">
 <div class="swap-opening"><h2><span class="eyebrow">05</span>Recommended swaps</h2><p class="muted">{swaps.length} options · Read the trade-off before changing a slot. Prices in USD.</p>{#if swaps.length}<SwapCard swap={swaps[0]} index={0} {topIds} {swapActions}/>{/if}</div>
 <div class="swap-list">{#each swaps.slice(1) as swap,i}<SwapCard {swap} index={i+1} {topIds} {swapActions}/>{/each}</div>
</section>
