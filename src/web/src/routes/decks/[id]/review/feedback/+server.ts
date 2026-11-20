import { json } from '@sveltejs/kit';
import { apiPost, ApiError, type SwapFeedback } from '#lib/api.ts';
import type { RequestHandler } from './$types';

/** Accept / reject / maybe (+ reason) on one swap -> card 04's feedback store, via the review-document API. */
export const POST: RequestHandler = async ({ request }) => {
	const { document_id, swap_id, decision, reason } = await request.json();
	try {
		const out = await apiPost<{ feedback: Record<string, SwapFeedback> }>(`/api/v2/review-documents/${Number(document_id)}/feedback`, {
			swap_id,
			decision,
			reason: reason ?? ''
		});
		return json(out);
	} catch (e) {
		const status = e instanceof ApiError ? e.status : 502;
		return json({ message: e instanceof Error ? e.message : 'Could not save feedback' }, { status });
	}
};
