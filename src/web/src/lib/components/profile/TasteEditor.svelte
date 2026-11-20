<script lang="ts">
	import { onMount } from 'svelte';
	import { enhance } from '$app/forms';
	import type { SubmitFunction } from '$app/forms';
	import { beforeNavigate } from '$app/navigation';
	import ErrorBanner from '#lib/components/ErrorBanner.svelte';
	import SubmitButton from '#lib/components/SubmitButton.svelte';
	import ListField from './ListField.svelte';
	import WeightField from './WeightField.svelte';
	import ProvenanceBadge from './ProvenanceBadge.svelte';
	import { createPending } from '#lib/forms/pending.svelte.ts';
	import {
		CURRENCIES, DEPTHS, TIERS, TONES, LIST_FIELDS, WEIGHT_FIELDS, profileToForm, formModelToFormData, formToValues,
		validate, fieldLabel, type FieldError, type FormModel, type Values
	} from '#lib/profile/taste-form.ts';
	import { badgeFor, leafEntries, rollup, type Badge, type Provenance } from '#lib/profile/provenance.ts';

	interface Props {
		profile: Values;
		provenance: Record<string, Provenance>;
		/** Server errors from the last failed save. */
		serverErrors?: FieldError[];
		formError?: string | null;
		saved?: string | null;
		/** Pending proposal counts per path. */
		pendingByPath?: Record<string, number>;
	}
	let { profile, provenance, serverErrors = [], formError = null, saved = null, pendingByPath = {} }: Props = $props();

	/* Props are the saved state; this component is re-mounted by the page when they change, so it seeds once. */
	// svelte-ignore state_referenced_locally
	let model = $state<FormModel>(profileToForm(profile));
	// svelte-ignore state_referenced_locally
	const original = profile;
	// svelte-ignore state_referenced_locally
	const savedLeaves = leafEntries(profile);
	let js = $state(false);
	let submitted = $state(false);
	let formEl: HTMLFormElement | undefined = $state();
	const p = createPending();

	onMount(() => (js = true));

	const current = $derived(formToValues(formModelToFormData(model, { name: String(original.name ?? '') }), original));
	const currentLeaves = $derived(leafEntries(current));
	const savedState = $derived({ leaves: savedLeaves, provenance });

	const dirtyCount = $derived.by(() => {
		const keys = new Set([...savedLeaves.keys(), ...currentLeaves.keys()]);
		let n = 0;
		for (const k of keys) if (savedLeaves.get(k) !== currentLeaves.get(k)) n++;
		return n;
	});
	const dirty = $derived(dirtyCount > 0 || JSON.stringify(current) !== JSON.stringify(original));

	const clientErrors = $derived(submitted ? validate(current) : []);
	const errors = $derived<FieldError[]>([...clientErrors, ...serverErrors]);
	const errAt = (path: string, item?: string) => errors.find((e) => e.path === path && (item === undefined ? true : e.item === item));
	function errMap(path: string): Record<string, string> {
		const m: Record<string, string> = {};
		for (const e of errors) if (e.path === path && e.item !== undefined) m[e.item] = e.message;
		return m;
	}
	const groupErrors = (path: string) => errors.filter((e) => e.path === path && e.item === undefined);

	function badge(key: string): Badge {
		return badgeFor(key, currentLeaves.get(key), savedState);
	}
	const itemBadge = (path: string) => (item: string) => badge(`${path}/${item.trim()}`);
	const weightBadge = (path: string) => (label: string) => badge(`${path}.${label}`);
	const rollupFor = (path: string, kind: 'list' | 'weight') => {
		const keys = [...currentLeaves.keys()].filter((k) => k.startsWith(path + (kind === 'list' ? '/' : '.')));
		return rollup(keys.map((k) => badge(k).kind));
	};
	const replaceNote = (key: string) =>
		badge(key).kind === 'unsaved' && provenance[key]?.source === 'learned_from_feedback'
			? 'Saving replaces a learned value with your own.'
			: null;
	const weightNote = (path: string) => (label: string) => {
		const k = `${path}.${label}`;
		return badge(k).kind === 'unsaved' && provenance[k]?.source === 'learned_from_feedback'
			? 'Saving replaces a learned value with your own.'
			: null;
	};
	const hintFor = (path: string) => {
		const n = pendingByPath[path] ?? 0;
		return n ? `${n} suggestion${n === 1 ? '' : 's'} pending` : null;
	};

	const GUILDS = ['W', 'U', 'B', 'R', 'G', 'C', 'Azorius', 'Dimir', 'Rakdos', 'Gruul', 'Selesnya', 'Orzhov', 'Izzet', 'Golgari', 'Boros', 'Simic', 'Bant', 'Esper', 'Grixis', 'Jund', 'Naya', 'Abzan', 'Jeskai', 'Sultai', 'Mardu', 'Temur'];

	const wrapEnhance: SubmitFunction = (input) => {
		submitted = true;
		const errs = validate(formToValues(input.formData, original));
		if (errs.length) {
			input.cancel();
			queueMicrotask(() => document.getElementById('taste-summary')?.focus());
			return;
		}
		type After = (o: { update: (opts?: { reset?: boolean; invalidateAll?: boolean }) => Promise<void> } & Record<string, unknown>) => void | Promise<void>;
		const done = p.enhance(input) as unknown as After | undefined;
		// A native form reset after success would blank the bound inputs; the editor is re-mounted from the saved profile instead.
		return done && ((o: Parameters<After>[0]) => done({ ...o, update: (opts) => o.update({ ...opts, reset: false }) }));
	};

	function goToField(e: MouseEvent, path: string, item?: string) {
		e.preventDefault();
		const box = formEl?.querySelector<HTMLElement>(`[data-field="${path}"]`);
		const target = box?.querySelector<HTMLElement>('[aria-invalid="true"]') ?? box?.querySelector<HTMLElement>('input,textarea') ?? document.getElementById(`f-${path}`);
		target?.focus();
	}
	function discard() {
		model = profileToForm(profile);
		submitted = false;
	}

	beforeNavigate((nav) => {
		if (dirty && !p.submitting && !confirm('Discard unsaved taste changes?')) nav.cancel();
	});
	function beforeUnload(e: BeforeUnloadEvent) {
		if (dirty) e.preventDefault();
	}
	const summaryErrors = $derived(errors);
</script>

<svelte:window onbeforeunload={beforeUnload} />

<form
	bind:this={formEl}
	method="POST"
	action="?/savePlayer"
	use:enhance={wrapEnhance}
	aria-busy={p.submitting}
	class="taste"
	novalidate
>
	<input type="hidden" name="name" value={String(original.name ?? '')} />
	<input type="hidden" name="metadata" value={JSON.stringify(original.metadata ?? {})} />

	{#if summaryErrors.length || formError}
		<div id="taste-summary" tabindex="-1">
			<ErrorBanner message={summaryErrors.length ? `Fix ${summaryErrors.length} ${summaryErrors.length === 1 ? 'field' : 'fields'} before saving` : 'Your profile could not be saved'} detail={formError ?? undefined}>
				{#if summaryErrors.length}
					<ul class="summary">
						{#each summaryErrors as e, i (i)}
							<li><a href={`#f-${e.path}`} onclick={(ev) => goToField(ev, e.path, e.item)}>{fieldLabel(e.path)}</a>: {e.message}</li>
						{/each}
					</ul>
					{#if formError}<p>{formError}</p>{/if}
				{/if}
			</ErrorBanner>
		</div>
	{/if}
	{#if p.transientError}
		<ErrorBanner message="Couldn't save your profile" detail={p.transientError} onRetry={() => formEl?.requestSubmit()} retryLabel="Try again: save" class="mb" />
	{/if}
	{#if saved && !dirty}
		<p class="saved" role="status">{saved}</p>
	{/if}

	{#snippet head(path: string, label: string)}
		<div class="sf__head">
			<label for={`f-${path}`}>{label}</label>
			<ProvenanceBadge kind={badge(path).kind} prov={badge(path).prov} />
		</div>
	{/snippet}
	{#snippet msg(path: string)}
		{@const e = errAt(path)}
		{#if e}<p class="sf__err" id={`e-${path}`}>{e.message}</p>
		{:else if replaceNote(path)}<p class="sf__note">{replaceNote(path)}</p>{/if}
		{#if hintFor(path)}<a class="sf__hint" href="#proposals">{hintFor(path)} ↓</a>{/if}
	{/snippet}
	{#snippet select(path: string, label: string, opts: readonly string[], allowNone: boolean)}
		<div class="sf">
			{@render head(path, label)}
			<select id={`f-${path}`} name={path} bind:value={model.text[path]} aria-invalid={errAt(path) ? 'true' : undefined} aria-describedby={errAt(path) ? `e-${path}` : undefined}>
				{#if allowNone}<option value="">Not set</option>{/if}
				{#each opts as o (o)}<option value={o}>{o[0].toUpperCase() + o.slice(1)}</option>{/each}
			</select>
			{@render msg(path)}
		</div>
	{/snippet}
	{#snippet money(path: string, label: string)}
		<div class="sf">
			{@render head(path, label)}
			<input id={`f-${path}`} name={path} type="number" min="0" step="0.01" inputmode="decimal" placeholder="Not set" bind:value={model.text[path]}
				aria-invalid={errAt(path) ? 'true' : undefined} aria-describedby={errAt(path) ? `e-${path}` : undefined} />
			{@render msg(path)}
		</div>
	{/snippet}
	{#snippet lists(paths: string[])}
		{#each LIST_FIELDS.filter((f) => paths.includes(f.path)) as f (f.path)}
			<div id={`f-${f.path}`}>
				<ListField path={f.path} label={f.label} bind:items={model.lists[f.path]} badge={itemBadge(f.path)} rollup={rollupFor(f.path, 'list')}
					errors={errMap(f.path)} multiline={f.multiline} suggest={f.card} hint={hintFor(f.path)} {js} />
				{#each groupErrors(f.path) as e, i (i)}<p class="sf__err">{e.message}</p>{/each}
			</div>
		{/each}
	{/snippet}
	{#snippet weights(path: string)}
		{#each WEIGHT_FIELDS.filter((f) => f.path === path) as f (f.path)}
			<div id={`f-${f.path}`}>
				<WeightField path={f.path} label={f.label} bind:entries={model.weights[f.path]} badge={weightBadge(f.path)} rollup={rollupFor(f.path, 'weight')}
					errors={errMap(f.path)} note={weightNote(f.path)} suggestions={path === 'color_affinities' ? GUILDS : []} {js} />
				{#each groupErrors(f.path) as e, i (i)}<p class="sf__err">{e.message}</p>{/each}
			</div>
		{/each}
	{/snippet}

	<div class="groups">
		<fieldset class="span-2">
			<legend>Identity</legend>
			<div class="sf">
				{@render head('builder_identity.text', 'How you build')}
				<textarea id="f-builder_identity.text" name="builder_identity.text" rows="4" bind:value={model.text['builder_identity.text']}></textarea>
				{@render msg('builder_identity.text')}
			</div>
			{@render lists(['builder_identity.tags'])}
		</fieldset>

		<fieldset>
			<legend>Affinities</legend>
			{@render weights('color_affinities')}
			{@render weights('archetype_affinities')}
		</fieldset>

		<fieldset>
			<legend>Budget</legend>
			{@render money('budget_norms.hard_cap', 'Hard cap')}
			{@render money('budget_norms.soft_cap', 'Soft cap')}
			{@render select('budget_norms.currency', 'Currency', CURRENCIES, false)}
		</fieldset>

		<fieldset>
			<legend>Likes</legend>
			{@render lists(['favored_mechanics', 'pet_cards'])}
		</fieldset>

		<fieldset>
			<legend>Reviews</legend>
			{@render select('review_preferences.depth', 'Review depth', DEPTHS, true)}
			{@render select('review_preferences.tone', 'Tone', TONES, true)}
			{@render lists(['review_preferences.skip'])}
			<fieldset class="sf radios">
				<legend>Generic staples welcome?</legend>
				<ProvenanceBadge kind={badge('review_preferences.welcome_generic_staples').kind} prov={badge('review_preferences.welcome_generic_staples').prov} />
				{#each [['', 'Not set'], ['yes', 'Yes'], ['no', 'No']] as [v, l] (v)}
					<label><input type="radio" name="welcome_generic_staples" value={v} bind:group={model.staples} /> {l}</label>
				{/each}
			</fieldset>
		</fieldset>

		<fieldset class="span-2">
			<legend>Dislikes</legend>
			{@render lists(['dislikes.cards', 'dislikes.strategies', 'dislikes.play_patterns'])}
		</fieldset>

		<fieldset>
			<legend>Power</legend>
			{@render select('power_tendencies.tier', 'Power tier', TIERS, true)}
			<div class="sf">
				{@render head('power_tendencies.notes', 'Power notes')}
				<textarea id="f-power_tendencies.notes" name="power_tendencies.notes" rows="2" bind:value={model.text['power_tendencies.notes']}></textarea>
				{@render msg('power_tendencies.notes')}
			</div>
		</fieldset>

		<fieldset class="span-2">
			<legend>Goals</legend>
			{@render lists(['done_when'])}
		</fieldset>
	</div>

	<div class="savebar" class:savebar--dirty={dirty}>
		<span class="dirty" role="status">
			{#if dirty}<span class="dot" aria-hidden="true"></span>{dirtyCount || 1} unsaved {dirtyCount === 1 ? 'change' : 'changes'}{:else}All changes saved{/if}
		</span>
		<button type="button" class="btn btn--ghost" onclick={discard} disabled={!dirty}>Discard changes</button>
		<SubmitButton pending={p.submitting} pendingLabel="Saving…" variant="primary">Save changes</SubmitButton>
	</div>
</form>

<style>
	.groups { display: grid; gap: var(--space-4); grid-template-columns: 1fr; align-items: start; padding-bottom: var(--footer-h, 4rem); }
	@media (min-width: 72rem) { .groups { grid-template-columns: 1fr 1fr; } .span-2 { grid-column: 1 / -1; } }
	fieldset { min-width: 0; margin: 0; padding: var(--space-4) var(--space-5) var(--space-5); background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-sm); }
	legend { float: left; width: 100%; padding: 0 0 var(--space-2); margin-bottom: var(--space-2); border-bottom: 1px solid var(--color-border); font: var(--text-xs) var(--font-mono); text-transform: uppercase; letter-spacing: var(--tracking-label); color: var(--color-accent); }
	legend + * { clear: both; }
	.sf { padding: var(--space-3) 0; display: grid; gap: var(--space-2); }
	.sf__head { display: flex; align-items: center; gap: var(--space-3); }
	.sf__head label { color: var(--color-text); font-weight: var(--weight-medium); }
	.sf input, .sf select, .sf textarea { width: 100%; max-width: 100%; }
	.sf__err { margin: 0; font-size: var(--text-xs); color: var(--color-danger); }
	.sf__note { margin: 0; font-size: var(--text-xs); color: var(--color-warning); }
	.sf__hint { font-size: var(--text-xs); color: var(--color-accent); }
	.radios { border: 0; background: transparent; padding: var(--space-3) 0; display: flex; flex-wrap: wrap; gap: var(--space-3); align-items: center; }
	.radios legend { float: none; width: auto; border: 0; font: inherit; text-transform: none; letter-spacing: 0; color: var(--color-text); }
	.radios label { display: inline-flex; align-items: center; gap: var(--space-2); min-height: var(--touch-min); }
	.radios input { width: auto; min-height: 0; }
	.summary { margin: var(--space-2) 0 0; padding-left: var(--space-4); }
	.saved { color: var(--color-success); }
	.savebar { position: sticky; bottom: 0; z-index: 5; display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-3); min-height: var(--footer-h, 4rem); padding: var(--space-2) var(--space-4); background: var(--color-surface); border-top: 1px solid var(--color-border); }
	.savebar--dirty { border-top: 2px solid var(--color-accent); }
	.dirty { margin-right: auto; font: var(--text-xs) var(--font-mono); color: var(--color-muted); display: inline-flex; align-items: center; gap: var(--space-2); }
	.savebar--dirty .dirty { color: var(--color-text); }
	.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--color-accent); }
	:global(.mb) { margin-bottom: var(--space-3); }
	@media (max-width: 40rem) { .savebar :global(.btn--primary) { flex: 1 1 100%; } }
</style>
