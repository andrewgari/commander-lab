<script lang="ts">
 import CardImage from './CardImage.svelte';
 import type { Swap } from './types';
 import { priceLabel } from './types';
 import type { Snippet } from 'svelte';
 let { swap, index, topIds, swapActions }: { swap: Swap; index: number; topIds: string[]; swapActions?: Snippet<[Swap]> } = $props();
</script>
<article class="swap" id={'swap-' + swap.id} tabindex="-1">
 <div class="swap-heading"><span class="eyebrow">Swap {String(index+1).padStart(2,'0')}</span>{#if topIds.includes(swap.id)}<span class="badge alert">Top 3 · priority {topIds.indexOf(swap.id)+1}</span>{/if}<a href={'#finding-' + swap.resolves_finding_id}>Evidence ↗</a></div>
 <div class="swap-body"><div class="swap-pair">{#each [{label:'Cut',card:swap.cut},{label:'Add',card:swap.add}] as {label,card}}<div class="swap-card"><span class="eyebrow">{label}</span><CardImage name={card.name}/><h3 class="card-name">{card.name}</h3></div>{/each}</div>
  <div class="swap-copy"><h3>Trade-off</h3><p>{swap.tradeoff}</p><div class="swap-meta"><span class="badge" class:alert={swap.bracket_effect === 'raises'}>Bracket: {swap.bracket_effect === 'none' ? 'unchanged' : swap.bracket_effect}</span><span class="badge" class:owned={swap.owned}>{swap.owned ? 'Owned' : 'Not owned'}</span><strong class="mono">{priceLabel(swap.price)}</strong></div><p>{swap.budget_effect}</p>{#if swap.alternative}<p class="muted">Alternative: <span class="card-name">{swap.alternative.name}</span></p>{/if}</div>
 </div>
 {@render swapActions?.(swap)}
</article>
