<script lang="ts">
	import ArchetypeMixChart from '#lib/components/ArchetypeMixChart.svelte';
	import CurveChart from '#lib/components/CurveChart.svelte';
	import PowerPanel from '#lib/components/PowerPanel.svelte';
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
	<p class="error" role="alert">Profile unavailable: {data.error}</p>
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
	{#if form?.playerError}
		<p class="error" role="alert">{form.playerError}</p>
	{/if}
	{#if form?.playerOk}
		<p class="ok">{form.playerOk}</p>
	{/if}
	{#if !data.player}
		<p class="unavailable">Not available: {data.playerError ?? 'no profile yet'}</p>
		<form method="POST" action="?/seedPlayer">
			<button type="submit">Seed from profiles/andrew.json</button>
		</form>
	{:else}
		<form method="POST" action="?/savePlayer">
			<label for="player-json">Profile values (JSON)</label>
			<textarea id="player-json" name="values" rows="18" spellcheck="false"
				>{JSON.stringify(data.player.profile, null, 2)}</textarea
			>
			<button type="submit">Save as stated</button>
		</form>

		<h3>Provenance</h3>
		<ul class="prov">
			{#each Object.entries(data.player.provenance) as [key, p] (key)}
				<li><code>{key}</code> — {p.source} · {fmtTs(p.at)}</li>
			{/each}
		</ul>

		<h3>Proposed from your feedback</h3>
		<form method="POST" action="?/generateProposals">
			<button type="submit">Look for patterns in my feedback</button>
		</form>
		{#if data.player.pending_proposals.length === 0}
			<p class="hint">No pending proposals.</p>
		{:else}
			<ul class="proposals">
				{#each data.player.pending_proposals as pr (pr.id)}
					<li>
						<strong>{pr.op === 'add' ? 'Add' : 'Set'} {pr.path}:</strong>
						{JSON.stringify(pr.value)}
						<span class="meta">{pr.rationale}</span>
						<form method="POST" action="?/confirmProposal" class="inline">
							<input type="hidden" name="id" value={pr.id} />
							<button type="submit">Confirm</button>
						</form>
						<form method="POST" action="?/dismissProposal" class="inline">
							<input type="hidden" name="id" value={pr.id} />
							<button type="submit" class="clear">Dismiss</button>
						</form>
					</li>
				{/each}
			</ul>
		{/if}
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
	textarea {
		width: 100%;
		font-family: var(--font-mono);
		font-size: 0.8rem;
	}
	.inline {
		display: inline-block;
		margin-left: 0.5rem;
	}
	.steered ul {
		margin: 0;
		padding-left: 1.2rem;
	}
</style>
