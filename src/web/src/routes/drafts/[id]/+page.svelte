<script lang="ts">
	import type { ActionData, PageProps } from './$types';

	let { data, form }: PageProps & { form: ActionData } = $props();

	const draft = $derived(data.state.draft);
	const open = $derived(draft.status === 'open');

	function confirmSubmit(message: string) {
		return (event: SubmitEvent) => {
			if (!window.confirm(message)) event.preventDefault();
		};
	}
</script>

<svelte:head>
	<title>{draft.name} — Draft — Commander Lab</title>
</svelte:head>

<nav><a href="/drafts">← drafts</a></nav>
<h1>{draft.name}</h1>
<p class="meta">
	{draft.kind} · {#each draft.source_decks as src, i}{i > 0 ? ' + ' : ''}{src.name}{/each}
	→ <strong>{draft.target_name}</strong> ·
	<span class:open class:committed={draft.status === 'committed'}>{draft.status}</span>
	{#if draft.result_deck_id}· built <code>{draft.result_deck_id}</code>{/if}
</p>
{#if draft.notes}<p class="meta">{draft.notes}</p>{/if}

{#if form?.actionError}<p class="error">{form.actionError}</p>{/if}

{#if open}
	<section class="danger-zone">
		<form method="POST" action="?/commit" onsubmit={confirmSubmit('Commit this draft? The new deck, card rows and physical-copy moves are applied in one transaction.')}>
			<button type="submit" class="commit">Commit draft</button>
		</form>
		<form method="POST" action="?/abandon" onsubmit={confirmSubmit('Abandon this draft? All staged rows are deleted — zero residue.')}>
			<button type="submit" class="abandon">Abandon</button>
		</form>
	</section>
{/if}

<section>
	<h2>Staged list ({data.state.cards.reduce((n, c) => n + c.qty, 0)} cards)</h2>
	{#if data.state.cards.length === 0}
		<p class="meta">Nothing staged yet.</p>
	{:else}
		<table>
			<thead>
				<tr><th>Qty</th><th>Card</th><th>Board</th><th>Type</th><th>CMC</th>{#if open}<th></th>{/if}</tr>
			</thead>
			<tbody>
				{#each data.state.cards as card (card.oracle_id + card.board)}
					<tr>
						<td>{card.qty}</td>
						<td>{card.name}</td>
						<td>{card.board}{#if card.category} · {card.category}{/if}</td>
						<td class="meta">{card.type_line}</td>
						<td>{card.cmc}</td>
						{#if open}
							<td>
								<form method="POST" action="?/remove_card">
									<input type="hidden" name="oracle_id" value={card.oracle_id} />
									<input type="hidden" name="board" value={card.board} />
									<button type="submit" class="link">remove</button>
								</form>
							</td>
						{/if}
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
	{#if open}
		<form method="POST" action="?/upsert_card" class="inline">
			<input name="oracle_id" required placeholder="oracle id" />
			<input name="name" required placeholder="card name" />
			<input name="qty" type="number" min="1" value="1" />
			<select name="board">
				<option value="main">main</option>
				<option value="commander">commander</option>
				<option value="sideboard">sideboard</option>
				<option value="maybeboard">maybeboard</option>
			</select>
			<button type="submit">Add / update card</button>
		</form>
	{/if}
</section>

<section>
	<h2>Physical-copy moves ({data.state.moves.length})</h2>
	{#if data.state.moves.length === 0}
		<p class="meta">No copy moves staged. Moves are applied to card instances only on Commit, with provenance events.</p>
	{:else}
		<table>
			<thead>
				<tr><th>Instance</th><th>Card</th><th>Action</th><th>From deck</th>{#if open}<th></th>{/if}</tr>
			</thead>
			<tbody>
				{#each data.state.moves as move (move.instance_id)}
					<tr>
						<td><code>{move.instance_id.slice(0, 8)}…</code></td>
						<td>{move.card_name}</td>
						<td>{move.action}</td>
						<td>{move.from_deck ?? '—'}</td>
						{#if open}
							<td>
								<form method="POST" action="?/unstage_move">
									<input type="hidden" name="instance_id" value={move.instance_id} />
									<button type="submit" class="link">unstage</button>
								</form>
							</td>
						{/if}
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
	{#if open}
		<form method="POST" action="?/stage_move" class="inline">
			<input name="instance_id" required placeholder="instance id" />
			<select name="move_action">
				<option value="assign">assign into result deck</option>
				<option value="unassign">unassign back to collection</option>
			</select>
			<button type="submit">Stage move</button>
		</form>
	{/if}
</section>

<section>
	<h2>Suggestions</h2>
	{#if data.suggestionsError}
		<p class="error">Suggestions unavailable: {data.suggestionsError}</p>
	{:else if data.suggestions}
		{@const s = data.suggestions}
		{#if Object.keys(s.inputs_unavailable).length > 0}
			<p class="meta">Scoring inputs not yet available (reported, never faked):</p>
			<ul class="meta">
				{#each Object.entries(s.inputs_unavailable) as [k, v] (k)}
					<li><strong>{k}</strong>: {v}</li>
				{/each}
			</ul>
		{/if}
		<h3>Adds (own-first, donor-no-worse)</h3>
		{#if s.adds.length === 0}
			<p class="meta">No add suggestions.</p>
		{:else}
			<table>
				<thead><tr><th>Card</th><th>Tier</th><th>Owned avail.</th><th>Donor pull</th><th>Why</th></tr></thead>
				<tbody>
					{#each s.adds as add (add.oracle_id)}
						<tr>
							<td>{add.name}</td>
							<td>{add.tier}</td>
							<td>{add.qty_available}</td>
							<td>
								{#if add.donor}
									from <code>{add.donor.deck_id}</code> ← {add.donor.replacement_name}
								{:else}
									—
								{/if}
							</td>
							<td class="meta">{add.reasons.join('; ')}</td>
						</tr>
					{/each}
				</tbody>
			</table>
			{#if s.hidden_donor_pulls > 0}
				<p class="meta">{s.hidden_donor_pulls} donor pull(s) hidden — {s.hidden_reason}</p>
			{/if}
		{/if}
		<h3>Cuts (weak performers)</h3>
		{#if s.cuts.length === 0}
			<p class="meta">No cut suggestions.</p>
		{:else}
			<table>
				<thead><tr><th>Card</th><th>Board</th><th>Weak score</th><th>Why</th></tr></thead>
				<tbody>
					{#each s.cuts as cut (cut.oracle_id)}
						<tr>
							<td>{cut.name}</td>
							<td>{cut.board ?? '—'}</td>
							<td>{cut.weak_score.toFixed(2)}</td>
							<td class="meta">{cut.reasons.join('; ')}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
		<p class="meta">{s.note}</p>
	{/if}
</section>

<style>
	h1 {
		font-size: 1.5rem;
	}
	table {
		border-collapse: collapse;
		font-size: 0.85rem;
		margin-bottom: 0.75rem;
	}
	th,
	td {
		text-align: left;
		padding: 0.2rem 0.6rem 0.2rem 0;
		border-bottom: 1px solid rgba(127, 127, 127, 0.25);
	}
	form.inline {
		display: flex;
		gap: 0.4rem;
		flex-wrap: wrap;
		align-items: center;
	}
	form.inline input[name='qty'] {
		width: 4rem;
	}
	button.link {
		background: none;
		border: none;
		padding: 0;
		color: var(--color-danger);
		cursor: pointer;
		font-size: inherit;
		text-decoration: underline;
	}
	.danger-zone {
		display: flex;
		gap: 0.6rem;
		margin: 0.75rem 0;
	}
	button.commit {
		padding: 0.45rem 1.1rem;
		background: var(--color-success-solid);
		color: var(--color-on-solid);
		border: none;
		cursor: pointer;
	}
	button.abandon {
		padding: 0.45rem 1.1rem;
		background: var(--color-danger-solid);
		color: var(--color-on-solid);
		border: none;
		cursor: pointer;
	}
	.meta {
		font-size: 0.85rem;
		opacity: 0.75;
	}
	.open {
		color: var(--color-warning);
	}
	.committed {
		color: var(--color-success);
	}
	.error {
		color: var(--color-danger);
	}
</style>
