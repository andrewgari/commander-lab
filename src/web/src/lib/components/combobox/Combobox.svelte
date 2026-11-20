<script lang="ts" generics="T">
	// Accessible async combobox (WAI-ARIA APG "list autocomplete with manual selection").
	// The host supplies `search`; this component owns debounce, stale-request cancellation,
	// states, keyboard handling and ARIA. The internal value never reaches the DOM as text.
	import { onDestroy } from 'svelte';
	import { SEARCH, nextIndex, statusText, type ComboOption, type ComboStatus } from './model.ts';

	interface Props {
		label: string;
		placeholder?: string;
		required?: boolean;
		/** Message for the native validity bubble while nothing is selected. */
		requiredMessage?: string;
		search: (term: string, signal: AbortSignal) => Promise<ComboOption<T>[]>;
		selected?: ComboOption<T> | null;
		debounceMs?: number;
		minChars?: number;
	}

	let {
		label,
		placeholder = '',
		required = false,
		requiredMessage = 'Choose an option from the list',
		search,
		selected = $bindable(null),
		debounceMs = SEARCH.debounceMs,
		minChars = SEARCH.minChars
	}: Props = $props();

	const uid = $props.id();
	const listId = `${uid}-list`;
	const statusId = `${uid}-status`;
	const optionId = (key: string) => `${uid}-opt-${key}`;

	let input: HTMLInputElement;
	let text = $state('');
	let open = $state(false);
	let status = $state<ComboStatus>('idle');
	let options = $state<ComboOption<T>[]>([]);
	let active = $state(-1);
	let error = $state('');
	let timer: ReturnType<typeof setTimeout> | undefined;
	let controller: AbortController | undefined;
	let seq = 0;

	const showList = $derived(open && status === 'results' && options.length > 0);
	const message = $derived(open ? statusText(status, text, minChars, error) : null);
	const activeId = $derived(showList && active >= 0 ? optionId(options[active].key) : undefined);
	const announce = $derived(
		showList
			? `${options.length} result${options.length === 1 ? '' : 's'} available`
			: (statusText(status, text, minChars, error) ?? '')
	);

	// Keep the native constraint validation honest: typed-but-unselected text is not a value.
	$effect(() => {
		input?.setCustomValidity(required && !selected ? requiredMessage : '');
	});

	function cancel() {
		clearTimeout(timer);
		controller?.abort();
		controller = undefined;
		seq++;
	}

	function run(term: string) {
		cancel();
		const trimmed = term.trim();
		options = [];
		active = -1;
		if (trimmed.length === 0) {
			status = 'idle';
			return;
		}
		if (trimmed.length < minChars) {
			status = 'short';
			return;
		}
		status = 'loading';
		const mine = seq;
		timer = setTimeout(async () => {
			controller = new AbortController();
			const { signal } = controller;
			try {
				const found = await search(trimmed, signal);
				if (mine !== seq || signal.aborted) return;
				options = found;
				status = found.length ? 'results' : 'empty';
			} catch (e) {
				if (mine !== seq || signal.aborted || (e instanceof DOMException && e.name === 'AbortError')) return;
				error = e instanceof Error ? e.message : '';
				status = 'error';
			}
		}, debounceMs);
	}

	function oninput() {
		selected = null; // editing invalidates the previous choice
		open = true;
		run(text);
	}

	function choose(i: number) {
		const opt = options[i];
		if (!opt) return;
		cancel();
		selected = opt;
		text = opt.label;
		open = false;
		status = 'idle';
		options = [];
		active = -1;
	}

	function onkeydown(e: KeyboardEvent) {
		switch (e.key) {
			case 'ArrowDown':
			case 'ArrowUp':
				e.preventDefault();
				if (!open) {
					open = true;
					if (status === 'idle' && !selected) run(text);
				}
				if (showList) active = nextIndex(e.key, active, options.length);
				break;
			case 'Home':
			case 'End':
				if (showList) {
					e.preventDefault();
					active = nextIndex(e.key, active, options.length);
				}
				break;
			case 'Enter':
				// Never submit the surrounding form while the list is open.
				if (open) e.preventDefault();
				if (showList && active >= 0) choose(active);
				break;
			case 'Escape':
				if (open) {
					e.preventDefault();
					open = false;
					active = -1;
				} else if (text) {
					e.preventDefault();
					text = '';
					selected = null;
					run('');
				}
				break;
			case 'Tab':
				// Focus moves on naturally; the popup closes and nothing is selected implicitly.
				open = false;
				active = -1;
				break;
		}
	}

	function onfocusout(e: FocusEvent) {
		const next = e.relatedTarget as Node | null;
		if (!next || !(e.currentTarget as HTMLElement).contains(next)) {
			open = false;
			active = -1;
		}
	}

	onDestroy(cancel);
</script>

<div class="combo" onfocusout={onfocusout}>
	<label for="{uid}-input">{label}</label>
	<input
		bind:this={input}
		id="{uid}-input"
		type="text"
		role="combobox"
		autocomplete="off"
		spellcheck="false"
		aria-expanded={open && (showList || message !== null)}
		aria-controls={listId}
		aria-autocomplete="list"
		aria-activedescendant={activeId}
		aria-describedby={statusId}
		aria-required={required || undefined}
		{placeholder}
		{required}
		bind:value={text}
		{oninput}
		{onkeydown}
		onfocus={() => {
			if (text.trim() && !selected) open = true;
		}}
	/>
	{#if open && (showList || message)}
		<div class="popup">
			{#if message}
				<p class="state" aria-hidden="true" class:state-error={status === 'error'}>{message}</p>
			{/if}
		</div>
	{/if}
	<ul id={listId} role="listbox" aria-label={label} class="list" hidden={!showList}>
		{#each options as opt, i (opt.key)}
			<!-- svelte-ignore a11y_click_events_have_key_events -->
			<!-- Pointer selection only; keyboard users act through the input (aria-activedescendant). -->
			<li
				id={optionId(opt.key)}
				role="option"
				aria-selected={i === active}
				class:active={i === active}
				onmousedown={(e) => e.preventDefault()}
				onclick={() => choose(i)}
			>
				<span class="name">{opt.label}</span>
				{#if opt.detail}<span class="detail">{opt.detail}</span>{/if}
			</li>
		{/each}
	</ul>
	<div id={statusId} class="visually-hidden" role="status" aria-live="polite">{announce}</div>
</div>

<style>
	.combo {
		position: relative;
		display: grid;
		gap: 0.15rem;
		font-size: 0.85rem;
		min-width: 16rem;
		flex: 1 1 16rem;
	}
	input {
		min-height: var(--touch-min, 2.75rem);
	}
	.popup,
	.list {
		position: absolute;
		top: 100%;
		left: 0;
		right: 0;
		z-index: 10;
		margin: 0.15rem 0 0;
		background: var(--color-surface-raised);
		border: 1px solid var(--color-border-control);
	}
	.list {
		list-style: none;
		padding: 0;
		max-height: 18rem;
		overflow-y: auto;
	}
	.list[hidden] {
		display: none;
	}
	.state {
		margin: 0;
		padding: 0.6rem 0.75rem;
		color: var(--color-muted);
	}
	.state-error {
		color: var(--color-danger);
	}
	li {
		display: grid;
		gap: 0.1rem;
		padding: 0.5rem 0.75rem;
		min-height: var(--touch-min, 2.75rem);
		cursor: pointer;
		border-left: 3px solid transparent;
	}
	li.active {
		background: var(--color-surface);
		border-left-color: var(--color-accent);
	}
	li:hover {
		background: var(--color-surface);
	}
	.name {
		color: var(--color-text);
	}
	.detail {
		color: var(--color-muted);
		font-size: 0.8rem;
	}
</style>
