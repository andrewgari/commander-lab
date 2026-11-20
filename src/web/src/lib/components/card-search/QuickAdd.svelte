<script lang="ts">
	// Quick-add combobox (docs/ui/card-search.md §4; drafts page-specs §2.4, WAI-ARIA 1.2 combobox).
	// The host owns the list: this component only calls `onadd` and reports the outcome.
	import { onMount } from 'svelte';
	import { autocompleteTerm } from '#lib/card-search/quickAdd.ts';
	import { canSuggest, QUICK_ADD } from '#lib/card-search/query.ts';
	import {
		buildRows,
		completeName,
		countAnnouncement,
		enterTarget,
		moveHighlight,
		nameSegments,
		ownedLabel,
		ownedSuggestUrl,
		typedQty,
		type ComboRow,
		type SearchPhase
	} from '#lib/card-search/combobox.ts';
	import type { AddRequest, Board, CardHit, SearchResponse } from '#lib/card-search/types.ts';

	interface Props {
		/** Target boards; the first is the default. */
		boards: { value: Board; label: string }[];
		/** Display-only "In draft ×N" badge, keyed by oracle id. */
		inList?: ReadonlyMap<string, number>;
		/** Resolve with the message to announce ("Added 2× Sol Ring to Main (now 3)"), or reject with Error(message). */
		onadd: (req: AddRequest) => Promise<string>;
		/** Ctrl+' focuses the input. */
		hotkey?: boolean;
	}

	let { boards, inList, onadd, hotkey = true }: Props = $props();

	const uid = $props.id();
	const inputId = `${uid}-input`;
	const listId = `${uid}-list`;
	const hintId = `${uid}-hint`;

	let value = $state('');
	let board = $state<Board>('main');
	let phase = $state<SearchPhase>('idle');
	let results = $state<CardHit[]>([]);
	let degraded = $state(false);
	let errorDetail = $state('');
	let showSearching = $state(false);
	let open = $state(false);
	let highlighted = $state(-1);
	let busy = $state(false);
	let status = $state<{ kind: 'info' | 'ok' | 'error'; text: string }>({ kind: 'info', text: '' });
	let input: HTMLInputElement;

	let seq = 0;
	let debounce: ReturnType<typeof setTimeout> | undefined;
	let searchingTimer: ReturnType<typeof setTimeout> | undefined;
	let controller: AbortController | undefined;
	let pendingEnter = false;

	$effect(() => {
		// First configured board is the default; keep the selection valid if the prop changes.
		if (!boards.some((b) => b.value === board)) board = boards[0]?.value ?? 'main';
	});

	const term = $derived(autocompleteTerm(value));
	const rows = $derived<ComboRow[]>(
		buildRows({ phase, term, results, degraded, showSearching, errorDetail })
	);
	const boardLabel = $derived(boards.find((b) => b.value === board)?.label ?? board);
	const optionId = (i: number) => `${uid}-opt-${i}`;
	const activeId = $derived(open && highlighted >= 0 ? optionId(highlighted) : undefined);

	function setStatus(kind: 'info' | 'ok' | 'error', text: string) {
		status = { kind, text };
	}

	function cancelPending() {
		clearTimeout(debounce);
		clearTimeout(searchingTimer);
		controller?.abort();
		seq++;
	}

	function reset(keepStatus = false) {
		cancelPending();
		phase = 'idle';
		results = [];
		degraded = false;
		showSearching = false;
		highlighted = -1;
		open = false;
		pendingEnter = false;
		if (!keepStatus) setStatus('info', '');
	}

	async function runSearch(forTerm: string) {
		const mine = ++seq;
		controller?.abort();
		controller = new AbortController();
		phase = 'loading';
		showSearching = false;
		clearTimeout(searchingTimer);
		searchingTimer = setTimeout(() => {
			if (mine === seq) showSearching = true;
		}, 300);
		open = true;
		try {
			const res = await fetch(ownedSuggestUrl(forTerm), { signal: controller.signal, headers: { accept: 'application/json' } });
			if (mine !== seq) return;
			if (!res.ok) throw new Error(`${res.status} from card search`);
			const body = (await res.json()) as SearchResponse;
			if (mine !== seq) return;
			results = body.results ?? [];
			degraded = Boolean(body.degraded);
			phase = 'ready';
			highlighted = -1;
			setStatus('info', countAnnouncement(results.length));
		} catch (e) {
			if (mine !== seq || (e instanceof DOMException && e.name === 'AbortError')) return;
			results = [];
			errorDetail = e instanceof Error ? e.message : 'card search failed';
			phase = 'error';
			highlighted = -1;
			setStatus('error', 'Search failed. Press Enter to retry.');
		} finally {
			if (mine === seq) {
				clearTimeout(searchingTimer);
				showSearching = false;
			}
		}
		if (mine === seq && pendingEnter && phase === 'ready') {
			pendingEnter = false;
			const target = enterTarget(rows, -1);
			if (target?.kind === 'card') await add(target.hit);
		}
	}

	function onInput() {
		pendingEnter = false;
		clearTimeout(debounce);
		const t = autocompleteTerm(value);
		if (!canSuggest(t)) {
			reset();
			return;
		}
		debounce = setTimeout(() => runSearch(t), QUICK_ADD.debounceMs);
		phase = phase === 'idle' ? 'loading' : phase;
		open = true;
	}

	async function add(hit: CardHit) {
		const qty = typedQty(value);
		if (qty === null) {
			setStatus('error', 'Quantity must be 1 or more');
			return;
		}
		if (busy) return;
		busy = true;
		cancelPending();
		try {
			const message = await onadd({ card: hit, qty, board });
			setStatus('ok', message || `Added ${qty}× ${hit.name} to ${boardLabel}`);
			value = '';
			reset(true);
		} catch (e) {
			setStatus('error', e instanceof Error ? e.message : 'Could not add the card');
			// Keep the typed text and the list so the user can retry.
		} finally {
			busy = false;
			input?.focus();
		}
	}

	function activate(row: ComboRow | null) {
		if (!row) return;
		if (row.kind === 'card') void add(row.hit);
		else if (row.kind === 'error') void runSearch(term);
	}

	function onKeydown(e: KeyboardEvent) {
		switch (e.key) {
			case 'ArrowDown':
			case 'ArrowUp': {
				e.preventDefault();
				if (!open) {
					if (canSuggest(term) && rows.length > 0) open = true;
					else if (canSuggest(term)) void runSearch(term);
					if (!open) return;
				}
				highlighted = moveHighlight(rows, highlighted, e.key === 'ArrowDown' ? 1 : -1);
				break;
			}
			case 'Enter': {
				e.preventDefault();
				if (busy) return;
				if (phase === 'loading' && results.length === 0) {
					// Results are on their way: add the top row when they land.
					clearTimeout(debounce);
					pendingEnter = true;
					void runSearch(term);
					return;
				}
				activate(open ? enterTarget(rows, highlighted) : null);
				break;
			}
			case 'Tab': {
				// Complete the name; do not add; move focus normally.
				const target = open ? rows[highlighted] : undefined;
				const pick = target?.kind === 'card' ? target : rows.find((r) => r.kind === 'card');
				if (open && pick?.kind === 'card') {
					value = completeName(value, pick.hit.name);
				}
				open = false;
				break;
			}
			case 'Escape': {
				if (open) {
					e.preventDefault();
					open = false;
					highlighted = -1;
				} else if (value) {
					e.preventDefault();
					value = '';
					reset();
				}
				break;
			}
		}
	}

	function onPaste(e: ClipboardEvent) {
		const text = e.clipboardData?.getData('text') ?? '';
		if (/[\r\n]/.test(text.trim())) {
			e.preventDefault();
			setStatus('info', 'Paste a list via Import');
		}
	}

	onMount(() => {
		if (!hotkey) return;
		const onKey = (e: KeyboardEvent) => {
			if (e.ctrlKey && !e.altKey && !e.metaKey && e.key === "'") {
				e.preventDefault();
				input?.focus();
				input?.select();
			}
		};
		window.addEventListener('keydown', onKey);
		return () => {
			window.removeEventListener('keydown', onKey);
			cancelPending();
		};
	});
</script>

<div class="quickadd">
	<div class="qa-row">
		<div class="qa-field">
			<label for={inputId}>Add cards to {boardLabel}</label>
			<div class="combo">
				<svg class="combo__icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" stroke-width="1.6" /><path d="m10.5 10.5 3.5 3.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" /></svg>
				<input
					bind:this={input}
					id={inputId}
					type="text"
					role="combobox"
					autocomplete="off"
					autocapitalize="off"
					spellcheck="false"
					aria-expanded={open && rows.length > 0}
					aria-controls={listId}
					aria-autocomplete="list"
					aria-activedescendant={activeId}
					aria-describedby={hintId}
					placeholder="Sol Ring, or 2x Arcane Signet"
					bind:value
					oninput={onInput}
					onkeydown={onKeydown}
					onpaste={onPaste}
					onblur={() => {
						// Let a click on an option land before the list closes.
						setTimeout(() => {
							if (document.activeElement !== input) open = false;
						}, 120);
					}}
					onfocus={() => {
						if (rows.length > 0 && canSuggest(term)) open = true;
					}}
				/>
				{#if hotkey}<kbd class="combo__kbd" aria-hidden="true">Ctrl '</kbd>{/if}
				<ul id={listId} class="listbox" role="listbox" aria-label="Card suggestions" hidden={!(open && rows.length > 0)}>
					{#each rows as row, i (row.key)}
						{#if row.kind === 'degraded'}
							<li role="presentation" class="row row--degraded">Card search is offline — showing local matches</li>
						{:else if row.kind === 'searching'}
							<li id={optionId(i)} role="option" aria-selected="false" aria-disabled="true" class="row row--state"><span class="spin" aria-hidden="true"></span> Searching…</li>
						{:else if row.kind === 'empty'}
							<li id={optionId(i)} role="option" aria-selected="false" aria-disabled="true" class="row row--state">
								<span>No owned cards match “{row.term}”</span>
								<span class="sub">Draft staging only offers cards you own.</span>
							</li>
						{:else if row.kind === 'error'}
							<!-- svelte-ignore a11y_click_events_have_key_events -->
							<li id={optionId(i)} role="option" aria-selected={highlighted === i} aria-disabled="false" class="row row--error" class:is-active={highlighted === i} onmousedown={(e) => e.preventDefault()} onclick={() => activate(row)}>
								<span>✕ Search failed — press Enter to retry</span>
								<span class="sub">{row.detail}</span>
							</li>
						{:else}
							{@const hit = row.hit}
							{@const staged = inList?.get(hit.oracle_id) ?? 0}
							<!-- svelte-ignore a11y_click_events_have_key_events -->
							<li id={optionId(i)} role="option" aria-selected={highlighted === i} class="row row--card" class:is-active={highlighted === i} onmousedown={(e) => e.preventDefault()} onclick={() => activate(row)} onmousemove={() => (highlighted = i)}>
								<span class="name">{#each nameSegments(hit.name, term) as seg, k (k)}{#if seg.sep}<span class="face-sep"> // </span>{:else if seg.match}<mark>{seg.text}</mark>{:else}{seg.text}{/if}{/each}</span>
								<span class="cost">{hit.mana_cost}</span>
								<span class="sub">{hit.type_line} · <span class="mono">{ownedLabel(hit)}</span>{#if staged > 0} · <span class="mono in-draft">In draft ×{staged}</span>{/if}</span>
							</li>
						{/if}
					{/each}
				</ul>
			</div>
		</div>
		<div class="qa-field qa-board">
			<label for="{uid}-board">Board</label>
			<select id="{uid}-board" bind:value={board}>
				{#each boards as b (b.value)}<option value={b.value}>{b.label}</option>{/each}
			</select>
		</div>
	</div>
	<p id={hintId} class="qa-hint">
		<span class="chip">✓ Owned only</span>
		<span>Draft staging searches your collection. Type <code>2x</code> before a name to set quantity.</span>
	</p>
	<p class="qa-status" class:is-ok={status.kind === 'ok'} class:is-error={status.kind === 'error'} role="status" aria-live="polite">{status.text}</p>
</div>

<style>
	.quickadd { max-width: 52rem; display: grid; gap: var(--space-2); }
	.qa-row { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--space-3); align-items: start; }
	@media (min-width: 40rem) { .qa-row { grid-template-columns: minmax(0, 1fr) 11rem; } }
	.qa-field { display: grid; gap: var(--space-1); min-width: 0; }
	label { color: var(--color-text); font-size: var(--text-sm); font-weight: var(--weight-medium); }
	.combo { position: relative; }
	.combo input { width: 100%; font-size: var(--text-md); padding-left: 2.5rem; padding-right: 4.5rem; }
	.combo__icon { position: absolute; left: var(--space-3); top: 0; height: var(--touch-min); width: 1em; font-size: 1.1rem; color: var(--color-muted); pointer-events: none; }
	.combo__kbd { position: absolute; right: var(--space-3); top: 0; height: var(--touch-min); display: inline-flex; align-items: center; font-size: var(--text-xs); color: var(--color-muted); pointer-events: none; }
	select { width: 100%; }
	.listbox {
		position: absolute; z-index: 20; left: 0; right: 0; top: 100%; margin: var(--space-1) 0 0; padding: 0; list-style: none;
		background: var(--color-surface-raised); border: 1px solid var(--color-border-control); border-radius: var(--radius-sm);
		max-height: min(24rem, 60vh); overflow-y: auto;
	}
	.listbox[hidden] { display: none; }
	.row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 0 var(--space-3); padding: var(--space-2) var(--space-3); min-height: var(--touch-min); border-left: 3px solid transparent; cursor: pointer; align-content: center; }
	.row .name { color: var(--color-text); font-weight: var(--weight-medium); overflow-wrap: anywhere; }
	.row mark { background: none; color: var(--color-accent); font-weight: var(--weight-bold); }
	.face-sep { color: var(--color-muted); font-weight: var(--weight-regular); }
	.cost { font-family: var(--font-mono); font-size: var(--text-xs); white-space: nowrap; color: var(--color-text-body); }
	.sub { grid-column: 1 / -1; font-size: var(--text-xs); color: var(--color-muted); overflow-wrap: anywhere; }
	.mono { font-family: var(--font-mono); }
	.in-draft { color: var(--color-info); }
	.row.is-active { background: color-mix(in srgb, var(--color-accent) 14%, transparent); border-left-color: var(--color-accent); }
	.row--state { cursor: default; color: var(--color-text-body); display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-1) var(--space-2); }
	.row--error { color: var(--color-danger); display: grid; }
	.row--error.is-active { background: var(--color-danger-bg); border-left-color: var(--color-danger); }
	.row--degraded { display: block; padding: var(--space-1) var(--space-3); border-bottom: 1px solid var(--color-border); font-family: var(--font-mono); font-size: var(--text-xs); text-transform: uppercase; letter-spacing: var(--tracking-label); color: var(--color-warning); cursor: default; }
	.spin { display: inline-block; width: 1em; height: 1em; border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%; animation: spin 0.8s linear infinite; }
	@keyframes spin { to { transform: rotate(360deg); } }
	@media (prefers-reduced-motion: reduce) { .spin { animation: none; border-style: dotted; border-right-color: currentColor; } }
	.qa-hint { margin: 0; display: flex; flex-wrap: wrap; gap: var(--space-2); align-items: center; color: var(--color-muted); font-size: var(--text-sm); }
	.chip { font-family: var(--font-mono); font-size: var(--text-xs); font-weight: var(--weight-bold); text-transform: uppercase; color: var(--color-success); border: 1px solid var(--color-border-control); border-radius: var(--radius-sm); padding: 0 var(--space-2); }
	.qa-status { margin: 0; min-height: 1.5em; color: var(--color-text-body); }
	.qa-status.is-ok { color: var(--color-text); }
	.qa-status.is-ok::before { content: '✓ '; color: var(--color-success); }
	.qa-status.is-error { color: var(--color-danger); }
	.qa-status.is-error::before { content: '✕ '; }
</style>
