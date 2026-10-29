import { error } from '@sveltejs/kit';
import {
	apiGet,
	ApiError,
	type ReviewDocumentList,
	type ReviewDocumentResponse,
	type SwapFeedback
} from '#lib/api.ts';
import type { ReviewDocument } from '#lib/components/review/types.js';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, url }) => {
	const requested = Number(url.searchParams.get('doc'));
	const doc = Number.isInteger(requested) && requested > 0 ? requested : null;

	// Print mode is what the PDF container loads: no chrome, document fetched with the short-lived signed token.
	if (url.searchParams.get('print') === '1') {
		const token = url.searchParams.get('token');
		if (!doc || !token) error(403, 'The print view needs a signed link; use Export PDF on the review page.');
		try {
			const row = await apiGet<ReviewDocumentResponse>(
				`/api/v2/review-documents/${doc}/print-view?token=${encodeURIComponent(token)}`
			);
			if (row.status !== 'ok' || !row.doc) error(409, 'This review is not finished yet.');
			return { print: true as const, deckId: params.id, document: row.doc as unknown as ReviewDocument };
		} catch (e) {
			if (e instanceof ApiError) error(e.status === 403 ? 403 : 503, e.message);
			throw e;
		}
	}

	let list: ReviewDocumentList;
	try {
		list = await apiGet<ReviewDocumentList>(`/api/v2/decks/${encodeURIComponent(params.id)}/review-documents`);
	} catch (e) {
		if (e instanceof ApiError && e.status === 404) error(404, 'Deck not found');
		error(503, e instanceof Error ? e.message : 'Reviews unavailable');
	}
	const viewId = doc ?? list.documents.find((d) => d.status === 'ok')?.id ?? list.documents[0]?.id ?? null;
	let row: ReviewDocumentResponse | null = null;
	let feedback: Record<string, SwapFeedback> = {};
	if (viewId) {
		try {
			row = await apiGet<ReviewDocumentResponse>(`/api/v2/review-documents/${viewId}`);
			if (row.status === 'ok') {
				feedback = (await apiGet<{ feedback: Record<string, SwapFeedback> }>(`/api/v2/review-documents/${viewId}/feedback`)).feedback;
			}
		} catch (e) {
			if (!(e instanceof ApiError && e.status === 404)) error(503, e instanceof Error ? e.message : 'Review unavailable');
		}
	}
	return {
		print: false as const,
		deckId: params.id,
		list,
		row,
		document: (row?.status === 'ok' ? row.doc : null) as unknown as ReviewDocument | null,
		feedback
	};
};
