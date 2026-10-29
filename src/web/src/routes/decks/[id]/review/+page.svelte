<script lang="ts">
	import { untrack } from 'svelte';
	import { goto, invalidateAll } from '$app/navigation';
	import { navigating } from '$app/state';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import ErrorBanner from '#lib/components/ErrorBanner.svelte';
	import ReviewReport from '#lib/components/review/ReviewReport.svelte';
	import SwapFeedback from '#lib/components/review-page/SwapFeedback.svelte';
	import {
		MAX_POLL_FAILURES, derive, failureHeadline, historyLabel, partialSections, pollDelay, reasonOf, transition
	} from '#lib/review/reviewState.ts';
	import type { ReviewDocumentSummary } from '#lib/api.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	// Client-held poll state (spec S5/S8/S9): the last active run survives a failed poll.
	let lastActive = $state<ReviewDocumentSummary | null>(null);
	let pollFailures = $state(0);
	let announcement = $state('');
	let busy = $state(false);
	let pdfState = $state<'idle' | 'working' | 'error'>('idle');
	let pdfMessage = $state('');
	let pdfDismissed = $state(false);
	let genError = $state('');
	let reconnecting = $state(false);

	const view = $derived(data.print ? null : derive({ list: data.list, row: data.row, feedback: data.feedback }, { lastActive, pollFailures }));
	const active = $derived(view?.active ?? null);
	const shownActive = $derived(active ?? lastActive);
	const viewing = $derived(view?.viewing ?? null);
	const has = (id: string) => (view?.states as string[] | undefined)?.includes(id) ?? false;
	const lost = $derived(!!lastActive && !data.print && !data.list.ok);
	const gaveUp = $derived(lost && pollFailures >= MAX_POLL_FAILURES);
	const partial = $derived(viewing?.status === 'ok' ? partialSections(viewing.doc) : null);
	const switching = $derived(navigating.to?.url.searchParams.get('doc') ?? null);
	const failure = $derived(failureHeadline(viewing?.error));
	const lastGood = $derived(view?.documents.find((d) => d.status === 'ok') ?? null);

	// Track list results: failed polls count, successes reset, and an active run ending announces and switches (S8/S9).
	$effect(() => {
		if (data.print) return;
		const list = data.list;
		untrack(() => {
			if (list.ok) {
				pollFailures = 0;
				const t = transition(lastActive, list);
				lastActive = list.value.active;
				if (t) {
					announcement = t.kind === 'failed' ? `Review #${t.id} failed.` : `Review #${t.id} is ready.`;
					void goto(`?doc=${t.id}`, { replace: true, reset: false });
				}
			} else if (lastActive) {
				pollFailures += 1;
			}
		});
	});

	// A running review is polled with backoff (2 s, 5 s, 10 s); after five failures it stops and offers Reconnect.
	const polling = $derived(!data.print && !!shownActive && pollFailures < MAX_POLL_FAILURES);
	$effect(() => {
		if (!polling) return;
		let stop = false;
		(async () => {
			while (!stop) {
				await new Promise((r) => setTimeout(r, pollDelay(untrack(() => pollFailures))));
				if (stop) break;
				try {
					await invalidateAll();
				} catch {
					// The page's own server is unreachable: the load never ran, so count the miss here.
					if (!stop) pollFailures += 1;
				}
			}
		})();
		return () => (stop = true);
	});

	async function reconnect() {
		reconnecting = true;
		pollFailures = 0;
		try {
			await invalidateAll();
		} catch {
			pollFailures = MAX_POLL_FAILURES;
		} finally {
			reconnecting = false;
		}
	}

	let now = $state(Date.now());
	$effect(() => {
		if (!shownActive) return;
		const t = setInterval(() => (now = Date.now()), 1000);
		return () => clearInterval(t);
	});
	function elapsed(from: string | null) {
		if (!from) return '';
		const s = Math.max(0, Math.round((now - new Date(from).getTime()) / 1000));
		return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
	}

	async function generate() {
		if (busy) return;
		busy = true;
		genError = '';
		try {
			const res = await fetch(`/decks/${encodeURIComponent(data.deckId)}/review/generate`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
			if (!res.ok) throw new Error((await res.json().catch(() => ({})))?.message ?? res.statusText);
			await invalidateAll();
		} catch (e) {
			genError = reasonOf(e, 'Could not start the review');
		} finally {
			busy = false;
		}
	}

	async function exportPdf() {
		if (!viewing) return;
		pdfState = 'working';
		pdfDismissed = false;
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
			pdfMessage = reasonOf(e, 'PDF export failed');
		}
	}
	const when = (iso: string | null) => (iso ? iso.replace('T', ' ').slice(0, 16) + ' UTC' : '');
	const clock = (iso: string | null) => (iso ? iso.replace('T', ' ').slice(11, 16) : '');
	const reload = () => invalidateAll();
	const view_ = (id: number) => goto(`?doc=${id}`, { reset: false });
	const decisionsLost = $derived(has('S12'));
</script>

<svelte:head>
	<title>{data.print ? 'Scouting report' : 'Deck review'} — Commander Lab</title>
</svelte:head>

{#if data.print}
	<div><ReviewReport document={data.document} theme="light" print={true} /></div>
{:else}
	<form class="review-preview review-bar" onsubmit={(e) => { e.preventDefault(); generate(); }}>
		<a href={`/decks/${encodeURIComponent(data.deckId)}`}>← deck</a>
		<strong>Deck review</strong>
		{#if view && view.documents.length}
			<label>Review
				<select aria-label="Review history" onchange={(e) => goto(`?doc=${encodeURIComponent(e.currentTarget.value)}`, { reset: false })} value={viewing?.id ?? shownActive?.id}>
					{#each view.documents as d (d.id)}
						<option value={d.id}>{historyLabel(d, when)}</option>
					{/each}
				</select>
			</label>
		{/if}
		{#if data.list.ok}
			<button type="submit" class:demoted={has('S6')} aria-disabled={busy || !!active || undefined} aria-describedby={active ? 'gen-why' : undefined}>
				{busy ? 'Starting…' : data.document ? 'Generate new review' : 'Generate review'}
			</button>
			{#if active}<span id="gen-why" class="why">A review is already running</span>{/if}
		{/if}
		{#if viewing?.status === 'ok'}
			<button type="button" onclick={exportPdf} aria-disabled={pdfState === 'working' || undefined}>{pdfState === 'working' ? 'Preparing PDF…' : 'Export PDF'}</button>
		{/if}
	</form>

	<div class="review-alerts">
		{#if genError}
			<ErrorBanner message="Couldn't start the review" detail={genError} onRetry={generate} retryLabel="Try again: start a new review" />
		{/if}
		{#if !data.list.ok && !lost}
			<ErrorBanner message="Reviews couldn't load" detail={data.list.reason} onRetry={reload} retryLabel="Reload reviews" />
		{/if}
		{#if lost}
			<ErrorBanner severity="warning" message={gaveUp ? "Still can't reach the server" : 'Lost contact with the server — retrying…'} detail={gaveUp ? 'Automatic retries stopped.' : `Attempt ${pollFailures} of ${MAX_POLL_FAILURES} · next try in ${pollDelay(pollFailures) / 1000} s`} onRetry={gaveUp ? reconnect : undefined} retryLabel="Reconnect" retrying={reconnecting} />
		{/if}
		{#if has('S11') && data.row && !data.row.ok}
			<ErrorBanner message="Couldn't load this review" detail={data.row.reason} onRetry={reload} retryLabel="Reload reviews" />
		{/if}
		{#if has('S7') && view?.newest && viewing}
			<ErrorBanner severity="warning" message={`Your latest review (#${view.newest.id}, ${when(view.newest.created_at)}) failed. Showing #${viewing.id} from ${when(viewing.created_at)}.`} onRetry={generate} retryLabel="Try again: start a new review">
				{#snippet actions()}<button type="button" class="btn" onclick={() => view_(view!.newest!.id)}>See what went wrong</button>{/snippet}
			</ErrorBanner>
		{/if}
		{#if decisionsLost}
			<ErrorBanner severity="warning" message="Your saved swap decisions couldn't load." detail={data.feedback && !data.feedback.ok ? data.feedback.reason : undefined} onRetry={reload} retryLabel="Reload reviews" />
		{/if}
		{#if partial && (partial.sections.length || partial.omitted)}
			<ErrorBanner severity="info" message="Parts of this review are unavailable">
				{#if partial.sections.length}
					<ul class="chips">
						{#each partial.sections as s (s.key)}<li><a href={`#${s.anchor}`}>{s.label}</a></li>{/each}
					</ul>
				{/if}
				{#if partial.omitted}<p><a href="#sec-debug">{partial.omitted} items omitted by the verifier · Debug appendix</a></p>{/if}
			</ErrorBanner>
		{/if}
		{#if pdfState === 'error' && !pdfDismissed}
			<ErrorBanner message="PDF export failed" detail={pdfMessage} onRetry={exportPdf} dismissible onDismiss={() => (pdfDismissed = true)} />
		{/if}
	</div>

	<div role="status" aria-live="polite" class="visually-hidden">{announcement}</div>
	{#if switching}<p class="switching" role="status">Loading review #{switching}…</p>{/if}

	{#if shownActive}
		<section class="review-status live-status" class:stale={lost} data-testid="live-status">
			<div role="status" aria-live="polite" aria-atomic="true" class="review-live">
				<span class="eyebrow">Review #{shownActive.id} · {shownActive.status}{lost ? (gaveUp ? ' · status unknown' : ` · last heard ${clock(new Date(now).toISOString())}`) : ''}</span>
				<strong data-testid="review-stage">{shownActive.stage || 'Starting'}</strong>
			</div>
			<span class="muted" data-now={now}><span aria-hidden="true" data-testid="review-elapsed">{elapsed(shownActive.started_at ?? shownActive.created_at)} elapsed · </span>Started {clock(shownActive.started_at ?? shownActive.created_at)}. A review takes a few minutes; you can leave this page and come back.</span>
		</section>
	{/if}

	{#if viewing?.status === 'failed'}
		<section class="review-status failed failed-panel" aria-labelledby="failed-h">
			<span class="eyebrow">Review #{viewing.id} failed · {when(viewing.finished_at ?? viewing.created_at)}</span>
			<h2 id="failed-h">{failure.headline}</h2>
			<p class="muted">{failure.body}</p>
			{#if viewing.error}<details><summary>Details</summary><pre>{viewing.error}</pre></details>{/if}
			<div class="actions">
				<button type="button" class="btn btn--primary" onclick={generate} aria-disabled={busy || !!active || undefined}>{busy ? 'Starting…' : 'Try again: start a new review'}</button>
				{#if lastGood}<button type="button" class="btn" onclick={() => view_(lastGood.id)}>View last good review (#{lastGood.id})</button>{/if}
			</div>
		</section>
	{/if}

	{#if data.document}
		<div class="report-wrap" aria-busy={switching ? 'true' : undefined}>
			<ReviewReport document={data.document} theme="dark">
				{#snippet swapActions(swap)}
					<SwapFeedback documentId={viewing!.id} deckId={data.deckId} {swap} saved={data.feedback?.ok ? data.feedback.value[swap.id] : undefined} unavailable={decisionsLost} />
				{/snippet}
			</ReviewReport>
		</div>
	{:else if data.list.ok && view?.states.includes('S1')}
		<section class="review-status">
			<EmptyState variant="inline" title="No review yet for this deck" description="Generate one: it reads the deck, your collection and your profile, and takes a few minutes." ctaLabel="Generate review" onCta={generate} />
		</section>
	{/if}
{/if}

<style>
	:global(body:has(.review-report)), :global(body:has(.review-bar)) { margin: 0; background: var(--color-bg); }
	:global(body:has(.print-mode)) { background: white; }
	@media print { :global(body:has(.review-report)) { background: white; } .review-bar, .review-status, .review-alerts, .failed-panel, .live-status, .switching { display: none; } }
	.review-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
	.review-status { display: flex; flex-direction: column; gap: 4px; max-width: 1280px; margin: 16px auto; padding: 16px 20px; border: 1px solid #29323b; border-left: 3px solid #f0a63a; color: #b6bfc8; background: #141a1f; }
	.review-live { display: flex; flex-direction: column; gap: 4px; }
	.review-status.failed { border-left-color: var(--color-danger-solid); }
	.review-status strong { color: #e8ebee; }
	.review-alerts { display: grid; gap: var(--space-2); max-width: var(--content-max); margin: var(--space-4) auto 0; }
	.review-alerts:empty { display: none; }
	.chips { display: flex; flex-wrap: wrap; gap: var(--space-2); list-style: none; margin: var(--space-2) 0 0; padding: 0; }
	.chips a { display: inline-flex; align-items: center; min-height: 2rem; padding: 0 var(--space-3); border: 1px solid var(--color-info); color: var(--color-text); font-size: var(--text-xs); }
	.chips a::before { content: '↓ '; color: var(--color-info); }
	.why { font-size: var(--text-xs); color: var(--color-muted); }
	button[aria-disabled='true'] { color: var(--color-muted); cursor: not-allowed; }
	.demoted { background: transparent; }
	.switching { max-width: var(--content-max); margin: var(--space-2) auto 0; font: var(--text-xs) var(--font-mono); color: var(--color-accent); }
	.report-wrap { position: relative; }
	.report-wrap[aria-busy='true']::before { content: ''; position: absolute; inset: 0 0 auto 0; height: 3px; z-index: 2; background: linear-gradient(90deg, var(--color-border) 0 30%, var(--color-info) 30% 60%, var(--color-border) 60%); background-size: 200% 100%; animation: busy 1100ms linear infinite; }
	@keyframes busy { from { background-position: 100% 0; } to { background-position: -100% 0; } }
	@media (prefers-reduced-motion: reduce) { .report-wrap[aria-busy='true']::before { animation: none; } }
	.live-status.stale { border-left-style: dashed; border-left-color: var(--color-warning); }
	.failed-panel { background: var(--color-danger-bg); border: 1px solid var(--color-danger-solid); border-left: 6px solid var(--color-danger); padding: var(--space-6); gap: var(--space-2); }
	.failed-panel h2 { font-size: var(--text-xl); margin: 0; }
	.failed-panel .eyebrow { color: var(--color-danger); }
	.failed-panel pre { max-height: 12rem; overflow: auto; font-size: var(--text-xs); background: var(--color-bg); padding: var(--space-2); white-space: pre-wrap; }
	.failed-panel .actions { display: flex; flex-wrap: wrap; gap: var(--space-2); }
</style>
