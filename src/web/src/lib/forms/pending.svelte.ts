import type { SubmitFunction } from '$app/forms';

export const NETWORK_ERROR_MESSAGE = 'Network or server error — your input was kept. Try again.';

export interface PendingOptions {
	/** Return a message to ask `window.confirm`, or null for no prompt. */
	confirm?: (ctx: { formData: FormData; action: string }) => string | null;
	/** Which row/button is pending (e.g. the row's oracle id). */
	key?: (formData: FormData) => string;
}

/** '?/commit' -> 'commit'; a form with no named action -> 'default'. */
function actionName(url: URL): string {
	const name = url.search.slice(1).split('&')[0].replace(/^\//, '');
	return name || 'default';
}

/**
 * Pending state and lifecycle for SvelteKit POST forms (docs/ui/shared-components README D16).
 * One instance may enhance several <form>s; the lock is shared across all of them.
 * The helper never builds UI: pages render `transientError` and SubmitButton shows the pending label.
 */
export function createPending(opts: PendingOptions = {}) {
	let submitting = $state(false);
	let action = $state<string | null>(null);
	let key = $state<string | null>(null);
	let transientError = $state<string | null>(null);

	const enhance: SubmitFunction = ({ action: url, formData, cancel }) => {
		const name = actionName(url);

		// The confirm runs before the lock, so dismissing the dialog never locks the form.
		const message = opts.confirm?.({ formData, action: name }) ?? null;
		if (message !== null && !window.confirm(message)) {
			cancel();
			return;
		}
		if (submitting) {
			cancel();
			return;
		}

		submitting = true;
		action = name;
		key = opts.key ? opts.key(formData) : null;
		transientError = null;

		return async ({ update, result }) => {
			try {
				if (result.type === 'error' || (result.type === 'failure' && result.status >= 500)) {
					// update() would render +error.svelte and drop the user's input.
					transientError = NETWORK_ERROR_MESSAGE;
				} else if (result.type === 'failure') {
					await update({ reset: false });
				} else {
					await update({ reset: true });
				}
			} catch {
				transientError = NETWORK_ERROR_MESSAGE;
			} finally {
				submitting = false;
				action = null;
				key = null;
			}
		};
	};

	return {
		get submitting() {
			return submitting;
		},
		get action() {
			return action;
		},
		get key() {
			return key;
		},
		get transientError() {
			return transientError;
		},
		enhance,
		clearError() {
			transientError = null;
		}
	};
}
