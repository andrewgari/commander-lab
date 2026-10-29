<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import ReviewReport from '#lib/components/review/ReviewReport.svelte';
	import SwapFeedback from '#lib/components/review-page/SwapFeedback.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const active = $derived(data.print ? null : data.list.active);
	const viewing = $derived(data.print ? null : data.row);
	let busy = $state(false);
	let pdfState = $state<'idle' | 'working' | 'error'>('idle');
	let pdfMessage = $state('');

	// A running review is polled every 2 s; the job itself runs on the server, so leaving the page loses nothing.
	$effect(() => {
		if (!active) return;
		const t = setInterval(() => invalidateAll(), 2000);
		return () => clearInterval(t);
	});

	function elapsed(from: string | null) {
		if (!from) return '';
		const s = Math.max(0, Math.round((Date.now() - new Date(from).getTime()) / 1000));
		return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
	}
	let now = $state(Date.now());
	$effect(() => {
		if (!active) return;
		const t = setInterval(() => (now = Date.now()), 1000);
		return () => clearInterval(t);
	});

	let genError = $state('');
	async function generate() {
		busy = true;
		genError = '';
		try {
			const res = await fetch(`/decks/${encodeURIComponent(data.deckId)}/review/generate`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
			if (!res.ok) throw new Error((await res.json().catch(() => ({})))?.message ?? res.statusText);
			await invalidateAll();
		} catch (e) {
			genError = e instanceof Error ? e.message : 'Could not start the review';
		} finally {
			busy = false;
		}
	}

	async function exportPdf() {
		if (!viewing) return;
		pdfState = 'working';
		try {
			const res = await fetch(`/decks/${encodeURIComponent(data.deckId)}/review/pdf?doc=${viewing.id}`);
			if (!res.ok) throw new Error(await res.text());
			const blob = await res.blob();
			const a = document.createElement('a');
			a.href = URL.createObjectURL(blob);
			a.download = res.headers.get('content-disposition')?.match(/filename="([^"]+)"/)?.[1] ?? `review-${viewing.id}.pdf`;
			a.click();
			URL.revokeObjectURL(a.href);
			pdfState = 'idle';
		} catch (e) {
			pdfState = 'error';
			pdfMessage = e instanceof Error ? e.message : 'PDF export failed';
		}
	}
	const when = (iso: string | null) => (iso ? iso.replace('T', ' ').slice(0, 16) + ' UTC' : '');
</script>

<svelte:head>
	<title>{data.print ? 'Scouting report' : 'Deck review'} — Commander Lab</title>
</svelte:head>

{#if data.print}
	<main><ReviewReport document={data.document} theme="light" print={true} /></main>
{:else}
	<form class="review-preview review-bar" onsubmit={(e) => { e.preventDefault(); generate(); }}>
		<a href={`/decks/${encodeURIComponent(data.deckId)}`}>← deck</a>
		<strong>Deck review</strong>
		{#if data.list.documents.length}
			<label>Review
				<select aria-label="Review history" onchange={(e) => (window.location.search = `?doc=${e.currentTarget.value}`)} value={viewing?.id}>
					{#each data.list.documents as d}
						<option value={d.id}>#{d.id} · {when(d.created_at)} · {d.status === 'ok' ? `grade ${d.grade ?? '?'}` : d.status}</option>
					{/each}
				</select>
			</label>
		{/if}
		<button type="submit" disabled={busy || !!active}>{data.document ? 'Generate new review' : 'Generate review'}</button>
		{#if viewing?.status === 'ok'}
			<button type="button" onclick={exportPdf} disabled={pdfState === 'working'}>{pdfState === 'working' ? 'Preparing PDF…' : 'Export PDF'}</button>
		{/if}
		{#if pdfState === 'error'}<span class="review-error" role="alert">PDF export failed: {pdfMessage}</span>{/if}
		{#if genError}<span class="review-error" role="alert">{genError}</span>{/if}
	</form>

	{#if active}
		<section class="review-status" role="status" aria-live="polite">
			<span class="eyebrow">Review #{active.id} · {active.status}</span>
			<strong data-testid="review-stage">{active.stage || 'Starting'}</strong>
			<span class="muted" data-now={now}>{elapsed(active.started_at ?? active.created_at)} elapsed · a review takes a few minutes; you can leave this page and come back.</span>
		</section>
	{/if}

	{#if viewing?.status === 'failed'}
		<section class="review-status failed" role="alert">
			<span class="eyebrow">Review #{viewing.id} failed</span>
			<strong>{viewing.error}</strong>
			<span class="muted">Nothing was shown from this run. Generate a new review to try again.</span>
		</section>
	{/if}

	{#if data.document}
		<main>
			<ReviewReport document={data.document} theme="dark">
				{#snippet swapActions(swap)}
					<SwapFeedback documentId={viewing!.id} deckId={data.deckId} {swap} saved={data.feedback[swap.id]} />
				{/snippet}
			</ReviewReport>
		</main>
	{:else if !active && viewing?.status !== 'failed'}
		<section class="review-status">
			<strong>No review yet for this deck.</strong>
			<span class="muted">Generate one: it reads the deck, your collection and your profile, and takes a few minutes.</span>
		</section>
	{/if}
{/if}

<style>
	:global(body:has(.review-report)), :global(body:has(.review-bar)) { margin: 0; background: #0e1216; }
	:global(body:has(.print-mode)) { background: white; }
	@media print { :global(body:has(.review-report)) { background: white; } .review-bar, .review-status { display: none; } }
	.review-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
	.review-status { display: flex; flex-direction: column; gap: 4px; max-width: 1280px; margin: 16px auto; padding: 16px 20px; border: 1px solid #29323b; border-left: 3px solid #f0a63a; color: #b6bfc8; background: #141a1f; }
	.review-status.failed { border-left-color: #b4443c; }
	.review-status strong { color: #e8ebee; }
	.review-error { color: #e5766d; font-size: 13px; }
</style>
