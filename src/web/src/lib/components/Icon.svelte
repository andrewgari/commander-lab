<script lang="ts">
	import { ICON_PATHS, type IconName } from '#lib/icons.ts';

	let { name, size = '1em', class: className }: { name: IconName; size?: string; class?: string } = $props();
</script>

<svg
	class={['icon', name === 'spinner' && 'icon--spin', className]}
	width={size}
	height={size}
	viewBox="0 0 16 16"
	fill="none"
	stroke="currentColor"
	stroke-width="1.5"
	stroke-linecap="round"
	stroke-linejoin="round"
	aria-hidden="true"
	focusable="false"
	data-icon={name}
>
	<path d={ICON_PATHS[name]} />
</svg>

<style>
	.icon { flex: none; vertical-align: -0.125em; }
	/* The spinner is a static arc unless the user allows motion. */
	@media (prefers-reduced-motion: no-preference) {
		.icon--spin { animation: icon-spin 0.9s linear infinite; }
	}
	@keyframes icon-spin { to { transform: rotate(360deg); } }
</style>
