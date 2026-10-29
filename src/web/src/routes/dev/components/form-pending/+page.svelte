<script lang="ts">
	// Fixture for the form-pending pattern (createPending + SubmitButton), exercised by
	// src/web/tests/form_pending.py and src/web/tests/components_a11y.py.
	//
	// The page-level contract this fixture demonstrates (README D15-D18):
	//   <form method="POST" action="?/x" use:enhance={p.enhance} aria-busy={p.submitting}>
	//     <fieldset disabled={p.submitting}> ...fields... </fieldset>
	//     <SubmitButton pending={p.submitting} pendingLabel="Saving…">Save</SubmitButton>   <- OUTSIDE the fieldset
	//     {#if p.transientError}<p role="alert">…</p>{/if}
	//   </form>
	// Errors are plain <p role="alert"> here. Real pages use ErrorBanner (wired up in C6).
	import { enhance } from '$app/forms';
	import { createPending } from '#lib/forms/pending.svelte.ts';
	import SubmitButton from '#lib/components/SubmitButton.svelte';
	import type { PageProps } from './$types';

	let { data, form }: PageProps = $props();

	const slow = createPending();
	const ok = createPending();
	const invalid = createPending();
	const net = createPending(); // boom + throws share one instance
	const pair = createPending({ key: (fd) => String(fd.get('row')) }); // A and B share one lock
	const guarded = createPending({ confirm: ({ formData }) => `Save “${String(formData.get('title'))}”?` });
	const redirecting = createPending();

	// `form` is page-level ActionData; each form shows only the result addressed to it.
	const mine = (source: string) => (form && 'source' in form && form.source === source ? form : null);
	const message = (source: string) => {
		const f = mine(source);
		return f && 'message' in f ? String(f.message) : null;
	};
	const actionError = (source: string) => {
		const f = mine(source);
		return f && 'actionError' in f ? String(f.actionError) : null;
	};
</script>

<svelte:head><title>Form pending — Component fixtures — Commander Lab</title></svelte:head>

<div class="fixture">
	<h1>Form pending</h1>
	<p class="meta">
		createPending() owns the lock and lifecycle; SubmitButton owns the look. Forms keep method="POST" and action="?/x", so they work
		without JavaScript.
	</p>

	{#if data.done}<p role="status" data-testid="done">Redirect landed (?done=1).</p>{/if}

	<section aria-labelledby="h-slow">
		<h2 id="h-slow">Slow action (1500ms)</h2>
		<form method="POST" action="?/slow" use:enhance={slow.enhance} aria-busy={slow.submitting} data-testid="form-slow">
			<fieldset disabled={slow.submitting}>
				<legend>Details</legend>
				<label>Title <input name="title" value="Alpha" autocomplete="off" /></label>
			</fieldset>
			<SubmitButton pending={slow.submitting} pendingLabel="Saving…" variant="primary" data-testid="btn-slow">Save</SubmitButton>
			{#if message('slow')}<p role="status" data-testid="msg-slow">{message('slow')}</p>{/if}
		</form>
	</section>

	<section aria-labelledby="h-ok">
		<h2 id="h-ok">Immediate success (works without JavaScript)</h2>
		<form method="POST" action="?/ok" use:enhance={ok.enhance} aria-busy={ok.submitting} data-testid="form-ok">
			<input type="hidden" name="source" value="ok" />
			<label>Title <input name="title" value="Beta" autocomplete="off" /></label>
			<SubmitButton pending={ok.submitting} pendingLabel="Saving…" variant="success" data-testid="btn-ok">Save</SubmitButton>
			{#if message('ok')}<p role="status" data-testid="msg-ok">{message('ok')}</p>{/if}
		</form>
	</section>

	<section aria-labelledby="h-invalid">
		<h2 id="h-invalid">Validation failure (400): input is kept, no retry</h2>
		<form method="POST" action="?/invalid" use:enhance={invalid.enhance} aria-busy={invalid.submitting} data-testid="form-invalid">
			<input type="hidden" name="source" value="invalid" />
			<label>Title <input name="title" value="Gamma" autocomplete="off" /></label>
			<SubmitButton pending={invalid.submitting} pendingLabel="Saving…" data-testid="btn-invalid">Save</SubmitButton>
			{#if actionError('invalid')}<p role="alert" data-testid="err-invalid">{actionError('invalid')}</p>{/if}
		</form>
	</section>

	<section aria-labelledby="h-net">
		<h2 id="h-net">Server or network failure: input is kept, shown in-form, not the error page</h2>
		<p class="meta">Both forms share one createPending() instance.</p>
		<form method="POST" action="?/boom" use:enhance={net.enhance} aria-busy={net.submitting} data-testid="form-boom">
			<input type="hidden" name="source" value="boom" />
			<label>Title <input name="title" value="Delta" autocomplete="off" /></label>
			<SubmitButton pending={net.submitting} busy={net.action === 'boom'} pendingLabel="Saving…" data-testid="btn-boom">Boom (503)</SubmitButton>
		</form>
		<form method="POST" action="?/throws" use:enhance={net.enhance} aria-busy={net.submitting} data-testid="form-throws">
			<input type="hidden" name="source" value="throws" />
			<label>Title <input name="title" value="Epsilon" autocomplete="off" /></label>
			<SubmitButton pending={net.submitting} busy={net.action === 'throws'} pendingLabel="Saving…" data-testid="btn-throws">Throws</SubmitButton>
		</form>
		{#if net.transientError}<p role="alert" data-testid="err-transient">{net.transientError}</p>{/if}
	</section>

	<section aria-labelledby="h-pair">
		<h2 id="h-pair">Two forms, one lock</h2>
		<p class="meta">Pressing A locks B; only A shows its pending label (keyed by the hidden row field).</p>
		<div class="row">
			<form method="POST" action="?/slow" use:enhance={pair.enhance} aria-busy={pair.submitting} data-testid="form-a">
				<input type="hidden" name="source" value="pair-a" />
				<input type="hidden" name="row" value="a" />
				<input type="hidden" name="title" value="A" />
				<SubmitButton pending={pair.submitting} busy={pair.key === 'a'} pendingLabel="Starting A…" data-testid="btn-a">Start A</SubmitButton>
			</form>
			<form method="POST" action="?/slow" use:enhance={pair.enhance} aria-busy={pair.submitting} data-testid="form-b">
				<input type="hidden" name="source" value="pair-b" />
				<input type="hidden" name="row" value="b" />
				<input type="hidden" name="title" value="B" />
				<SubmitButton pending={pair.submitting} busy={pair.key === 'b'} pendingLabel="Starting B…" variant="danger" data-testid="btn-b">Start B</SubmitButton>
			</form>
		</div>
	</section>

	<section aria-labelledby="h-confirm">
		<h2 id="h-confirm">Confirm before submitting</h2>
		<form method="POST" action="?/ok" use:enhance={guarded.enhance} aria-busy={guarded.submitting} data-testid="form-confirm">
			<input type="hidden" name="source" value="confirm" />
			<label>Title <input name="title" value="Zeta" autocomplete="off" /></label>
			<SubmitButton pending={guarded.submitting} pendingLabel="Saving…" variant="link-danger" data-testid="btn-confirm">Save…</SubmitButton>
			{#if message('confirm')}<p role="status" data-testid="msg-confirm">{message('confirm')}</p>{/if}
		</form>
	</section>

	<section aria-labelledby="h-redirect">
		<h2 id="h-redirect">Redirect on success</h2>
		<form method="POST" action="?/redirect" use:enhance={redirecting.enhance} aria-busy={redirecting.submitting} data-testid="form-redirect">
			<SubmitButton pending={redirecting.submitting} pendingLabel="Redirecting…" data-testid="btn-redirect">Redirect</SubmitButton>
		</form>
	</section>
</div>

<style>
	.fixture { padding: var(--space-4); max-width: var(--content-max); margin: 0 auto; }
	section { margin-bottom: var(--space-6); }
	form { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-3); margin-bottom: var(--space-3); }
	fieldset { border: 1px solid var(--color-border); border-radius: var(--radius-sm); margin: 0; }
	label { display: inline-flex; align-items: center; gap: var(--space-2); }
	.row { display: flex; flex-wrap: wrap; gap: var(--space-4); }
	[role='alert'] { color: var(--color-danger); margin: 0; }
	[role='status'] { color: var(--color-success); margin: 0; }
</style>
