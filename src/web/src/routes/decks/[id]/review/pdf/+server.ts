import { API_URL } from '#lib/api.ts';
import type { RequestHandler } from './$types';

/** Export PDF: asks the API to print the review through the pdf container and streams the file back. */
export const GET: RequestHandler = async ({ url }) => {
	const doc = Number(url.searchParams.get('doc'));
	if (!Number.isInteger(doc) || doc < 1) return new Response('doc is required', { status: 400 });
	const upstream = await fetch(`${API_URL}/api/v2/review-documents/${doc}/pdf`).catch(() => null);
	if (!upstream) return new Response('The API is unreachable.', { status: 502 });
	if (!upstream.ok) {
		let reason = upstream.statusText;
		try {
			const body = await upstream.json();
			reason = body?.detail?.reason ?? body?.detail ?? reason;
		} catch {
			/* keep statusText */
		}
		return new Response(String(reason), { status: upstream.status });
	}
	const headers = new Headers({ 'content-type': 'application/pdf' });
	for (const h of ['content-disposition', 'x-pdf-pages', 'x-missing-images']) {
		const v = upstream.headers.get(h);
		if (v) headers.set(h, v);
	}
	return new Response(upstream.body, { status: 200, headers });
};
