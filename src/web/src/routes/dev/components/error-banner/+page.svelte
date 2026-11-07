<script lang="ts">
	// Fixture for ErrorBanner, exercised by src/web/tests/components_a11y.py.
	// The layout's <main id="main" tabindex="-1"> is the dismiss-focus fallback target, so this
	// page needs no extra landmark of its own.
	import ErrorBanner from '#lib/components/ErrorBanner.svelte';

	const severities = ['error', 'warning', 'info'] as const;
	const longDetail =
		'GET https://api.example.test/v3/decks/12345 returned 503 Service Unavailable after 3 attempts: upstream connect error or disconnect/reset before headers. reset reason: connection termination';

	// Written with an escaped slash so the markup stays inside this script block.
	const markupDetail = '<script>alert(1)<\/script> <b>not bold</b>';

	let resolved = $state(0);
	let rejected = $state(0);
	const slowOk = async () => {
		await new Promise((r) => setTimeout(r, 1000));
		resolved += 1;
	};
	const alwaysFails = async () => {
		await new Promise((r) => setTimeout(r, 500));
		rejected += 1;
		throw new Error('still down');
	};

	// A form-style banner that the caller mounts and unmounts (it is the live region).
	let formError = $state(false);
	let boundOpen = $state(true);
</script>

<svelte:head><title>ErrorBanner — Component fixtures — Commander Lab</title></svelte:head>

<div class="fixture">
	<h1>ErrorBanner</h1>

	<section aria-labelledby="h-plain">
		<h2 id="h-plain">Each severity, message only</h2>
		{#each severities as severity (severity)}
			<ErrorBanner {severity} message="Deck data unavailable" class="gap" />
		{/each}
	</section>

	<section aria-labelledby="h-detail">
		<h2 id="h-detail">With detail</h2>
		{#each severities as severity (severity)}
			<ErrorBanner {severity} message="Couldn't load printings" detail="Server said 503." class="gap" />
		{/each}
	</section>

	<section aria-labelledby="h-long">
		<h2 id="h-long">Long detail (collapses into Details)</h2>
		{#each severities as severity (severity)}
			<ErrorBanner {severity} message="Game data unavailable" detail={longDetail} class="gap" />
		{/each}
		<ErrorBanner message="Collapsed on request" detail="Short, but detailCollapsed is set." detailCollapsed class="gap" />
		<ErrorBanner message="Markup is text" detail={markupDetail} class="gap" />
	</section>

	<section aria-labelledby="h-retry">
		<h2 id="h-retry">Retry</h2>
		<ErrorBanner message="Deck data unavailable" detail="Resolves after 1s." onRetry={slowOk} retryLabel="Reload decks" class="gap" />
		<p class="meta">Resolved {resolved} time(s).</p>
		<ErrorBanner severity="warning" message="Inventory unavailable" detail="Always rejects after 0.5s." onRetry={alwaysFails} class="gap" />
		<p class="meta">Rejected {rejected} time(s).</p>
		<ErrorBanner message="Controlled retrying" onRetry={() => {}} retrying class="gap" />
	</section>

	<section aria-labelledby="h-dismiss">
		<h2 id="h-dismiss">Dismissible</h2>
		{#each severities as severity (severity)}
			<ErrorBanner {severity} message="Couldn't save your changes" dismissible class="gap" />
		{/each}
		<form onsubmit={(e) => { e.preventDefault(); formError = true; }}>
			<button type="submit" class="btn btn--primary">Create draft</button>
			{#if formError}
				<ErrorBanner message="Couldn't create draft" detail="Name is required." dismissible onDismiss={() => (formError = false)} class="gap" />
			{/if}
		</form>
		<p class="meta">Dismissing the form banner returns focus to the Create draft button.</p>
		<button type="button" class="btn btn--secondary" onclick={() => (boundOpen = true)}>Reopen bound banner</button>
		<ErrorBanner bind:open={boundOpen} severity="info" message="bind:open demo" dismissible class="gap" />
		<p class="meta">Bound open: {boundOpen}</p>
	</section>

	<section aria-labelledby="h-actions">
		<h2 id="h-actions">Actions snippet</h2>
		<ErrorBanner message="Review failed" detail="The worker exited with code 1." onRetry={slowOk} retryLabel="Re-run review" dismissible class="gap">
			{#snippet actions()}
				<a class="btn btn--secondary" href="/dev/components">Open fixtures</a>
			{/snippet}
		</ErrorBanner>
		<ErrorBanner severity="info" message="Children replace detail" class="gap">
			<span>Rich content with a <a href="/dev/components">link</a>.</span>
		</ErrorBanner>
	</section>
</div>

<style>
	.fixture { padding: var(--space-4); max-width: var(--content-max); margin: 0 auto; }
	section { margin-bottom: var(--space-6); }
	.fixture :global(.gap) { margin-block: var(--space-3); }
</style>
