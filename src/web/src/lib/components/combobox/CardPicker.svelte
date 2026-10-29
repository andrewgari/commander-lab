<script lang="ts">
	// Card search for the draft "Add card" form. Submits oracle_id + name as hidden fields;
	// the user only ever sees and types card names.
	import Combobox from './Combobox.svelte';
	import { SEARCH, cardOption, type CardChoice, type CardHitLike, type ComboOption } from './model.ts';

	let { selected = $bindable(null) }: { selected?: ComboOption<CardChoice> | null } = $props();

	async function search(term: string, signal: AbortSignal) {
		const qs = new URLSearchParams({ name: term, page_size: String(SEARCH.limit) });
		const res = await fetch(`/api/card-search?${qs}`, { signal });
		if (!res.ok) throw new Error(`HTTP ${res.status}`);
		const body = (await res.json()) as { results: CardHitLike[] };
		return body.results.map(cardOption);
	}
</script>

<Combobox
	label="Card"
	placeholder="Search cards by name"
	required
	requiredMessage="Choose a card from the list"
	{search}
	bind:selected
/>
<input type="hidden" name="oracle_id" value={selected?.value.oracleId ?? ''} />
<input type="hidden" name="name" value={selected?.value.name ?? ''} />
