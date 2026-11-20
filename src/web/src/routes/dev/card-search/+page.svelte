<script lang="ts">
	// Dev harness for the CardSearch module (docs/ui/card-search.md). No backend: a fixture fetcher answers
	// /api/card-search and /api/card-autocomplete, and a fixture onadd records what was added.
	// Two instances: plain, and preset="commander" (Can be commander chip pressed in the bar).
	import CardSearch from '#lib/card-search/CardSearch.svelte';
	import type { AddRequest, CardHit, Color } from '#lib/card-search/types.ts';

	const mk = (name: string, o: Partial<CardHit>): CardHit => ({
		oracle_id: `fx-${name.toLowerCase().replace(/\W+/g, '-')}`, name, mana_cost: '', cmc: 0, type_line: 'Artifact', color_identity: [],
		rarity: 'uncommon', oracle_text: '', image: '', owned_qty: 0, free_qty: 0, can_be_commander: false, pairing: null, ...o
	});
	const CARDS: CardHit[] = [
		mk('Sol Ring', { mana_cost: '{1}', cmc: 1, owned_qty: 2, free_qty: 1, rarity: 'uncommon' }),
		mk('Arcane Signet', { mana_cost: '{2}', cmc: 2, owned_qty: 1, free_qty: 1, rarity: 'common' }),
		mk('Atraxa, Praetors’ Voice', { mana_cost: '{G}{W}{U}{B}', cmc: 4, type_line: 'Legendary Creature — Phyrexian Angel Horror', color_identity: ['W', 'U', 'B', 'G'], rarity: 'mythic', can_be_commander: true, owned_qty: 1, free_qty: 0 }),
		mk('Esika’s Chariot', { mana_cost: '{3}{G}', cmc: 4, type_line: 'Legendary Artifact — Vehicle', color_identity: ['G'], rarity: 'rare', can_be_commander: true }),
		mk('Akiri, Line-Slinger', { mana_cost: '{1}{R}{W}', cmc: 3, type_line: 'Legendary Creature — Kor Soldier', color_identity: ['R', 'W'], rarity: 'rare', can_be_commander: true, pairing: 'partner' }),
		mk('Agent of the Iron Throne', { mana_cost: '{2}{B}', cmc: 3, type_line: 'Legendary Enchantment — Background', color_identity: ['B'], rarity: 'rare', can_be_commander: true, pairing: 'background' }),
		mk('Grist, the Hunger Tide', { mana_cost: '{1}{B}{G}', cmc: 3, type_line: 'Legendary Planeswalker — Grist', color_identity: ['B', 'G'], rarity: 'mythic', can_be_commander: true }),
		mk('Swords to Plowshares', { mana_cost: '{W}', cmc: 1, type_line: 'Instant', color_identity: ['W'], rarity: 'uncommon', owned_qty: 3, free_qty: 3 }),
		mk('Counterspell', { mana_cost: '{U}{U}', cmc: 2, type_line: 'Instant', color_identity: ['U'], rarity: 'uncommon' }),
		mk('Llanowar Elves', { mana_cost: '{G}', cmc: 1, type_line: 'Creature — Elf Druid', color_identity: ['G'], rarity: 'common', owned_qty: 4, free_qty: 2 })
	];

	const identity: Color[] = ['W', 'U', 'B', 'G'];
	let added = $state<{ plain: AddRequest[]; commander: AddRequest[] }>({ plain: [], commander: [] });
	let inList = $state(new Map<string, number>());

	const json = (b: unknown) => new Response(JSON.stringify(b), { headers: { 'content-type': 'application/json' } });
	// Fixture backend: only the parts of the filter the harness needs.
	const fetcher: typeof fetch = async (input) => {
		const url = new URL(String(input), 'http://fixture.local');
		await new Promise((r) => setTimeout(r, 120));
		const q = (url.searchParams.get('q') ?? url.searchParams.get('name') ?? '').toLowerCase();
		const cmdr = url.searchParams.get('cmdr') === '1' || url.searchParams.get('commanders_only') === 'true';
		const type = (url.searchParams.get('type') ?? '').split(',').filter(Boolean);
		let hits = CARDS.filter((c) => c.name.toLowerCase().includes(q));
		if (cmdr) hits = hits.filter((c) => c.can_be_commander);
		if (type.length) hits = hits.filter((c) => type.some((t) => c.type_line.toLowerCase().includes(t)));
		if (url.pathname === '/api/card-autocomplete') {
			const limit = Number(url.searchParams.get('limit') ?? 8);
			return json({ results: hits.slice(0, limit).map((c) => ({ ...c, uid: c.oracle_id, colors: c.color_identity, keywords: [] })), source: 'cache', degraded: false });
		}
		return json({ results: hits, page: 1, has_more: false, total: hits.length, query: `legal:commander${cmdr ? ' is:commander' : ''}`, source: 'cache', degraded: false });
	};

	async function addPlain(req: AddRequest) {
		if (req.card.name === 'Counterspell') throw new Error('Counterspell is outside the commander’s identity (fixture rejection)');
		added.plain = [...added.plain, req];
		inList = new Map(inList).set(req.card.oracle_id, (inList.get(req.card.oracle_id) ?? 0) + req.qty);
	}
	async function addCommander(req: AddRequest) {
		added.commander = [...added.commander, req];
	}
</script>

<svelte:head><title>CardSearch — Dev harness — Commander Lab</title></svelte:head>

<div class="harness">
	<h1>CardSearch</h1>
	<p class="meta">
		Fixture backend (no network). Try <kbd>Ctrl</kbd>+<kbd>'</kbd>, type “sol”, <kbd>Enter</kbd> in the plain instance;
		type “2x atraxa” in the commander instance. Counterspell is rejected by the fixture <code>onadd</code>.
	</p>

	<section aria-labelledby="h-plain" data-testid="plain">
		<h2 id="h-plain">Plain</h2>
		<CardSearch onadd={addPlain} {fetcher} {inList} {identity} boards={['main', 'sideboard', 'maybeboard']} />
		<ol data-testid="added-plain">
			{#each added.plain as a, i (i)}<li>{a.qty}× {a.card.name} → {a.board}</li>{/each}
		</ol>
	</section>

	<section aria-labelledby="h-cmdr" data-testid="commander">
		<h2 id="h-cmdr">Commander preset</h2>
		<CardSearch onadd={addCommander} {fetcher} preset="commander" hotkey={false} />
		<ol data-testid="added-commander">
			{#each added.commander as a, i (i)}<li>{a.qty}× {a.card.name} → {a.board}</li>{/each}
		</ol>
	</section>
</div>

<style>
	.harness { padding: var(--space-4); max-width: var(--content-max); margin: 0 auto; display: flex; flex-direction: column; gap: var(--space-3); }
	h1, h2 { margin: 0; }
	section { display: flex; flex-direction: column; gap: var(--space-2); }
	kbd { font-family: var(--font-mono); border: 1px solid var(--color-border-control); border-radius: var(--radius-sm); padding: 0 var(--space-1); }
</style>
