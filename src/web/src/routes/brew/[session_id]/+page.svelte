<script lang="ts">
	import type { PageProps } from './$types';
	import { deserialize } from '$app/forms';
	import { page } from '$app/state';
	import CardSearch from '#lib/card-search/CardSearch.svelte';
	import type { AddRequest } from '#lib/card-search/types.ts';
	import { groupByTier, inListByOracle, isCommander, legacyHref, searchIdentity } from './brew.ts';
	import type { BrewLine } from './+page.server.ts';

	let { data }: PageProps = $props();

	// The server is the source of truth: every add/remove answers with the recomputed list. An answer is only
	// used while it was computed against the currently loaded data (a reload/invalidate drops it).
	let answer = $state.raw<{ base: BrewLine[]; cards: BrewLine[] } | null>(null);
	const cards = $derived(answer && answer.base === data.cards ? answer.cards : data.cards);
	const groups = $derived(groupByTier(cards));
	const inList = $derived(inListByOracle(cards));
	const editable = $derived(data.status === 'drafts_ready');
	const total = $derived(cards.reduce((n, c) => n + c.quantity, 0));
	const legacy = $derived(legacyHref(data.legacyOrigin, page.url, data.sessionId));
	let removeError = $state('');
	let removing = $state('');

	// One request at a time: each answer is the full list, so overlapping ones could land out of order.
	let queue: Promise<unknown> = Promise.resolve();
	function serial<T>(job: () => Promise<T>): Promise<T> {
		const run = queue.then(job, job);
		queue = run.catch(() => {});
		return run;
	}

	async function call(action: 'add' | 'remove', fields: Record<string, string>): Promise<BrewLine[]> {
		const body = new FormData();
		for (const [k, v] of Object.entries(fields)) body.set(k, v);
		const res = await fetch(`?/${action}`, { method: 'POST', body, headers: { 'x-sveltekit-action': 'true' } });
		const result = deserialize(await res.text());
		if (result.type === 'failure') throw new Error(String((result.data as { actionError?: string } | undefined)?.actionError ?? 'The server rejected the change.'));
		if (result.type !== 'success') throw new Error('Network or server error. Try again.');
		return (result.data as { cards: BrewLine[] }).cards;
	}

	/** `onadd` for CardSearch. Rejecting keeps the typed text and shows the message (spec §6: singleton 409). */
	async function addToBrew({ card, qty }: AddRequest): Promise<void> {
		try {
			answer = { base: data.cards, cards: await serial(() => call('add', { oracle_id: card.oracle_id, name: card.name, qty: String(qty) })) };
		} catch (e) {
			const msg = e instanceof Error ? e.message : 'Could not add that card';
			throw new Error(/singleton|already in the brew/i.test(msg) ? 'Already in the brew (singleton)' : msg);
		}
	}

	async function remove(line: BrewLine) {
		if (!line.oracle_id) return;
		removeError = '';
		removing = line.oracle_id;
		try {
			const id = line.oracle_id;
			answer = { base: data.cards, cards: await serial(() => call('remove', { oracle_id: id })) };
		} catch (e) {
			removeError = e instanceof Error ? e.message : 'Could not remove that card';
		} finally {
			removing = '';
		}
	}
</script>

<svelte:head><title>{data.name || 'Brew'} — Commander Lab</title></svelte:head>

<nav><a href="/decks">← all decks</a></nav>
<h1>{data.name || 'Brew'}</h1>
<p class="meta">
	{data.status}
	{#if data.commanders.length}· {data.commanders.join(' + ')}{/if}
	· {data.identity ? data.identity.join('') || 'colorless' : 'identity unknown'}
	· {total} cards
</p>
<p><a href={legacy} data-testid="legacy-link">Finish on the brew page →</a> <span class="meta">(accept, export, make physical, abandon)</span></p>

{#if editable}
	<CardSearch
		boards={['main']}
		identity={searchIdentity(data.identity)}
		{inList}
		ownedDefault={data.ownedDefault}
		onadd={addToBrew}
		placeholder="Add cards to the brew…"
	/>
{:else}
	<p role="status">This brew is {data.status}; cards can only be edited while it is drafts_ready.</p>
{/if}
{#if removeError}<p class="err" role="alert">{removeError}</p>{/if}

{#each groups as g (g.tier)}
	<section aria-labelledby="tier-{g.tier}" data-testid="tier-{g.tier}">
		<h2 id="tier-{g.tier}">{g.title} <span class="meta">({g.count})</span></h2>
		<ul>
			{#each g.lines as line (line.oracle_id ?? line.name)}
				<li data-testid="brew-line">
					<span class="qty">{line.quantity}×</span>
					<span class="name">{line.name}</span>
					<span class="chip" data-tier={line.tier}>{g.chip}</span>
					{#if isCommander(line, data.commanders)}
						<span class="meta">commander</span>
					{:else if editable && line.oracle_id}
						<button type="button" class="btn btn--secondary" disabled={removing === line.oracle_id} onclick={() => remove(line)} aria-label="Remove {line.name}">Remove</button>
					{/if}
				</li>
			{/each}
		</ul>
	</section>
{:else}
	<p>No cards yet.</p>
{/each}

<style>
	h1 { font-size: 1.5rem; }
	ul { list-style: none; padding: 0; margin: 0 0 var(--space-3, 1rem); }
	li { display: flex; align-items: center; gap: var(--space-2, 0.5rem); padding: 0.2rem 0; border-bottom: 1px solid rgba(127, 127, 127, 0.25); }
	.qty { min-width: 2.5ch; text-align: right; font-family: var(--font-mono, monospace); }
	.name { flex: 1; }
	.chip { font-size: 0.75rem; padding: 0 0.5rem; border: 1px solid currentColor; border-radius: 999px; }
	.err { color: var(--color-danger, #b00020); }
</style>
