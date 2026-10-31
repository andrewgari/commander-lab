<script lang="ts">
	import type { ActionData, PageProps } from './$types';
	import TableScroll from '#lib/components/TableScroll.svelte';
	import ScrollTable, { type Column } from '#lib/components/ScrollTable.svelte';
	import StatusPill from '#lib/components/StatusPill.svelte';
	import ErrorBanner from '#lib/components/ErrorBanner.svelte';
	import { invalidateAll } from '$app/navigation';
	import { draftStatus } from '#lib/status.ts';
	import { deckName, moveRows, summarize, abandonSentence } from '#lib/drafts/summary.ts';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import SubmitButton from '#lib/components/SubmitButton.svelte';
	import QtyEditor from '#lib/components/drafts/QtyEditor.svelte';
	import { createPending } from '#lib/forms/pending.svelte.ts';
	import { enhance } from '$app/forms';
	import QuickAdd from '#lib/components/card-search/QuickAdd.svelte';
	import CopyPicker from '#lib/components/combobox/CopyPicker.svelte';
	import type { ComboOption } from '#lib/components/combobox/model.ts';
	import { deserialize } from '$app/forms';
	import { boardLabel, groupByBoard, nextQty, stagedQty, stagedQtyByOracle, toApiBoard } from '#lib/drafts/boards.ts';
	import type { AddRequest, Board } from '#lib/card-search/types.ts';
	import CommitDialog from '#lib/components/drafts/CommitDialog.svelte';
	import AbandonDialog from '#lib/components/drafts/AbandonDialog.svelte';
	import CommitSummaryView from '#lib/components/drafts/CommitSummary.svelte';

	let { data, form }: PageProps & { form: ActionData } = $props();

	const draft = $derived(data.state.draft);
	const open = $derived(draft.status === 'open');
	const names = $derived(data.deckNames ? new Map(Object.entries(data.deckNames)) : null);
	const rows = $derived(moveRows(data.state.moves, names));
	const moveColumns: Column[] = [
		{ key: 'cardName', header: 'Card' },
		{ key: 'moveLabel', header: 'Move' },
		{ key: 'from', header: 'From' },
		{ key: 'state', header: 'Now' },
		{ key: 'actions', header: 'Actions', headerHidden: true }
	];
	const addColumns: Column[] = [
		{ key: 'name', header: 'Card' },
		{ key: 'tier', header: 'Tier' },
		{ key: 'qty_available', header: 'Owned avail.' },
		{ key: 'donor', header: 'Donor pull' },
		{ key: 'why', header: 'Why' }
	];
	const cutColumns: Column[] = [
		{ key: 'name', header: 'Card' },
		{ key: 'board', header: 'Board' },
		{ key: 'weak', header: 'Weak score' },
		{ key: 'why', header: 'Why' }
	];

	const BOARDS: { value: Board; label: string }[] = [
		{ value: 'main', label: 'Main' },
		{ value: 'commander', label: 'Commander' },
		{ value: 'sideboard', label: 'Sideboard' },
		{ value: 'maybeboard', label: 'Maybeboard' },
		{ value: 'considering', label: 'Considering' }
	];
	const inList = $derived(stagedQtyByOracle(data.state.cards));

	/** Stage a card from the combobox. Upsert SETS qty, so send existing + requested. */
	async function addToDraft({ card, qty, board }: AddRequest): Promise<string> {
		const apiBoard = toApiBoard(board);
		if (!apiBoard) throw new Error(`Unknown board “${board}”`);
		const existing = stagedQty(data.state.cards, card.oracle_id, apiBoard);
		const newQty = nextQty(existing, qty);
		const body = new FormData();
		body.set('oracle_id', card.oracle_id);
		body.set('name', card.name);
		body.set('qty', String(newQty));
		body.set('board', apiBoard);
		const res = await fetch('?/upsert_card', { method: 'POST', body, headers: { 'x-sveltekit-action': 'true' } });
		const result = deserialize(await res.text());
		if (result.type === 'failure') {
			throw new Error(String((result.data as { actionError?: string } | undefined)?.actionError ?? 'The server rejected the card.'));
		}
		if (result.type === 'error') throw new Error('Network or server error. Try again.');
		await invalidateAll();
		return `Added ${qty}× ${card.name} to ${boardLabel(apiBoard)}${existing > 0 ? ` (now ${newQty})` : ''}`;
	}

	const groups = $derived(groupByBoard(data.state.cards));
	let rowMessage = $state('');
	const removing = createPending({ key: (fd) => String(fd.get('oracle_id')) + String(fd.get('board')) });

	let pickedCopy = $state<ComboOption<string> | null>(null);
	let moveAction = $state('assign');

	const summary = $derived(summarize({ draft, cards: data.state.cards, moves: data.state.moves }, names));
	const cardCount = $derived(data.state.cards.reduce((n, c) => n + c.qty, 0));
	let commitDialog: CommitDialog | undefined = $state();
	let abandonDialog: AbandonDialog | undefined = $state();

	function openCommit(e: MouseEvent) {
		e.preventDefault();
		if (summary.blockedReason) return;
		commitDialog?.show(e.currentTarget as HTMLElement);
	}
	function openAbandon(e: MouseEvent) {
		e.preventDefault();
		abandonDialog?.show(e.currentTarget as HTMLElement);
	}
</script>

<svelte:head>
	<title>{draft.name} — Draft — Commander Lab</title>
</svelte:head>

<nav><a href="/drafts">← drafts</a></nav>
<h1>{draft.name}</h1>
<p class="meta">
	{draft.kind}: {#each draft.source_decks as src, i}{i > 0 ? ' + ' : ''}{src.name}{/each}
	→ <strong>{draft.target_name}</strong> ·
	<StatusPill status={draftStatus(draft.status)} label={draft.status} />
	{#if draft.result_deck_id}· Built deck: <a href={`/decks/${draft.result_deck_id}`}>{draft.target_name}</a>{/if}
</p>
{#if data.deckNames === null}<p class="meta" role="status">Deck names unavailable</p>{/if}
{#if draft.notes}<p class="meta">{draft.notes}</p>{/if}

{#if form?.actionError}<p class="error" role="alert">{form.actionError}</p>{/if}

{#if open && form?.needsConfirm === 'commit'}
	<section class="confirm-inline" aria-labelledby="confirm-h">
		<h2 id="confirm-h">Commit draft?</h2>
		<CommitSummaryView {summary} namesMissing={data.deckNames === null} />
		<form method="POST" action="?/commit">
			<input type="hidden" name="confirmed" value="1" />
			<button type="submit" class="btn btn--primary">Commit draft</button>
			<a href={`/drafts/${encodeURIComponent(draft.id)}`}>Cancel</a>
		</form>
	</section>
{:else if open && form?.needsConfirm === 'abandon'}
	<section class="confirm-inline" aria-labelledby="confirm-h">
		<h2 id="confirm-h">Abandon draft?</h2>
		<p>{abandonSentence(data.state.cards, data.state.moves)}</p>
		<form method="POST" action="?/abandon">
			<input type="hidden" name="confirmed" value="1" />
			<button type="submit" class="btn btn--danger">Abandon draft</button>
			<a href={`/drafts/${encodeURIComponent(draft.id)}`}>Cancel</a>
		</form>
	</section>
{/if}

{#if open}
	<section aria-labelledby="add-h">
		<h2 id="add-h">Add cards</h2>
		<QuickAdd boards={BOARDS} {inList} onadd={addToDraft} />
		<noscript>
			<form method="POST" action="?/upsert_card_by_name" class="inline">
				<label>Card name <input name="card_name" required /></label>
				<label>Quantity <input name="qty" type="number" min="1" value="1" /></label>
				<label>Board
					<select name="board">{#each BOARDS as b (b.value)}<option value={toApiBoard(b.value)}>{b.label}</option>{/each}</select>
				</label>
				<button type="submit">Add card</button>
			</form>
		</noscript>
	</section>
{/if}

<section>
	<h2>Staged list ({data.state.cards.reduce((n, c) => n + c.qty, 0)} cards)</h2>
	{#if data.state.cards.length === 0}
		<EmptyState variant="inline" title="Nothing staged yet" description="Search your collection above to add cards." />
	{:else}
		<TableScroll label="Staged list" minWidth="28rem">
		<table>
			<caption class="visually-hidden">Staged cards by board</caption>
			<thead>
				<tr>
					<th scope="col">Card</th>
					<th scope="col" class="col-type">Type</th>
					<th scope="col">MV</th>
					<th scope="col">Qty</th>
					{#if open}<th scope="col"><span class="visually-hidden">Remove</span></th>{/if}
				</tr>
			</thead>
			{#each groups as group (group.board)}
				<tbody>
					<tr class="group-row">
						<th scope="colgroup" colspan={open ? 5 : 4}>{group.label} {group.qty}</th>
					</tr>
					{#each group.rows as card (card.oracle_id + card.board)}
						<tr>
							<td><a href={`/card/${card.oracle_id}`}>{card.name}</a></td>
							<td class="meta col-type">{card.type_line}</td>
							<td>{card.cmc}</td>
							<td class="qty-cell">
								{#if open}
									<QtyEditor oracleId={card.oracle_id} board={card.board} cardName={card.name} boardLabel={group.label} qty={card.qty} onsaved={(m) => (rowMessage = m)} />
								{:else}
									{card.qty}
								{/if}
							</td>
							{#if open}
								<td>
									<form method="POST" action="?/remove_card" use:enhance={removing.enhance}>
										<input type="hidden" name="oracle_id" value={card.oracle_id} />
										<input type="hidden" name="board" value={card.board} />
										<SubmitButton variant="link-danger" pending={removing.submitting} busy={removing.key === card.oracle_id + card.board} pendingLabel="Removing…" aria-label={`Remove ${card.name} from ${group.label}`}>Remove</SubmitButton>
									</form>
								</td>
							{/if}
						</tr>
					{/each}
				</tbody>
			{/each}
		</table>
		</TableScroll>
		<p class="visually-hidden" role="status">{rowMessage}</p>
	{/if}
</section>

<section>
	<h2>Physical-copy moves ({data.state.moves.length})</h2>
	<ScrollTable caption="Staged physical-copy moves" captionHidden columns={moveColumns} {rows} rowKey={(r) => r.instanceId} minWidth="32rem">
		{#snippet cell(row, column)}
			{#if column.key === 'actions'}
				{#if open}
					<form method="POST" action="?/unstage_move">
						<input type="hidden" name="instance_id" value={row.instanceId} />
						<button type="submit" class="link" aria-label={`Unstage ${row.displayName} from ${row.from}`}>unstage</button>
					</form>
				{/if}
			{:else if column.key === 'cardName'}
				{row.displayName}
			{:else}
				{row[column.key as 'moveLabel' | 'from' | 'state']}
			{/if}
		{/snippet}
		{#snippet empty()}
			<p class="meta">No copy moves staged. Commit without moves creates a virtual deck.</p>
		{/snippet}
	</ScrollTable>
	{#if open}
		<form method="POST" action="?/stage_move" class="inline">
			<CopyPicker action={moveAction} bind:selected={pickedCopy} />
			<select name="move_action" aria-label="Move action" bind:value={moveAction}>
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
		<ErrorBanner severity="warning" message="Suggestions unavailable" detail={data.suggestionsError} onRetry={() => invalidateAll()} />
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
		<ScrollTable caption="Suggested cards to add" captionHidden columns={addColumns} rows={s.adds} rowKey={(r) => r.oracle_id} minWidth="32rem">
			{#snippet cell(add, column)}
				{#if column.key === 'donor'}
					{#if add.donor}from {deckName(add.donor.deck_id, names)} ← {add.donor.replacement_name}{:else}—{/if}
				{:else if column.key === 'why'}
					<span class="meta">{add.reasons.join('; ')}</span>
				{:else}
					{add[column.key as 'name' | 'tier' | 'qty_available']}
				{/if}
			{/snippet}
			{#snippet empty()}<p class="meta">No add suggestions.</p>{/snippet}
		</ScrollTable>
			{#if s.hidden_donor_pulls > 0}
				<p class="meta">{s.hidden_donor_pulls} donor pull(s) hidden — {s.hidden_reason}</p>
		{/if}
		<h3>Cuts (weak performers)</h3>
		<ScrollTable caption="Suggested cards to cut" captionHidden columns={cutColumns} rows={s.cuts} rowKey={(r) => r.oracle_id} minWidth="28rem">
			{#snippet cell(cut, column)}
				{#if column.key === 'board'}
					{cut.board ?? '—'}
				{:else if column.key === 'weak'}
					{cut.weak_score.toFixed(2)}
				{:else if column.key === 'why'}
					<span class="meta">{cut.reasons.join('; ')}</span>
				{:else}
					{cut.name}
				{/if}
			{/snippet}
			{#snippet empty()}<p class="meta">No cut suggestions.</p>{/snippet}
		</ScrollTable>
		<p class="meta">{s.note}</p>
	{/if}
</section>

{#if open}
	<section class="commit-bar" aria-label="Commit or abandon this draft">
		<form method="POST" action="?/commit" class="commit-group">
			<button
				type="submit"
				class="btn btn--primary"
				aria-disabled={summary.blockedReason ? 'true' : undefined}
				aria-describedby={summary.blockedReason ? 'commit-blocked' : undefined}
				onclick={openCommit}
			>Review &amp; commit…</button>
			<span class="tally mono"><strong>{cardCount}</strong> cards · <strong>{data.state.moves.length}</strong> moves</span>
			{#if summary.blockedReason}<span id="commit-blocked" class="meta">{summary.blockedReason}</span>{/if}
		</form>
		<form method="POST" action="?/abandon" class="abandon-group">
			<button type="submit" class="btn btn--danger-outline" onclick={openAbandon}>Abandon draft…</button>
		</form>
	</section>
	<CommitDialog bind:this={commitDialog} draftName={draft.name} {summary} namesMissing={data.deckNames === null} />
	<AbandonDialog bind:this={abandonDialog} draftName={draft.name} cards={data.state.cards} moves={data.state.moves} />
{/if}

<style>
	h1 {
		font-size: 1.5rem;
	}
	table {
		/* positioned so the absolutely-positioned .visually-hidden caption/labels are clipped by
		   the .table-scroll wrapper instead of widening the page */
		position: relative;
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
	.group-row th {
		background: var(--color-surface-2, rgba(127, 127, 127, 0.12));
		font-size: 0.8rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.qty-cell {
		text-align: right;
	}
	@media (max-width: 40rem) {
		.col-type {
			display: none;
		}
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
		padding: 0.4rem 0.6rem;
		min-height: var(--touch-min, 2.75rem);
		min-width: var(--touch-min, 2.75rem);
		display: inline-flex;
		align-items: center;
		color: var(--color-danger);
		cursor: pointer;
		font-size: inherit;
		text-decoration: underline;
	}
	.commit-bar {
		position: sticky;
		bottom: 0;
		z-index: 5;
		display: flex;
		justify-content: space-between;
		align-items: center;
		flex-wrap: wrap;
		gap: var(--space-8);
		margin-top: var(--space-6);
		padding: var(--space-3) var(--space-4);
		background: var(--color-surface-2, var(--color-surface));
		border-top: 1px solid var(--color-border);
	}
	.commit-group {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: var(--space-3);
	}
	.tally {
		font-family: var(--font-mono, ui-monospace, monospace);
		font-size: 0.85rem;
	}
	.btn--danger-outline,
	.btn--danger-outline:hover {
		background: transparent;
		color: var(--color-danger);
		border-color: var(--color-danger);
	}
	.commit-bar :global(.btn[aria-disabled='true']) {
		opacity: 0.6;
		cursor: not-allowed;
		border-style: dashed;
	}
	@media (max-width: 40rem) {
		.commit-bar,
		.commit-group {
			flex-direction: column;
			align-items: stretch;
			gap: var(--space-3);
		}
	}
	.confirm-inline {
		border: 1px solid var(--color-border);
		padding: var(--space-4);
		margin: var(--space-4) 0;
	}
	.confirm-inline form {
		display: flex;
		align-items: center;
		gap: var(--space-4);
	}
	.meta {
		font-size: 0.85rem;
		color: var(--color-muted);
	}
	.error {
		color: var(--color-danger);
	}
</style>
