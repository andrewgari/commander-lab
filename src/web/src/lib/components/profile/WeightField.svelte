<script lang="ts">
	import ProvenanceBadge from './ProvenanceBadge.svelte';
	import type { Badge } from '#lib/profile/provenance.ts';

	interface Props {
		path: string;
		label: string;
		entries: { label: string; weight: string }[];
		badge: (label: string) => Badge;
		rollup: string | null;
		errors?: Record<string, string>;
		note?: (label: string) => string | null;
		suggestions?: string[];
		js: boolean;
	}
	let { path, label, entries = $bindable(), badge, rollup, errors = {}, note, suggestions = [], js }: Props = $props();
	const uid = $props.id();
	let box: HTMLElement | undefined = $state();

	function add() {
		entries = [...entries, { label: '', weight: '0.5' }];
		queueMicrotask(() => {
			const els = box?.querySelectorAll<HTMLElement>('[data-row-input]');
			els?.[els.length - 1]?.focus();
		});
	}
	function remove(i: number) {
		entries = entries.filter((_, j) => j !== i);
		queueMicrotask(() => {
			const els = box?.querySelectorAll<HTMLElement>('[data-row-input]');
			(els?.[Math.min(i, (els?.length ?? 1) - 1)] ?? box?.parentElement?.querySelector<HTMLElement>('button.add'))?.focus();
		});
	}
	const pct = (w: string) => Math.max(0, Math.min(1, Number(w) || 0));
</script>

<div class="wf" data-field={path}>
	<div class="wf__head">
		<span class="wf__label" id={`${uid}-l`}>{label}</span>
		{#if rollup}<span class="wf__rollup">{rollup}</span>{/if}
	</div>
	<div bind:this={box} role="group" aria-labelledby={`${uid}-l`}>
		{#each entries as e, i (i)}
			{@const b = badge(e.label.trim())}
			{@const err = errors[e.label.trim()]}
			{@const nt = note?.(e.label.trim())}
			<div class="wf__row" class:wf__row--err={!!err}>
				<input
					type="text" name={`${path}__label`} data-row-input list={`${uid}-dl`} autocomplete="off"
					aria-label={`${label} item ${i + 1} label`} aria-invalid={err ? 'true' : undefined}
					aria-describedby={err ? `${uid}-e${i}` : undefined} bind:value={e.label}
				/>
				<input
					type="number" class="wf__num" name={`${path}__weight`} min="0" max="1" step="0.05" inputmode="decimal"
					aria-label={`${label} item ${i + 1} weight`} aria-invalid={err ? 'true' : undefined} bind:value={e.weight}
				/>
				<span class="wf__bar" aria-hidden="true" style={`--w:${pct(e.weight)}`} data-kind={b.kind}></span>
				<span class="wf__badge"><ProvenanceBadge kind={b.kind} prov={b.prov} /></span>
				{#if js}
					<button type="button" class="wf__remove" aria-label={`Remove ${e.label || 'item ' + (i + 1)} from ${label}`} onclick={() => remove(i)}>✕</button>
				{:else}
					<label class="wf__remove"><input type="checkbox" name={`${path}__remove`} value={i} /> Remove</label>
				{/if}
				{#if err}<p class="wf__err" id={`${uid}-e${i}`}>{err}</p>{:else if nt}<p class="wf__note">{nt}</p>{/if}
			</div>
		{/each}
		{#if !js}
			<div class="wf__row">
				<input type="text" name={`${path}__label`} aria-label={`Add ${label} label`} placeholder="Add label…" autocomplete="off" />
				<input type="number" class="wf__num" name={`${path}__weight`} min="0" max="1" step="0.05" aria-label={`Add ${label} weight`} />
			</div>
		{/if}
	</div>
	{#if js}<button type="button" class="btn btn--secondary add" onclick={add}>Add another</button>{/if}
	<datalist id={`${uid}-dl`}>{#each suggestions as s (s)}<option value={s}></option>{/each}</datalist>
</div>

<style>
	.wf { padding: var(--space-3) 0; border-top: 1px dotted var(--color-border); }
	.wf__head { display: flex; align-items: baseline; gap: var(--space-3); }
	.wf__label { color: var(--color-text); font-weight: var(--weight-medium); }
	.wf__rollup { margin-left: auto; font: var(--text-xs) var(--font-mono); color: var(--color-muted); }
	.wf__row { display: grid; grid-template-columns: minmax(0, 1fr) 5.5rem minmax(3rem, 6rem) 9.5rem var(--touch-min); gap: var(--space-2); align-items: center; margin-top: var(--space-2); }
	.wf__row input[type='text'] { min-width: 0; width: 100%; }
	.wf__num { text-align: right; font-family: var(--font-mono); width: 100%; }
	.wf__bar { height: 8px; background: var(--color-surface-raised); border: 1px solid var(--color-border); position: relative; }
	.wf__bar::after { content: ''; position: absolute; inset: 0 auto 0 0; width: calc(var(--w) * 100%); background: var(--color-accent); transition: width var(--dur-fast); }
	.wf__bar[data-kind='learned']::after { background: var(--color-info); }
	.wf__row--err input { border-color: var(--color-danger); }
	.wf__remove { min-height: var(--touch-min); min-width: var(--touch-min); display: inline-flex; align-items: center; justify-content: center; background: transparent; border-color: transparent; color: var(--color-muted); }
	.wf__remove:hover { color: var(--color-danger); border-color: var(--color-danger-solid); }
	.wf__err, .wf__note { grid-column: 1 / -1; margin: 0; font-size: var(--text-xs); }
	.wf__err { color: var(--color-danger); }
	.wf__note { color: var(--color-warning); }
	.add { margin-top: var(--space-2); }
	@media (max-width: 40rem) {
		.wf__row { grid-template-columns: minmax(0, 1fr) 5.5rem var(--touch-min); }
		.wf__bar, .wf__badge { grid-row: 2; }
		.wf__bar { grid-column: 1; }
		.wf__badge { grid-column: 2 / 4; }
	}
</style>
