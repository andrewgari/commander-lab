<script lang="ts">
	// Power-level readout (metrics.power): bracket, 1-10 meter, Game Changers.
	// Missing data renders "unavailable" + reason (AGENT_RULES.md §4).
	interface Props {
		power: {
			available?: boolean;
			reason?: string | null;
			estimated_bracket?: number | null;
			bracket_name?: string | null;
			power_level?: number | null;
			power_source?: string | null;
			game_changers?: number;
			notes?: string[];
		} | null;
	}

	let { power }: Props = $props();

	const level = $derived(power?.available ? (power.power_level ?? null) : null);
	const pct = $derived(level !== null ? Math.min(100, Math.max(0, (level / 10) * 100)) : 0);
</script>

{#if !power || !power.available}
	<p class="unavailable">Power level unavailable: {power?.reason ?? 'power not computed'}.</p>
{:else}
	<div class="power">
		<div class="headline">
			<strong>Bracket {power.estimated_bracket}</strong>
			{#if power.bracket_name}<span class="bracket-name">({power.bracket_name})</span>{/if}
			· power {level}/10
			{#if power.game_changers}
				· {power.game_changers} Game Changer{power.game_changers === 1 ? '' : 's'}
			{/if}
		</div>
		<div class="meter" role="img" aria-label="power level {level} out of 10">
			<div class="fill" style:width="{pct}%"></div>
		</div>
		{#each power.notes ?? [] as note, i (i)}
			<p class="note">{note}</p>
		{/each}
	</div>
{/if}

<style>
	.headline {
		font-size: 0.95rem;
	}
	.bracket-name {
		color: var(--text-3, var(--text-muted));
	}
	.meter {
		margin-top: 0.4rem;
		height: 0.7rem;
		max-width: 20rem;
		background: rgba(127, 127, 127, 0.18);
		border-radius: 3px;
		overflow: hidden;
	}
	.fill {
		height: 100%;
		background: linear-gradient(90deg, #4a7dbd, #c0392b);
		border-radius: 3px;
	}
	.note {
		font-size: 0.75rem;
		color: var(--text-3, var(--text-muted));
		margin: 0.25rem 0 0;
	}
	.unavailable {
		color: var(--text-3, var(--text-muted));
		font-size: 0.85rem;
	}
</style>
