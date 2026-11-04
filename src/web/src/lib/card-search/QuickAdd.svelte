<script lang="ts">
	// Quick-add combobox (docs/ui/card-search.md §4). Ports the behaviour of static/js/card_autocomplete.js:
	// sequence guard, debounce, ARIA listbox, Enter commits the top row, four distinct non-result rows.
	import { untrack } from 'svelte';
	import { cardImage } from '#lib/cardImage.ts';
	import { autocompleteTerm, parseQuickAdd } from './quickAdd.ts';
	import { QUICK_ADD, canSuggest, suggestUrl } from './query.ts';
	import type { AutocompleteHit, AutocompleteResponse, CardHit, SearchResponse } from './types.ts';

	interface Props {
		placeholder: string;
		fixedQty?: 1;
		commander?: boolean;
		pair?: string | null;
		/** Resolves when the host accepted the card; rejects with Error(message) to keep the text for a retry. */
		onpick: (card: CardHit, qty: number) => Promise<boolean>;
		fetcher?: typeof fetch;
		id?: string;
	}
	let { placeholder, fixedQty, commander = false, pair = null, onpick, fetcher = fetch, id = 'cs-quick-add' }: Props = $props();

	type Phase = 'idle' | 'loading' | 'ready' | 'empty' | 'error';
	let value = $state('');
	let items = $state<CardHit[]>([]);
	let phase = $state<Phase>('idle');
	let degraded = $state(false);
	let highlight = $state(-1);
	let open = $state(false);
	let note = $state('');
	let input: HTMLInputElement;
	let timer: ReturnType<typeof setTimeout> | undefined;
	let seq = 0;
	let busy = false;
	const listId = untrack(() => `${id}-list`);

	export function focus() {
		input?.focus();
	}

	function toHit(h: AutocompleteHit | CardHit): CardHit {
		if ('oracle_id' in h && 'cmc' in h) return h as CardHit;
		const a = h as AutocompleteHit;
		// The autocomplete has no oracle_id until CS-1 adds it (§5.3); '' means "unknown", the host resolves by name.
		return {
			oracle_id: a.oracle_id ?? '', name: a.name, mana_cost: a.mana_cost ?? '', cmc: 0, type_line: a.type_line ?? '',
			color_identity: a.color_identity ?? [], rarity: '', oracle_text: '', image: a.image ?? '',
			owned_qty: 0, free_qty: 0, can_be_commander: commander, pairing: null
		};
	}

	async function suggest(term: string): Promise<CardHit[]> {
		const mine = ++seq;
		phase = 'loading';
		open = true;
		try {
			const res = await fetcher(suggestUrl(term, { commander, pair }));
			if (!res.ok) throw new Error(String(res.status));
			const body = (await res.json()) as AutocompleteResponse | SearchResponse;
			if (mine !== seq) return items; // stale response: the sequence guard drops it
			items = body.results.map(toHit);
			degraded = 'degraded' in body && body.degraded === true;
			phase = items.length ? 'ready' : 'empty';
			highlight = -1;
			return items;
		} catch {
			if (mine !== seq) return items;
			items = [];
			phase = 'error';
			return [];
		}
	}

	function onInput() {
		clearTimeout(timer);
		seq++; // any in-flight response is now stale
		note = '';
		const term = autocompleteTerm(value);
		if (!canSuggest(term)) {
			phase = 'idle';
			open = false;
			items = [];
			return;
		}
		open = true;
		phase = 'loading';
		timer = setTimeout(() => void suggest(term), QUICK_ADD.debounceMs);
	}

	async function commit(card: CardHit | undefined) {
		const parsed = parseQuickAdd(value, { fixedQty });
		if (!parsed.ok) {
			note = parsed.reason === 'multiline' ? 'Paste a list via Import' : parsed.reason === 'bad_qty' ? 'Quantity must be 1 or more' : '';
			return;
		}
		if (!card || busy) return;
		if (!card.oracle_id) {
			// §5.3: an unresolved suggestion never commits; the text stays so the user can retry.
			note = 'That card is not available to add yet';
			return;
		}
		busy = true;
		try {
			const ok = await onpick(card, parsed.qty);
			if (ok) {
				value = '';
				items = [];
				phase = 'idle';
				open = false;
				highlight = -1;
			}
		} finally {
			busy = false;
		}
		input?.focus(); // focus stays so the next card can be typed straight away
	}

	async function onKeydown(e: KeyboardEvent) {
		if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
			if (!items.length) return;
			e.preventDefault();
			open = true;
			const n = items.length;
			highlight = e.key === 'ArrowDown' ? (highlight + 1) % n : (highlight - 1 + n) % n;
		} else if (e.key === 'Enter') {
			e.preventDefault();
			if (e.isComposing) return;
			clearTimeout(timer);
			if (phase === 'error') {
				await suggest(autocompleteTerm(value)); // Enter on a failed search retries; it never commits
				return;
			}
			let list = items;
			// Enter before the debounce fired: fetch now and take the top row.
			if (phase === 'loading' || (phase === 'idle' && canSuggest(autocompleteTerm(value)))) list = await suggest(autocompleteTerm(value));
			await commit(list[highlight >= 0 ? highlight : 0]);
		} else if (e.key === 'Tab') {
			const row = items[highlight >= 0 ? highlight : 0];
			if (open && row) {
				e.preventDefault();
				const qty = /^(\d+)\s*[xX]?\s+/.exec(value.trim());
				value = (qty && !fixedQty ? `${qty[0].trim()} ` : '') + row.name;
				open = false; // completes the name without committing
			}
		} else if (e.key === 'Escape') {
			if (open) {
				e.preventDefault();
				clearTimeout(timer);
				seq++;
				if (phase === 'loading') phase = 'idle';
				open = false;
			} else if (value) {
				e.preventDefault();
				clearTimeout(timer);
				seq++;
				value = '';
				items = [];
				phase = 'idle';
			}
		}
	}

	function onPaste(e: ClipboardEvent) {
		const text = e.clipboardData?.getData('text') ?? '';
		if (/[\r\n]/.test(text.trim())) {
			e.preventDefault(); // a single-line input would flatten the list into one search
			note = 'Paste a list via Import';
		}
	}
</script>

<div
	class="quick-add"
	role="presentation"
	onfocusout={(e) => {
		if (!e.currentTarget.contains(e.relatedTarget as Node | null)) open = false;
	}}
>
	<input
		bind:this={input}
		bind:value
		{id}
		class="qa-input"
		type="text"
		role="combobox"
		autocomplete="off"
		autocapitalize="off"
		spellcheck="false"
		aria-label="Quick add card"
		aria-expanded={open}
		aria-controls={listId}
		aria-autocomplete="list"
		aria-activedescendant={open && highlight >= 0 ? `${id}-opt-${highlight}` : undefined}
		{placeholder}
		oninput={onInput}
		onkeydown={onKeydown}
		onfocus={() => {
			if (phase !== 'idle') open = true;
		}}
		onpaste={onPaste}
	/>
	{#if note}<p class="qa-note" role="status">{note}</p>{/if}
	<ul id={listId} class="qa-list" role="listbox" aria-label="Card suggestions" hidden={!open}>
		{#if phase === 'loading'}
			<li class="qa-row qa-state" role="presentation">Searching…</li>
		{:else if phase === 'error'}
			<li class="qa-row qa-state qa-error" role="presentation">
				Search failed —
				<button type="button" class="qa-retry" onmousedown={(e) => e.preventDefault()} onclick={() => void suggest(autocompleteTerm(value))}>retry</button>
			</li>
		{:else if phase === 'empty'}
			<li class="qa-row qa-state" role="presentation">No matches</li>
		{:else}
			{#if degraded}<li class="qa-row qa-state qa-degraded" role="presentation">Card search is offline — local matches only</li>{/if}
			{#each items as it, i (it.oracle_id + it.name)}
				<!-- svelte-ignore a11y_click_events_have_key_events -->
				<li
					id="{id}-opt-{i}"
					class="qa-row qa-opt"
					class:active={i === highlight}
					role="option"
					aria-selected={i === highlight}
					onmousedown={(e) => e.preventDefault()}
					onmouseenter={() => (highlight = i)}
					onclick={() => void commit(it)}
				>
					<img class="qa-thumb" src={cardImage(it.name, 'small')} alt="" width="28" height="39" loading="lazy" />
					<span class="qa-name">{it.name}</span>
					{#if it.owned_qty > 0}<span class="qa-owned">Own {it.owned_qty}</span>{/if}
					<span class="qa-cost">{it.mana_cost}</span>
				</li>
			{/each}
		{/if}
	</ul>
</div>

<style>
	.quick-add { position: relative; flex: 1 1 12rem; min-width: 0; }
	.qa-input {
		width: 100%; min-height: var(--touch-min); padding: var(--space-2) var(--space-3);
		background: var(--color-surface); color: var(--color-text); font: inherit;
		border: 1px solid var(--color-border-control); border-radius: var(--radius-sm);
	}
	.qa-input::placeholder { color: var(--color-muted); }
	.qa-list {
		position: absolute; z-index: 30; left: 0; right: 0; top: calc(100% + 2px); margin: 0; padding: 0; list-style: none;
		background: var(--color-surface-raised); border: 1px solid var(--color-border-control); border-radius: var(--radius-sm);
		max-height: 22rem; overflow-y: auto;
	}
	.qa-list[hidden] { display: none; }
	.qa-row { display: flex; align-items: center; gap: var(--space-2); padding: var(--space-2) var(--space-3); min-height: var(--touch-min); }
	.qa-opt { cursor: pointer; }
	.qa-opt.active { background: var(--color-accent); color: var(--color-on-accent); }
	.qa-thumb { border-radius: 2px; background: var(--color-surface); flex: none; }
	.qa-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.qa-owned { font-size: var(--text-xs); }
	.qa-cost { font-family: var(--font-mono); font-size: var(--text-xs); }
	.qa-state { color: var(--color-text-body); }
	.qa-error { color: var(--color-danger); }
	.qa-degraded { color: var(--color-warning); font-size: var(--text-sm); }
	.qa-retry { background: none; border: 0; padding: 0; font: inherit; color: inherit; text-decoration: underline; cursor: pointer; }
	.qa-note { margin: var(--space-1) 0 0; font-size: var(--text-sm); color: var(--color-warning); }
</style>
