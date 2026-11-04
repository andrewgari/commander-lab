<script lang="ts">
	// Result thumbnails + badges + add buttons (docs/ui/card-search.md §4.1).
	import CardImage from '#lib/components/review/CardImage.svelte';
	import type { Board, CardHit, Color } from './types.ts';

	interface Props {
		results: CardHit[];
		inList?: Map<string, number>;
		identity?: Color[];
		preset?: 'commander';
		fixedQty?: 1;
		board: Board;
		onadd: (card: CardHit, qty: number) => void;
	}
	let { results, inList, identity, preset, fixedQty, board, onadd }: Props = $props();

	let qtys = $state<Record<string, number>>({});
	const qtyOf = (c: CardHit) => qtys[c.oracle_id || c.name] ?? 1;
	function step(c: CardHit, d: number) {
		const k = c.oracle_id || c.name;
		qtys[k] = Math.max(1, Math.min(99, (qtys[k] ?? 1) + d));
	}
	const outside = (c: CardHit) => !!identity && c.color_identity.some((x) => x !== 'C' && !identity.includes(x));

	function badges(c: CardHit): { text: string; tone: string }[] {
		const b: { text: string; tone: string }[] = [];
		b.push(c.owned_qty > 0 ? { text: `Own ${c.owned_qty} · ${c.free_qty} free`, tone: 'ok' } : { text: 'Not owned', tone: 'muted' });
		const n = inList?.get(c.oracle_id);
		if (n) b.push({ text: `In deck ×${n}`, tone: 'info' });
		if (outside(c)) b.push({ text: 'Outside identity', tone: 'warn' });
		if (preset === 'commander') {
			if (c.pairing === 'background') b.push({ text: 'Background', tone: 'info' }, { text: 'Needs a partner', tone: 'warn' });
			else if (c.pairing && c.pairing !== 'choose_background') b.push({ text: 'Partner', tone: 'info' });
			if (/\bVehicle\b/.test(c.type_line)) b.push({ text: 'Vehicle', tone: 'info' });
			else if (/\bPlaneswalker\b/.test(c.type_line)) b.push({ text: 'Planeswalker', tone: 'info' });
		} else if (c.can_be_commander) b.push({ text: 'Commander', tone: 'info' });
		return b;
	}
</script>

<ul class="grid">
	{#each results as c (c.oracle_id || c.name)}
		{@const have = inList?.get(c.oracle_id)}
		<li class="card">
			{#if c.oracle_id}
				<a class="thumb" href="/card/{c.oracle_id}" target="_blank" rel="noopener" aria-label="{c.name} (opens card page in a new tab)"><CardImage name={c.name} /></a>
			{:else}
				<div class="thumb"><CardImage name={c.name} /></div>
			{/if}
			<div class="name">{c.name}</div>
			<div class="cost">{c.mana_cost}</div>
			<ul class="badges" aria-label="Status of {c.name}">
				{#each badges(c) as b (b.text)}<li class="badge badge-{b.tone}">{b.text}</li>{/each}
			</ul>
			<div class="actions">
				{#if !fixedQty}
					<div class="stepper" role="group" aria-label="Quantity of {c.name}">
						<button type="button" aria-label="Fewer" onclick={() => step(c, -1)}>−</button>
						<span aria-live="polite">{qtyOf(c)}</span>
						<button type="button" aria-label="More" onclick={() => step(c, 1)}>+</button>
					</div>
				{/if}
				<button type="button" class="btn btn--primary add" onclick={() => onadd(c, fixedQty ?? qtyOf(c))} aria-label="Add {fixedQty ?? qtyOf(c)} {c.name} to {board}">
					{preset === 'commander' ? 'Pick' : have ? `+${qtyOf(c)} (have ${have})` : `+${qtyOf(c)}`}
				</button>
			</div>
		</li>
	{/each}
</ul>

<style>
	.grid { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(9.5rem, 1fr)); gap: var(--space-4); }
	.card { display: flex; flex-direction: column; gap: var(--space-1); min-width: 0; }
	.thumb { display: block; border-radius: var(--radius-md); }
	.thumb :global(.card-image) { width: 100%; }
	.thumb :global(img) { width: 100%; height: auto; border-radius: var(--radius-md); display: block; }
	.name { font-weight: var(--weight-medium); overflow-wrap: anywhere; }
	.cost { font-family: var(--font-mono); font-size: var(--text-xs); color: var(--color-muted); min-height: 1em; }
	.badges { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: var(--space-1); }
	.badge { font-size: var(--text-xs); padding: 0 var(--space-2); border: 1px solid var(--color-border-control); border-radius: var(--radius-sm); color: var(--color-text-body); }
	.badge-ok { color: var(--color-success); }
	.badge-warn { color: var(--color-warning); }
	.badge-info { color: var(--color-info); }
	.badge-muted { color: var(--color-muted); }
	.actions { display: flex; gap: var(--space-2); align-items: center; margin-top: auto; flex-wrap: wrap; }
	.add { flex: 1; justify-content: center; }
	.stepper { display: inline-flex; align-items: center; gap: var(--space-1); }
	.stepper button {
		min-width: var(--touch-min); min-height: var(--touch-min); font: inherit; cursor: pointer;
		background: var(--color-surface-raised); color: var(--color-text); border: 1px solid var(--color-border-control); border-radius: var(--radius-sm);
	}
	.stepper span { min-width: 1.5rem; text-align: center; }
	a.thumb:focus-visible, .stepper button:focus-visible, .add:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 2px; }
</style>
