<script lang="ts">
	import ProvenanceBadge from './ProvenanceBadge.svelte';
	import type { Badge } from '#lib/profile/provenance.ts';

	interface Props {
		path: string;
		label: string;
		items: string[];
		badge: (item: string) => Badge;
		rollup: string | null;
		errors?: Record<string, string>; // item text -> message
		note?: (item: string) => string | null;
		multiline?: boolean;
		suggest?: boolean; // card-name autocomplete
		hint?: string | null; // pending suggestion hint
		js: boolean;
	}
	let { path, label, items = $bindable(), badge, rollup, errors = {}, note, multiline = false, suggest = false, hint = null, js }: Props = $props();

	const uid = $props.id();
	const LIMIT = 12;
	let showAll = $state(false);
	let rows: HTMLElement | undefined = $state();
	let options = $state<string[]>([]);
	let timer: ReturnType<typeof setTimeout> | undefined;

	const visible = $derived(
		(items.map((v, i) => ({ v, i })) as { v: string; i: number }[]).filter(
			({ v, i }) => !js || showAll || i < LIMIT || errors[v] || badge(v).kind === 'unsaved'
		)
	);
	const hidden = $derived(items.length - visible.length);

	function focusRow(i: number) {
		queueMicrotask(() => (rows?.querySelectorAll<HTMLElement>('[data-row-input]')[i])?.focus());
	}
	function add() {
		items = [...items, ''];
		showAll = true;
		focusRow(items.length - 1);
	}
	function remove(i: number) {
		items = items.filter((_, j) => j !== i);
		queueMicrotask(() => {
			const els = rows?.querySelectorAll<HTMLElement>('[data-row-input]');
			(els?.[Math.min(i, (els?.length ?? 1) - 1)] ?? rows?.querySelector<HTMLElement>('button.add'))?.focus();
		});
	}
	function suggestFor(q: string) {
		clearTimeout(timer);
		if (!suggest || q.trim().length < 2) return (options = []);
		timer = setTimeout(async () => {
			try {
				const res = await fetch(`/api/card-autocomplete?q=${encodeURIComponent(q)}`);
				if (!res.ok) return;
				const body = await res.json();
				const list = Array.isArray(body) ? body : (body.results ?? body.names ?? []);
				options = list.map((o: unknown) => (typeof o === 'string' ? o : ((o as { name?: string }).name ?? ''))).filter(Boolean).slice(0, 10);
			} catch { /* autocomplete is optional */ }
		}, 200);
	}
</script>

<div class="lf" data-field={path}>
	<div class="lf__head">
		<span class="lf__label" id={`${uid}-l`}>{label}</span>
		{#if rollup}<span class="lf__rollup">{rollup}</span>{/if}
	</div>
	{#if hint}<a class="lf__hint" href="#proposals">{hint} ↓</a>{/if}
	<div bind:this={rows} role="group" aria-labelledby={`${uid}-l`}>
		{#each visible as { v, i } (i)}
			{@const b = badge(v)}
			{@const err = errors[v]}
			{@const nt = note?.(v)}
			<div class="lf__row" class:lf__row--err={!!err}>
				{#if multiline}
					<textarea
						name={path} rows="2" data-row-input id={`${uid}-i${i}`}
						aria-label={`${label} item ${i + 1}`} aria-invalid={err ? 'true' : undefined}
						aria-describedby={err ? `${uid}-e${i}` : undefined} bind:value={items[i]}
					></textarea>
				{:else}
					<input
						type="text" name={path} data-row-input id={`${uid}-i${i}`} autocomplete="off"
						list={suggest ? `${uid}-dl` : undefined}
						aria-label={`${label} item ${i + 1}`} aria-invalid={err ? 'true' : undefined}
						aria-describedby={err ? `${uid}-e${i}` : undefined} bind:value={items[i]}
						oninput={(e) => suggestFor(e.currentTarget.value)}
					/>
				{/if}
				<span class="lf__badge"><ProvenanceBadge kind={b.kind} prov={b.prov} /></span>
				{#if js}
					<button type="button" class="lf__remove" aria-label={`Remove ${v || 'item ' + (i + 1)} from ${label}`} onclick={() => remove(i)}>✕</button>
				{:else}
					<label class="lf__remove"><input type="checkbox" name={`${path}__remove`} value={i} /> Remove</label>
				{/if}
				{#if err}<p class="lf__err" id={`${uid}-e${i}`}>{err}</p>{:else if nt}<p class="lf__note">{nt}</p>{/if}
			</div>
		{/each}
		{#if !js}
			<div class="lf__row">
				<input type="text" name={path} class="lf__new" aria-label={`Add ${label}`} placeholder="Add…" autocomplete="off" />
			</div>
		{/if}
		{#if hidden > 0}
			<button type="button" class="btn btn--ghost" onclick={() => { showAll = true; focusRow(LIMIT); }}>Show all {items.length} {label.toLowerCase()}</button>
		{/if}
	</div>
	{#if js}<button type="button" class="btn btn--secondary add" onclick={add}>Add another</button>{/if}
	{#if suggest}<datalist id={`${uid}-dl`}>{#each options as o (o)}<option value={o}></option>{/each}</datalist>{/if}
</div>

<style>
	.lf { padding: var(--space-3) 0; border-top: 1px dotted var(--color-border); }
	.lf__head { display: flex; align-items: baseline; gap: var(--space-3); }
	.lf__label { color: var(--color-text); font-weight: var(--weight-medium); }
	.lf__rollup { margin-left: auto; font: var(--text-xs) var(--font-mono); color: var(--color-muted); }
	.lf__hint { font-size: var(--text-xs); color: var(--color-accent); }
	.lf__row { display: grid; grid-template-columns: minmax(0, 1fr) 9.5rem var(--touch-min); gap: var(--space-2); align-items: center; margin-top: var(--space-2); }
	.lf__row input[type='text'], .lf__row textarea { width: 100%; min-width: 0; }
	.lf__row--err input, .lf__row--err textarea { border-color: var(--color-danger); box-shadow: inset 3px 0 0 var(--color-danger); }
	.lf__remove { min-height: var(--touch-min); min-width: var(--touch-min); display: inline-flex; align-items: center; justify-content: center; background: transparent; border-color: transparent; color: var(--color-muted); }
	.lf__remove:hover { color: var(--color-danger); border-color: var(--color-danger-solid); }
	.lf__new { border-style: dashed; grid-column: 1 / -1; }
	.lf__err, .lf__note { grid-column: 1 / -1; margin: 0; font-size: var(--text-xs); }
	.lf__err { color: var(--color-danger); }
	.lf__note { color: var(--color-warning); }
	.add { margin-top: var(--space-2); }
	@media (max-width: 40rem) {
		.lf__row { grid-template-columns: minmax(0, 1fr) var(--touch-min); }
		.lf__badge { grid-column: 1 / -1; grid-row: 2; }
	}
</style>
