<script lang="ts">
	// Fixture for the C0 primitives, exercised by src/web/tests/components_a11y.py.
	import Icon from '#lib/components/Icon.svelte';
	import { ICON_NAMES } from '#lib/icons.ts';

	const variants = ['secondary', 'primary', 'success', 'danger', 'link-danger'] as const;
	const surfaces = [
		{ id: 'bg', label: 'On background', cls: 'on-bg' },
		{ id: 'surface', label: 'On surface', cls: 'on-surface' },
		{ id: 'raised', label: 'On raised surface', cls: 'on-raised' }
	];
	const tints = [
		{ id: 'danger', label: 'Danger tint', word: 'Error' },
		{ id: 'warning', label: 'Warning tint', word: 'Warning' },
		{ id: 'info', label: 'Info tint', word: 'Notice' }
	];
</script>

<svelte:head><title>Primitives — Component fixtures — Commander Lab</title></svelte:head>

<div class="fixture">
	<h1>Primitives</h1>

	<section aria-labelledby="h-btn">
		<h2 id="h-btn">Buttons</h2>
		{#each surfaces as s (s.id)}
			<div class="panel {s.cls}" data-testid="btn-{s.id}">
				<h3>{s.label}</h3>
				<div class="row">
					{#each variants as v (v)}
						<a class="btn btn--{v}" href="/dev/components/primitives">Link {v}</a>
						<button type="button" class="btn btn--{v}">Button {v}</button>
					{/each}
				</div>
			</div>
		{/each}
		<div class="panel on-surface">
			<h3>Busy state (aria-disabled)</h3>
			<div class="row">
				{#each variants as v (v)}
					<button type="button" class="btn btn--{v}" aria-disabled="true"><Icon name="spinner" />Busy {v}</button>
				{/each}
			</div>
		</div>
		<div class="panel on-surface">
			<h3>Buttons with icons</h3>
			<div class="row">
				<button type="button" class="btn btn--success"><Icon name="check" />Save</button>
				<button type="button" class="btn btn--danger"><Icon name="ban" />Remove</button>
				<button type="button" class="btn btn--secondary"><Icon name="close" />Cancel</button>
			</div>
		</div>
	</section>

	<section aria-labelledby="h-icons">
		<h2 id="h-icons">Icons</h2>
		<ul class="icons" data-testid="icons">
			{#each ICON_NAMES as name (name)}
				<li><Icon {name} size="1.5rem" /><span class="meta">{name}</span></li>
			{/each}
		</ul>
	</section>

	<section aria-labelledby="h-meta">
		<h2 id="h-meta">Meta text</h2>
		{#each surfaces as s (s.id)}
			<div class="panel {s.cls}"><p class="meta">Meta text {s.label.toLowerCase()}</p></div>
		{/each}
	</section>

	<section aria-labelledby="h-tints">
		<h2 id="h-tints">Tints</h2>
		{#each tints as t (t.id)}
			<div class="tint tint--{t.id}" data-testid="tint-{t.id}">
				<span class="rule" aria-hidden="true"></span>
				<div>
					<p class="tint-text"><strong>{t.word}:</strong> {t.label} with primary text</p>
					<p class="tint-body">Body text on the {t.id} tint</p>
					<p class="meta">Muted text on the {t.id} tint</p>
					<button type="button" class="btn btn--secondary">Focusable on tint</button>
				</div>
			</div>
		{/each}
	</section>
</div>

<style>
	.fixture { padding: var(--space-4); max-width: var(--content-max); margin: 0 auto; }
	.row { display: flex; flex-wrap: wrap; gap: var(--space-3); align-items: center; }
	.panel { padding: var(--space-4); margin-bottom: var(--space-3); border-radius: var(--radius-sm); }
	.on-bg { background: var(--color-bg); border: 1px solid var(--color-border); }
	.on-surface { background: var(--color-surface); }
	.on-raised { background: var(--color-surface-raised); }
	.icons { display: flex; flex-wrap: wrap; gap: var(--space-4); list-style: none; margin: 0; padding: 0; color: var(--color-text); }
	.icons li { display: flex; align-items: center; gap: var(--space-2); }
	.tint { display: flex; gap: var(--space-3); padding: var(--space-3) var(--space-4); margin-bottom: var(--space-3); border-radius: var(--radius-sm); }
	.tint p { margin: 0 0 var(--space-2); }
	.tint-text { color: var(--color-text); }
	.tint-body { color: var(--color-text-body); }
	.rule { flex: none; width: 4px; align-self: stretch; }
	.tint--danger { background: var(--color-danger-bg); }
	.tint--danger .rule { background: var(--color-danger); }
	.tint--warning { background: var(--color-warning-bg); }
	.tint--warning .rule { background: var(--color-warning); }
	.tint--info { background: var(--color-info-bg); }
	.tint--info .rule { background: var(--color-info); }
</style>
