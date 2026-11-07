import { json } from '@sveltejs/kit';
import { apiPost, ApiError } from '#lib/api.ts';
import type { RequestHandler } from './$types';

/** Queue a new review (API answers 202 at once). A JSON fetch, not a form action, so it does not depend on the form CSRF origin check. */
export const POST: RequestHandler = async ({ params }) => {
	try {
		const started = await apiPost<{ id: number; already_running: boolean }>(
			`/api/v2/decks/${encodeURIComponent(params.id)}/review-documents`,
			{}
		);
		return json(started, { status: 202 });
	} catch (e) {
		return json({ message: e instanceof Error ? e.message : 'Could not start the review' }, { status: e instanceof ApiError ? e.status : 502 });
	}
};
