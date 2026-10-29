import { API_URL } from '#lib/api.ts';
import type { RequestHandler } from './$types';

/** Same-origin card art for the review page and its PDF: proxies the API's local Scryfall image cache.
 * (The browser, and the PDF container's Chromium, only ever talk to this app, never to Scryfall.) */
export const GET: RequestHandler = async ({ url }) => {
	const name = url.searchParams.get('name') ?? '';
	const size = url.searchParams.get('size') ?? 'small';
	const upstream = await fetch(`${API_URL}/api/v2/card-images?name=${encodeURIComponent(name)}&size=${encodeURIComponent(size)}`).catch(
		() => null
	);
	if (!upstream) return new Response('card image service unreachable', { status: 502 });
	if (!upstream.ok) return new Response('image unavailable', { status: upstream.status === 404 ? 404 : 503 });
	return new Response(upstream.body, {
		status: 200,
		headers: {
			'content-type': upstream.headers.get('content-type') ?? 'image/jpeg',
			'cache-control': upstream.headers.get('cache-control') ?? 'public, max-age=86400'
		}
	});
};
