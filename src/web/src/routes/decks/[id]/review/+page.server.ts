import { error } from '@sveltejs/kit';
import {
	apiGet,
	ApiError,
	type ReviewDocumentList,
	type ReviewDocumentResponse,
	type SwapFeedback
} from '#lib/api.ts';
import type { ReviewDocument } from '#lib/components/review/types.js';
import type { Part } from '#lib/review/reviewState.ts';
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

	// Only a missing deck stops the page. Every other failure is a per-part result the page renders (spec §5.3).
	const part = async <T>(path: string): Promise<Part<T>> => {
		try {
			return { ok: true, value: await apiGet<T>(path) };
		} catch (e) {
			if (e instanceof ApiError) return { ok: false, reason: e.message, status: e.status };
			return { ok: false, reason: e instanceof Error ? e.message : 'unavailable' };
		}
	};
	const list = await part<ReviewDocumentList>(`/api/v2/decks/${encodeURIComponent(params.id)}/review-documents`);
	if (!list.ok && list.status === 404) error(404, 'Deck not found');
	const documents = list.ok ? list.value.documents : [];
	const viewId = doc ?? documents.find((d) => d.status === 'ok')?.id ?? documents[0]?.id ?? null;
	let row: Part<ReviewDocumentResponse> | null = null;
	let feedback: Part<Record<string, SwapFeedback>> | null = null;
	if (viewId) {
		row = await part<ReviewDocumentResponse>(`/api/v2/review-documents/${viewId}`);
		if (row.ok && row.value.status === 'ok') {
			const f = await part<{ feedback: Record<string, SwapFeedback> }>(`/api/v2/review-documents/${viewId}/feedback`);
			feedback = f.ok ? { ok: true, value: f.value.feedback } : f;
		}
	}
	return {
		print: false as const,
		deckId: params.id,
		list,
		row,
		document: (row?.ok && row.value.status === 'ok' ? row.value.doc : null) as unknown as ReviewDocument | null,
		feedback
	};
};
