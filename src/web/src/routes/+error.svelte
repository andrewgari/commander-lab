<script lang="ts">
	import { page } from '$app/state';
	import ErrorBanner from '#lib/components/ErrorBanner.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
</script>

<svelte:head>
	<title>{page.status === 404 ? 'Not found' : 'Something went wrong'} — Commander Lab</title>
</svelte:head>

{#if page.status === 404}
	<EmptyState title="Page not found" description={page.error?.message ?? 'There is nothing at this address.'} ctaLabel="Back to Home" ctaHref="/" />
{:else}
	<ErrorBanner message="This page couldn't be displayed" detail={page.error?.message ?? `Error ${page.status}`} onRetry={() => location.reload()}>
		{#snippet actions()}<a class="btn btn--secondary" href="/">Back to Home</a>{/snippet}
	</ErrorBanner>
{/if}
