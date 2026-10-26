<script lang="ts">
 import Section from './Section.svelte';
 import SwapCard from './SwapCard.svelte';
 import type { Swap } from './types';
 let { swaps, topIds }: { swaps: Swap[]; topIds: string[] } = $props();
</script>
<section class="review-section swaps-section">
 <div class="swap-opening"><h2><span class="eyebrow">05</span>Recommended swaps</h2><p class="muted">{swaps.length} options · Read the trade-off before changing a slot. Prices in USD.</p>{#if swaps.length}<SwapCard swap={swaps[0]} index={0} {topIds}/>{/if}</div>
 <div class="swap-list">{#each swaps.slice(1) as swap,i}<SwapCard {swap} index={i+1} {topIds}/>{/each}</div>
</section>
