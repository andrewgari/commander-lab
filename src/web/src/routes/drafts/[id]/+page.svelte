<script lang="ts">
	import { enhance } from '$app/forms';
	import { deserialize } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import type { ActionData, PageProps } from './$types';
	import TableScroll from '#lib/components/TableScroll.svelte';
	import QtyEditor from '#lib/components/drafts/QtyEditor.svelte';
	import QuickAdd from '#lib/components/card-search/QuickAdd.svelte';
	import CommitDialog from '#lib/components/drafts/CommitDialog.svelte';
	import AbandonDialog from '#lib/components/drafts/AbandonDialog.svelte';
	import CommitSummary from '#lib/components/drafts/CommitSummary.svelte';
	import CardPicker from '#lib/components/combobox/CardPicker.svelte';
	import CopyPicker from '#lib/components/combobox/CopyPicker.svelte';
	import type { CardChoice, ComboOption } from '#lib/components/combobox/model.ts';
	import { boardLabel, groupByBoard, nextQty, stagedQty, stagedQtyByOracle, toApiBoard } from '#lib/drafts/boards.ts';
	import { abandonSentence, deckName, moveRows, summarize } from '#lib/drafts/summary.ts';
	import type { AddRequest, Board } from '#lib/card-search/types.ts';

	let { data, form }: PageProps & { form: ActionData } = $props();

	const draft = $derived(data.state.draft);
	const open = $derived(draft.status === 'open');
	const deckNames = $derived(data.deckNames ? new Map(Object.entries(data.deckNames)) : null);
	const summary = $derived(summarize({ draft, cards: data.state.cards, moves: data.state.moves }, deckNames));
	const groups = $derived(groupByBoard(data.state.cards));
	const totalCards = $derived(data.state.cards.reduce((n, c) => n + c.qty, 0));
	const rows = $derived(moveRows(data.state.moves, deckNames));
	const inList = $derived(stagedQtyByOracle(data.state.cards));
	const blocked = $derived(summary.blockedReason !== null);
	const needsConfirm = $derived(form && 'needsConfirm' in form ? form.needsConfirm : null);
	const actionError = $derived(form && 'actionError' in form ? form.actionError : null);
	const sources = $derived(draft.source_decks.map((s) => s.name).join(' + '));

	const BOARDS: { value: Board; label: string }[] = [
		{ value: 'main', label: 'Main' },
		{ value: 'commander', label: 'Commander' },
		{ value: 'sideboard', label: 'Sideboard' },
		{ value: 'maybeboard', label: 'Maybeboard' },
		{ value: 'considering', label: 'Considering' }
	];

	let commitDialog = $state<ReturnType<typeof CommitDialog>>();
	let abandonDialog = $state<ReturnType<typeof AbandonDialog>>();
	let rowStatus = $state('');

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
		if (result.type === 'failure') throw new Error(String((result.data as { actionError?: string } | undefined)?.actionError ?? 'The server rejected the card.'));
		if (result.type === 'error') throw new Error('Network or server error. Try again.');
		await invalidateAll();
		const label = boardLabel(apiBoard);
		return `Added ${qty}× ${card.name} to ${label}${existing > 0 ? ` (now ${newQty})` : ''}`;
	}

	function openCommit(e: Event) {
		// With JS: the button opens the dialog. Without JS it submits (unconfirmed) and the server
		// answers with the inline summary.
		e.preventDefault();
		if (blocked) return;
		commitDialog?.show(e.currentTarget as HTMLElement);
	}
	function openAbandon(e: Event) {
		e.preventDefault();
		abandonDialog?.show(e.currentTarget as HTMLElement);
	}

	let pickedCard = $state<ComboOption<CardChoice> | null>(null);
	let pickedCopy = $state<ComboOption<string> | null>(null);
	let moveAction = $state('assign');
</script>

<svelte:head>
	<title>{draft.name} — Draft — Commander Lab</title>
</svelte:head>

<nav><a class="text-link" href="/drafts">← Drafts</a></nav>
<h1>{draft.name}</h1>
<p class="meta">
	{draft.kind}{#if sources}: {sources}{/if}
	→ <strong>{draft.target_name}</strong> ·
	<span class="status status--{draft.status}">{draft.status === 'open' ? '● Open' : draft.status === 'committed' ? '✓ Committed' : '✕ Abandoned'}</span>
</p>
{#if draft.status === 'committed' && draft.result_deck_id}
	<p>Built deck: <a href="/decks/{encodeURIComponent(draft.result_deck_id)}">{draft.target_name}</a></p>
{/if}
{#if draft.notes}<p class="meta">{draft.notes}</p>{/if}
{#if data.deckNames === null}<p class="meta" role="status">Deck names unavailable.</p>{/if}

{#if actionError}<p class="error" role="alert">{actionError}</p>{/if}

{#if open && needsConfirm === 'commit'}
	<section class="confirm-inline" aria-labelledby="confirm-commit-h">
		<h2 id="confirm-commit-h">Commit draft?</h2>
		<CommitSummary {summary} namesMissing={data.deckNames === null} />
		<form method="POST" action="?/commit" class="confirm-actions">
			<input type="hidden" name="confirmed" value="1" />
			<button type="submit" class="btn-primary">Commit draft</button>
			<a class="text-link" href="/drafts/{encodeURIComponent(draft.id)}">Cancel</a>
		</form>
	</section>
{:else if open && needsConfirm === 'abandon'}
	<section class="confirm-inline" aria-labelledby="confirm-abandon-h">
		<h2 id="confirm-abandon-h">Abandon draft?</h2>
		<p>{abandonSentence(data.state.cards, data.state.moves)}</p>
		<form method="POST" action="?/abandon" class="confirm-actions">
			<input type="hidden" name="confirmed" value="1" />
			<button type="submit" class="btn-danger">Abandon draft</button>
			<a class="text-link" href="/drafts/{encodeURIComponent(draft.id)}">Cancel</a>
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
					<select name="board">{#each BOARDS as b (b.value)}<option value={b.value === 'maybeboard' ? 'maybe' : b.value}>{b.label}</option>{/each}</select>
				</label>
				<button type="submit">Add card</button>
			</form>
		</noscript>
	</section>
{/if}

<section aria-labelledby="staged-h" aria-busy={false}>
	<h2 id="staged-h">Staged list ({totalCards} {totalCards === 1 ? 'card' : 'cards'})</h2>
	<p class="visually-hidden" role="status">{rowStatus}</p>
	{#if data.state.cards.length === 0}
		<div class="empty">
			<p class="empty__title">Nothing staged yet</p>
			{#if open}<p class="meta">Search your collection above to add cards.</p>{/if}
		</div>
	{:else}
		<TableScroll label="Staged list" minWidth="28rem">
			<table>
				<caption class="visually-hidden">Staged cards, grouped by board</caption>
				<thead>
					<tr>
						<th scope="col">Card</th>
						<th scope="col" class="col-type">Type</th>
						<th scope="col" class="num">MV</th>
						<th scope="col" class="num">Qty</th>
						{#if open}<th scope="col"><span class="visually-hidden">Actions</span></th>{/if}
					</tr>
				</thead>
				{#each groups as group (group.board)}
					<tbody>
						<tr class="group-row"><th scope="colgroup" colspan={open ? 5 : 4}>{group.label} <span class="group-count">{group.qty}</span></th></tr>
						{#each group.rows as card (card.oracle_id + card.board)}
							<tr>
								<td class="card-cell"><a href="/card/{encodeURIComponent(card.oracle_id)}">{card.name}</a>{#if card.category} <span class="meta">· {card.category}</span>{/if}</td>
								<td class="meta col-type">{card.type_line}</td>
								<td class="num">{card.cmc}</td>
								{#if open}
									<td class="num">
										<QtyEditor oracleId={card.oracle_id} board={card.board} cardName={card.name} boardLabel={group.label} qty={card.qty} onsaved={(m) => (rowStatus = m)} />
									</td>
									<td>
										<form method="POST" action="?/remove_card" use:enhance>
											<input type="hidden" name="oracle_id" value={card.oracle_id} />
											<input type="hidden" name="board" value={card.board} />
											<button type="submit" class="link" aria-label={`Remove ${card.name} from ${group.label}`}>Remove</button>
										</form>
									</td>
								{:else}
									<td class="num">{card.qty}</td>
								{/if}
							</tr>
						{/each}
					</tbody>
				{/each}
			</table>
		</TableScroll>
	{/if}
	{#if open}
		<form method="POST" action="?/upsert_card" class="inline">
			<CardPicker bind:selected={pickedCard} />
			<input name="qty" type="number" min="1" value="1" aria-label="Quantity" />
			<select name="board" aria-label="Board">
				<option value="main">main</option>
				<option value="commander">commander</option>
				<option value="sideboard">sideboard</option>
				<option value="maybeboard">maybeboard</option>
			</select>
			<button type="submit">Add / update card</button>
		</form>
	{/if}
</section>

<section aria-labelledby="moves-h">
	<h2 id="moves-h">Physical-copy moves ({data.state.moves.length})</h2>
	{#if rows.length === 0}
		<p class="meta">No copy moves staged. Commit without moves creates a virtual deck.</p>
	{:else}
		<TableScroll label="Physical-copy moves" minWidth="28rem">
			<table>
				<caption class="visually-hidden">Staged physical-copy moves</caption>
				<thead>
					<tr><th scope="col">Card</th><th scope="col">Move</th><th scope="col">From</th><th scope="col">Now</th>{#if open}<th scope="col"><span class="visually-hidden">Actions</span></th>{/if}</tr>
				</thead>
				<tbody>
					{#each rows as row (row.instanceId)}
						<tr>
							<td>{row.displayName}</td>
							<td>{row.moveLabel}</td>
							<td>{row.from}</td>
							<td class="meta">{row.state}</td>
							{#if open}
								<td>
									<form method="POST" action="?/unstage_move" use:enhance>
										<input type="hidden" name="instance_id" value={row.instanceId} />
										<button type="submit" class="link" aria-label={`Unstage ${row.displayName} from ${row.from}`}>Unstage</button>
									</form>
								</td>
							{/if}
						</tr>
					{/each}
				</tbody>
			</table>
		</TableScroll>
	{/if}
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
		<div class="error" role="alert">
			<p>Suggestions unavailable: {data.suggestionsError}</p>
			<button type="button" onclick={() => invalidateAll()}>Retry</button>
		</div>
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
			<TableScroll label="Add suggestions" minWidth="32rem">
			<table>
				<caption class="visually-hidden">Suggested cards to add</caption>
				<thead><tr><th scope="col">Card</th><th scope="col">Tier</th><th scope="col">Owned avail.</th><th scope="col">Donor pull</th><th scope="col">Why</th></tr></thead>
				<tbody>
					{#each s.adds as add (add.oracle_id)}
						<tr>
							<td>{add.name}</td>
							<td>{add.tier}</td>
							<td>{add.qty_available}</td>
							<td>
								{#if add.donor}
									from {deckName(add.donor.deck_id, deckNames)} ← {add.donor.replacement_name}
								{:else}
									—
								{/if}
							</td>
							<td class="meta">{add.reasons.join('; ')}</td>
						</tr>
					{/each}
				</tbody>
			</table>
			</TableScroll>
			{#if s.hidden_donor_pulls > 0}
				<p class="meta">{s.hidden_donor_pulls} donor pull(s) hidden — {s.hidden_reason}</p>
			{/if}
		{/if}
		<h3>Cuts (weak performers)</h3>
		{#if s.cuts.length === 0}
			<p class="meta">No cut suggestions.</p>
		{:else}
			<TableScroll label="Cut suggestions" minWidth="28rem">
			<table>
				<caption class="visually-hidden">Suggested cards to cut</caption>
				<thead><tr><th scope="col">Card</th><th scope="col">Board</th><th scope="col">Weak score</th><th scope="col">Why</th></tr></thead>
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
			</TableScroll>
		{/if}
		<p class="meta">{s.note}</p>
	{/if}
</section>

{#if open}
	<section class="commit-bar" aria-label="Commit or abandon this draft">
		<div class="commit-main">
			<form method="POST" action="?/commit" class="commit-form">
				<button
					type="submit"
					class="btn-primary"
					aria-disabled={blocked ? 'true' : undefined}
					aria-describedby={blocked ? 'commit-blocked' : undefined}
					onclick={openCommit}
				>Review & commit…</button>
			</form>
			{#if blocked}
				<span id="commit-blocked" class="meta">{summary.blockedReason}</span>
			{:else}
				<span class="tally"><strong>{summary.totalCards}</strong> cards · <strong>{data.state.moves.length}</strong> moves</span>
			{/if}
		</div>
		<form method="POST" action="?/abandon" class="abandon-form">
			<button type="submit" class="btn-abandon" onclick={openAbandon}>Abandon draft…</button>
		</form>
	</section>

	<CommitDialog bind:this={commitDialog} {summary} draftName={draft.name} namesMissing={data.deckNames === null} />
	<AbandonDialog bind:this={abandonDialog} sentence={abandonSentence(data.state.cards, data.state.moves)} draftName={draft.name} />
{/if}

<style>
	h1 { font-size: var(--text-xl); }
	section { margin-bottom: var(--space-6); }
	table { position: relative; border-collapse: collapse; font-size: var(--text-sm); margin-bottom: var(--space-3); width: 100%; }
	th, td { text-align: left; padding: var(--space-1) var(--space-3) var(--space-1) 0; border-bottom: 1px solid var(--color-border); vertical-align: middle; }
	.num { text-align: right; }
	.card-cell { max-width: 28rem; overflow-wrap: anywhere; }
	.group-row th { padding-top: var(--space-4); border-bottom: 1px solid var(--color-border-control); font-family: var(--font-mono); font-size: var(--text-xs); text-transform: uppercase; letter-spacing: var(--tracking-label); color: var(--color-muted); font-weight: var(--weight-medium); }
	.group-count { margin-left: var(--space-1); }
	@media (max-width: 39.99rem) { .col-type { display: none; } }
	form.inline { display: flex; gap: var(--space-3); flex-wrap: wrap; align-items: end; }
	form.inline label { display: grid; gap: var(--space-1); font-size: var(--text-sm); }
	button.link { background: none; border: none; padding: var(--space-2) var(--space-3); min-height: var(--touch-min); min-width: var(--touch-min); display: inline-flex; align-items: center; color: var(--color-accent); cursor: pointer; font-size: inherit; text-decoration: underline; }
	.empty { padding: var(--space-4); border: 1px dashed var(--color-border); border-radius: var(--radius-sm); }
	.empty__title { margin: 0; color: var(--color-text); font-weight: var(--weight-medium); }
	.status--open { color: var(--color-warning); }
	.status--committed { color: var(--color-success); }
	.status--abandoned { color: var(--color-muted); }
	.confirm-inline { border: 1px solid var(--color-border-control); border-top: 4px solid var(--color-accent); padding: var(--space-4); background: var(--color-surface); max-width: var(--dialog-max, 40rem); }
	.confirm-actions { display: flex; gap: var(--space-4); align-items: center; margin-top: var(--space-4); }
	.btn-primary { background: var(--color-accent); color: var(--color-on-accent); border-color: var(--color-accent); font-weight: var(--weight-medium); }
	.btn-primary:hover { background: var(--color-accent-hover); border-color: var(--color-accent-hover); }
	.btn-primary[aria-disabled='true'], .btn-primary[aria-disabled='true']:hover { background: var(--color-surface); color: var(--color-muted); border-color: var(--color-border); cursor: not-allowed; }
	.btn-danger { background: var(--color-danger-solid); color: var(--color-on-solid); border-color: var(--color-danger-solid); }
	.btn-abandon { background: transparent; color: var(--color-danger); border-color: var(--color-danger-solid); }
	.btn-abandon:hover { background: var(--color-danger-bg); border-color: var(--color-danger); }
	.commit-bar {
		position: sticky; bottom: 0; z-index: 10; margin: var(--space-6) calc(-1 * var(--space-4)) 0;
		display: flex; flex-direction: column; gap: var(--space-3);
		background: var(--color-surface); border-top: 2px solid var(--color-accent); padding: var(--space-3) var(--space-4);
	}
	@media (min-width: 40rem) { .commit-bar { flex-direction: row; align-items: center; justify-content: space-between; gap: var(--space-8); margin-inline: calc(-1 * var(--space-6)); padding-inline: var(--space-6); } }
	.commit-main { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-3) var(--space-4); }
	.commit-form, .abandon-form { display: contents; }
	.tally { font-family: var(--font-mono); font-size: var(--text-sm); color: var(--color-text-body); }
	.meta { font-size: var(--text-sm); color: var(--color-muted); }
	.error { color: var(--color-danger); }
</style>