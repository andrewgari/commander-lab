<script lang="ts">
	import Icon from '#lib/components/Icon.svelte';
	import type { BadgeKind, Provenance } from '#lib/profile/provenance.ts';

	let { kind, prov, id }: { kind: BadgeKind; prov?: Provenance; id?: string } = $props();

	const day = (iso?: string) => (iso ? iso.slice(0, 10) : '');
	const text = $derived(kind === 'learned' ? 'Learned' : kind === 'unsaved' ? 'Stated · unsaved' : 'Stated');
	const title = $derived(
		kind === 'learned'
			? `Learned from your review feedback on ${day(prov?.at)}`
			: kind === 'stated'
				? `You set this on ${day(prov?.at)}`
				: 'Saving records this as stated'
	);
	let open = $state(false);
	const uid = $props.id();
</script>

{#if kind}
	{#if kind === 'learned'}
		<button
			type="button"
			class="pb pb--learned"
			data-badge="learned"
			aria-expanded={open}
			aria-controls={`${uid}-ev`}
			{title}
			onclick={() => (open = !open)}
		>
			<Icon name="info" size="12px" />{text}<span aria-hidden="true">{open ? '▴' : '▾'}</span>
		</button>
		{#if open}
			<div class="pb__evidence" id={`${uid}-ev`}>
				<p>{title}</p>
				{#if prov?.evidence?.length}
					<ul>
						{#each prov.evidence as e, i (i)}<li>{e}</li>{/each}
					</ul>
				{/if}
			</div>
		{/if}
	{:else}
		<span class="pb" class:pb--unsaved={kind === 'unsaved'} data-badge={kind} {title}>
			<Icon name="check" size="12px" />{text}
		</span>
	{/if}
{/if}

<style>
	.pb {
		display: inline-flex; align-items: center; gap: 0.25rem; min-height: 1.25rem; padding: 0 var(--space-2);
		font: var(--weight-bold) 11px / 1 var(--font-mono); text-transform: uppercase; white-space: nowrap;
		color: var(--color-accent); border: 1px solid currentColor; border-radius: var(--radius-sm); background: transparent;
	}
	.pb--unsaved { border-style: dashed; background: var(--color-accent-bg, #3b2a12); }
	.pb--learned { color: var(--color-info); background: var(--color-info-bg); cursor: pointer; }
	.pb--learned:hover { border-color: var(--color-info); }
	.pb__evidence {
		grid-column: 1 / -1; padding: var(--space-2) var(--space-3); background: var(--color-info-bg);
		border-left: 2px solid var(--color-info); color: var(--color-text); font-size: var(--text-xs);
	}
	.pb__evidence p { margin: 0 0 var(--space-1); }
	.pb__evidence ul { margin: 0; padding-left: var(--space-4); }
</style>
