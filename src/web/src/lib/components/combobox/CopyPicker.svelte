<script lang="ts">
	// Physical-copy search for the draft "Stage move" form. Submits instance_id as a hidden
	// field; the options show card, printing, condition and where the copy currently is.
	import Combobox from './Combobox.svelte';
	import { SEARCH, copyOption, copyStatusesFor, type ComboOption, type InstanceLike } from './model.ts';

	let {
		action = 'assign',
		selected = $bindable(null)
	}: { action?: string; selected?: ComboOption<string> | null } = $props();

	// A copy that is invalid for the newly chosen action must not stay selected.
	let lastAction: string | undefined;
	$effect(() => {
		const current = action;
		if (lastAction !== undefined && current !== lastAction) selected = null;
		lastAction = current;
	});

	async function search(term: string, signal: AbortSignal) {
		const qs = new URLSearchParams({ q: term });
		const res = await fetch(`/api/card-copies?${qs}`, { signal });
		if (!res.ok) throw new Error(`HTTP ${res.status}`);
		const body = (await res.json()) as { instances: InstanceLike[] };
		const allowed = copyStatusesFor(action);
		return body.instances
			.filter((i) => allowed.includes(i.ownership_status))
			.slice(0, SEARCH.limit)
			.map(copyOption);
	}
</script>

<Combobox
	label="Copy"
	placeholder="Search your copies by card name"
	required
	requiredMessage="Choose a copy from the list"
	{search}
	bind:selected
/>
<input type="hidden" name="instance_id" value={selected?.value ?? ''} />
