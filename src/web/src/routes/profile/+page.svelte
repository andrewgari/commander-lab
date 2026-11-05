<script lang="ts">
	import ArchetypeMixChart from '#lib/components/ArchetypeMixChart.svelte';
	import CurveChart from '#lib/components/CurveChart.svelte';
	import PowerPanel from '#lib/components/PowerPanel.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import ErrorBanner from '#lib/components/ErrorBanner.svelte';
	import SubmitButton from '#lib/components/SubmitButton.svelte';
	import TasteEditor from '#lib/components/profile/TasteEditor.svelte';
	import { createPending } from '#lib/forms/pending.svelte.ts';
	import { fieldLabel, type FieldError } from '#lib/profile/taste-form.ts';
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import type { PageProps } from './$types';

	let { data, form }: PageProps = $props();

	interface CurveMetrics {
		buckets: number[];
		spells: number;
		lands: number;
		avg_mv_nonland: number | null;
	}
	interface MixCategory {
		category: string;
		cards: number;
		share: number;
	}

	function asCurve(m: Record<string, unknown> | undefined): CurveMetrics | null {
		const c = m?.curve as Record<string, unknown> | undefined;
		if (!c || !Array.isArray(c.buckets)) return null;
		return {
			buckets: c.buckets.map((n) => Number(n) || 0),
			spells: Number(c.spells) || 0,
			lands: Number(c.lands) || 0,
			avg_mv_nonland: typeof c.avg_mv_nonland === 'number' ? c.avg_mv_nonland : null
		};
	}

	function asMix(m: Record<string, unknown> | undefined): MixCategory[] {
		const mix = m?.archetype_mix as Record<string, unknown> | undefined;
		if (!mix || !Array.isArray(mix.categories)) return [];
		return mix.categories.map((row) => {
			const r = row as Record<string, unknown>;
			return {
				category: String(r.category ?? '?'),
				cards: Number(r.cards) || 0,
				share: Number(r.share) || 0
			};
		});
	}

	function asPower(m: Record<string, unknown> | undefined) {
		return (m?.power as Record<string, unknown> | undefined) ?? null;
	}

	function activeOverrides(m: Record<string, unknown> | undefined): Record<string, unknown> {
		return (m?.overrides as Record<string, unknown> | undefined) ?? {};
	}

	function fmtTs(ts: string | null): string {
		if (!ts) return '';
		const d = new Date(ts);
		return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
	}

	const seed = createPending();
	const gen = createPending();
	const pendingByPath = $derived(
		(data.player?.pending_proposals ?? []).reduce<Record<string, number>>((m, pr) => {
			m[pr.path] = (m[pr.path] ?? 0) + 1;
			return m;
		}, {})
	);
	const serverErrors = $derived<FieldError[]>((form as { fieldErrors?: FieldError[] } | null)?.fieldErrors ?? []);
	const proposalError = $derived((form as { proposalError?: { id: number; message: string } } | null)?.proposalError ?? null);
	// Re-mount the editor whenever the saved profile changes so badges and the dirty counter reset.
	const editorKey = $derived(JSON.stringify([data.player?.profile, data.player?.provenance]));
	const proposalValue = (v: unknown) => (typeof v === 'string' ? v : JSON.stringify(v));

	const snapshot = $derived(data.latest.snapshot);
	const metrics = $derived(snapshot?.metrics);
	const curve = $derived(asCurve(metrics));
	const mix = $derived(asMix(metrics));
	const power = $derived(asPower(metrics));
	const steered = $derived(activeOverrides(metrics));

	// Override control descriptors: number keys get numeric inputs.
	const overrideKinds: Record<string, 'number' | 'string'> = {
		power_level_target: 'number',
		curve_avg_mv_target: 'number',
		archetype_focus: 'string'
	};
</script>

<svelte:head>
	<title>Profile — Commander Lab</title>
</svelte:head>

<nav><a href="/">← home</a></nav>

<h1>Profile</h1>

{#if data.error}
	<ErrorBanner message="Profile snapshots couldn't load" detail={data.error} onRetry={() => invalidateAll()} />
{:else}
	{#if !data.latest.available || !snapshot}
		<p class="unavailable">
			No profile yet{data.latest.reason ? `: ${data.latest.reason}` : ''}. Import a deck to
			compute the first snapshot.
		</p>
	{:else}
		<section class="summary">
			<h2>Summary</h2>
			<p class="summary-text">{snapshot.summary_text}</p>
			<p class="meta">
				snapshot #{snapshot.id} · {snapshot.trigger} · {fmtTs(snapshot.created_at)}
			</p>
		</section>

		<div class="charts">
			{#if curve}
				<section>
					<h2>Mana curve</h2>
					<CurveChart buckets={curve.buckets} lands={curve.lands} avgMv={curve.avg_mv_nonland} />
				</section>
			{/if}
			<section>
				<h2>Archetype mix</h2>
				<ArchetypeMixChart categories={mix} />
			</section>
			<section>
				<h2>Power level</h2>
				<PowerPanel {power} />
			</section>
		</div>

		{#if Object.keys(steered).length > 0}
			<section class="steered">
				<h2>Active steering</h2>
				<ul>
					{#each Object.entries(steered) as [key, value] (key)}
						<li>
							<strong>{data.overrides.known_keys[key] ?? key}</strong>: {String(value)}
						</li>
					{/each}
				</ul>
			</section>
		{/if}
	{/if}

	<section class="overrides">
		<h2>Overrides</h2>
		<p class="hint">
			Overrides steer the profile without rewriting computed metrics; every change is appended
			to the history below as its own snapshot.
		</p>
		{#if form?.error}
			<p class="error" role="alert">{form.error}</p>
		{/if}
		{#if form?.snapshotId}
			<p class="ok">Saved — snapshot #{form.snapshotId} appended to history.</p>
		{/if}
		{#each Object.entries(data.overrides.known_keys) as [key, label] (key)}
			{@const current = data.overrides.overrides[key]}
			<form method="post" action="?/override" class="override-row">
				<input type="hidden" name="key" value={key} />
				<input type="hidden" name="kind" value={overrideKinds[key] ?? 'string'} />
				<label>
					<span class="label">{label}</span>
					{#if (overrideKinds[key] ?? 'string') === 'number'}
						<input
							type="number"
							name="value"
							step="any"
							value={current?.value != null ? String(current.value) : ''}
							placeholder="unset"
						/>
					{:else}
						<input
							type="text"
							name="value"
							value={current?.value != null ? String(current.value) : ''}
							placeholder="unset"
						/>
					{/if}
				</label>
				<button type="submit">Save</button>
				{#if current?.value != null}
					<button type="submit" formaction="?/clear" class="clear">Clear</button>
				{/if}
				{#if current?.set_at}
					<span class="meta">set {fmtTs(current.set_at)}</span>
				{/if}
			</form>
		{/each}
	</section>

	<section class="history">
		<h2>History</h2>
		{#if data.history.length === 0}
			<p>No snapshots yet.</p>
		{:else}
			<ul class="history-list">
				{#each data.history as snap (snap.id)}
					<li>
						<details>
							<summary>
								<span class="trigger">{snap.trigger}</span>
								<span class="meta">#{snap.id} · {fmtTs(snap.created_at)}</span>
							</summary>
							<p class="summary-text">{snap.summary_text}</p>
							<details class="metrics">
								<summary>metrics</summary>
								<pre>{JSON.stringify(snap.metrics, null, 2)}</pre>
							</details>
						</details>
					</li>
				{/each}
			</ul>
		{/if}
	</section>
{/if}

<section class="player-profile">
	<h2>Taste profile</h2>
	<p class="hint">
		What the reviewer knows about you. Values you edit are <strong>stated</strong> and are never
		overwritten by learning. Deck-level intent beats this profile.
	</p>
	{#if form?.playerError && (data.player ? !serverErrors.length || form.playerError : true)}
		{#if !data.player || !serverErrors.length}
			<ErrorBanner message="Profile action failed" detail={form.playerError} class="mb" />
		{/if}
	{/if}
	{#if form?.playerOk && !form?.playerError}
		<p class="ok" role="status">{form.playerOk}</p>
	{/if}
	{#if !data.player && data.playerError && !/not.?found|404|no profile/i.test(data.playerError)}
		<ErrorBanner message="Taste profile couldn't load" detail={data.playerError} onRetry={() => invalidateAll()} />
	{:else if !data.player}
		<EmptyState variant="inline" title="No taste profile yet" description={data.playerError ?? 'Seed it from the profiles/andrew.json file, then edit it here.'}>
			{#snippet actions()}
				<form method="POST" action="?/seedPlayer" use:enhance={seed.enhance}>
					<SubmitButton pending={seed.submitting} pendingLabel="Seeding…" variant="primary">Seed from profiles/andrew.json</SubmitButton>
				</form>
			{/snippet}
		</EmptyState>
	{:else}
		{#key editorKey}
			<TasteEditor
				profile={data.player.profile}
				provenance={data.player.provenance}
				{serverErrors}
				formError={form?.playerError && serverErrors.length ? form.playerError : null}
				saved={form?.playerOk ?? null}
				{pendingByPath}
			/>
		{/key}

		<section id="proposals" aria-labelledby="proposals-h">
			<h3 id="proposals-h">Suggested from your feedback</h3>
			<form method="POST" action="?/generateProposals" use:enhance={gen.enhance}>
				<SubmitButton pending={gen.submitting} pendingLabel="Looking…">Look for patterns in my feedback</SubmitButton>
			</form>
			{#if data.player.pending_proposals.length === 0}
				<p class="hint">No pending suggestions.</p>
			{:else}
				<ul class="proposals">
					{#each data.player.pending_proposals as pr (pr.id)}
						<li class="proposal" tabindex="-1">
							<div>
								<span class="meta">{fieldLabel(pr.path.split('/')[0])}</span>
								<p><strong>{pr.op === 'add' ? 'Add' : 'Set'}</strong> <code>{proposalValue(pr.value)}</code></p>
								<p class="meta">{pr.rationale}</p>
								{#if proposalError?.id === pr.id}
									<ErrorBanner severity="warning" message={proposalError.message} />
								{/if}
							</div>
							<div class="proposal__actions">
								{#if proposalError?.id !== pr.id}
									<form method="POST" action="?/confirmProposal"><input type="hidden" name="id" value={pr.id} /><button type="submit" class="btn btn--primary">Confirm</button></form>
								{/if}
								<form method="POST" action="?/dismissProposal"><input type="hidden" name="id" value={pr.id} /><button type="submit" class="btn btn--secondary">Dismiss</button></form>
							</div>
						</li>
					{/each}
				</ul>
			{/if}
		</section>
	{/if}
</section>

<style>
	h1 {
		font-size: 1.5rem;
		margin-bottom: 0.5rem;
	}
	h2 {
		font-size: 1.05rem;
		margin-bottom: 0.35rem;
	}
	section {
		margin-bottom: 1.5rem;
	}
	.summary-text {
		font-style: italic;
		max-width: 60ch;
	}
	.meta {
		font-size: 0.8rem;
		color: var(--color-muted);
	}
	.charts {
		display: grid;
		gap: 1.25rem;
		grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
		align-items: start;
	}
	.hint {
		font-size: 0.8rem;
		color: var(--color-muted);
		max-width: 60ch;
	}
	.override-row {
		display: flex;
		gap: 0.5rem;
		align-items: center;
		flex-wrap: wrap;
		padding: 0.35rem 0;
	}
	.override-row label {
		display: flex;
		gap: 0.5rem;
		align-items: center;
	}
	.override-row .label {
		min-width: 16rem;
		font-size: 0.9rem;
	}
	.override-row input[type='number'],
	.override-row input[type='text'] {
		width: 10rem;
		padding: 0.3rem 0.45rem;
	}
	.clear {
		color: var(--color-muted);
	}
	.history-list {
		list-style: none;
		padding: 0;
		display: grid;
		gap: 0.4rem;
	}
	.trigger {
		display: inline-block;
		min-width: 5rem;
		font-weight: 600;
	}
	pre {
		font-size: 0.72rem;
		overflow: auto;
		max-height: 20rem;
	}
	.metrics {
		margin-top: 0.25rem;
	}
	.error {
		color: var(--color-danger);
	}
	.ok {
		color: var(--color-success);
	}
	.unavailable {
		color: var(--color-muted);
	}
	.proposals { list-style: none; padding: 0; display: grid; gap: var(--space-3); }
	.proposal { display: grid; grid-template-columns: 1fr auto; gap: var(--space-3); padding: var(--space-3) var(--space-4); background: var(--color-surface); border: 1px solid var(--color-border); border-left: 3px solid var(--color-info); }
	.proposal p { margin: var(--space-1) 0; }
	.proposal__actions { display: flex; flex-direction: column; gap: var(--space-2); }
	@media (max-width: 40rem) { .proposal { grid-template-columns: 1fr; } .proposal__actions { flex-direction: row; } }
	.steered ul {
		margin: 0;
		padding-left: 1.2rem;
	}
</style>
