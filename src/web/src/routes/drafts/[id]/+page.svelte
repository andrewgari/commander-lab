<script lang="ts">
	import type { ActionData, PageProps } from './$types';
	import TableScroll from '#lib/components/TableScroll.svelte';
	import ScrollTable, { type Column } from '#lib/components/ScrollTable.svelte';
	import StatusPill from '#lib/components/StatusPill.svelte';
	import ErrorBanner from '#lib/components/ErrorBanner.svelte';
	import { invalidateAll } from '$app/navigation';
	import { draftStatus } from '#lib/status.ts';
	import { deckName, moveRows } from '#lib/drafts/summary.ts';
	import QuickAdd from '#lib/components/card-search/QuickAdd.svelte';
	import CopyPicker from '#lib/components/combobox/CopyPicker.svelte';
	import type { ComboOption } from '#lib/components/combobox/model.ts';
	import { deserialize } from '$app/forms';
	import { boardLabel, nextQty, stagedQty, stagedQtyByOracle, toApiBoard } from '#lib/drafts/boards.ts';
	import type { AddRequest, Board } from '#lib/card-search/types.ts';

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

	let pickedCopy = $state<ComboOption<string> | null>(null);
	let moveAction = $state('assign');

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
	{draft.kind}: {#each draft.source_decks as src, i}{i > 0 ? ' + ' : ''}{src.name}{/each}
	→ <strong>{draft.target_name}</strong> ·
	<StatusPill status={draftStatus(draft.status)} label={draft.status} />
	{#if draft.result_deck_id}· Built deck: <a href={`/decks/${draft.result_deck_id}`}>{draft.target_name}</a>{/if}
</p>
{#if data.deckNames === null}<p class="meta" role="status">Deck names unavailable</p>{/if}
{#if draft.notes}<p class="meta">{draft.notes}</p>{/if}

{#if form?.actionError}<p class="error" role="alert">{form.actionError}</p>{/if}

{#if open}
	<section class="danger-zone">
		<form method="POST" action="?/commit" onsubmit={confirmSubmit('Commit this draft? The new deck, card rows and physical-copy moves are applied in one transaction.')}>
			<button type="submit" class="commit" aria-label={`Commit draft ${draft.name ?? draft.id}: apply all staged changes`}>Commit draft</button>
		</form>
		<form method="POST" action="?/abandon" onsubmit={confirmSubmit('Abandon this draft? All staged rows are deleted — zero residue.')}>
			<button type="submit" class="abandon" aria-label={`Abandon draft ${draft.name ?? draft.id}: delete all staged rows`}>Abandon</button>
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
		<p class="meta">Nothing staged yet.</p>
	{:else}
		<TableScroll label="Staged list" minWidth="32rem">
		<table>
			<caption class="visually-hidden">Staged cards</caption>
			<thead>
				<tr><th scope="col">Qty</th><th scope="col">Card</th><th scope="col">Board</th><th scope="col">Type</th><th scope="col">CMC</th>{#if open}<th scope="col"><span class="visually-hidden">Actions</span></th>{/if}</tr>
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
									<button type="submit" class="link" aria-label={`Remove ${card.name} (${card.board}) from staged list`}>remove</button>
								</form>
							</td>
						{/if}
					</tr>
				{/each}
			</tbody>
		</table>
		</TableScroll>
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
	.danger-zone {
		display: flex;
		gap: 0.6rem;
		margin: 0.75rem 0;
	}
	button.commit {
		padding: 0.45rem 1.1rem;
		min-height: var(--touch-min);
		background: var(--color-success-solid);
		color: var(--color-on-solid);
		border: none;
		cursor: pointer;
	}
	button.abandon {
		padding: 0.45rem 1.1rem;
		min-height: var(--touch-min);
		background: var(--color-danger-solid);
		color: var(--color-on-solid);
		border: none;
		cursor: pointer;
	}
	.meta {
		font-size: 0.85rem;
		color: var(--color-muted);
	}
	.error {
		color: var(--color-danger);
	}
</style>
