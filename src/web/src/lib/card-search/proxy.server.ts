// Server-only: shared forwarding for the two card-search proxy routes (docs/ui/card-search.md §5).
// The browser talks only to SvelteKit; SvelteKit talks only to FastAPI; only FastAPI talks to Scryfall.
// `apiUrl` is passed in (the routes hand over API_URL) so this file loads under plain `node --test`.

/** Query params `GET /api/v2/cards/search` accepts (§3, §5.1). Anything else is dropped. */
export const SEARCH_PARAMS = [
	'name',
	'ci',
	'ci_mode',
	'type',
	'type_text',
	'mv',
	'rarity',
	'oracle',
	'owned',
	'cmdr',
	'pair',
	'page',
	'page_size'
] as const;

/** Query params `GET /api/cards/autocomplete` accepts. */
export const AUTOCOMPLETE_PARAMS = ['q', 'limit', 'commanders_only'] as const;

export const UPSTREAM_TIMEOUT_MS = 15_000;

function json(status: number, body: unknown): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json', 'cache-control': 'no-store' }
	});
}

/** Copy only the allowed params, so the proxy is not an open relay to arbitrary upstream queries. */
export function pickParams(source: URLSearchParams, allowed: readonly string[]): URLSearchParams {
	const out = new URLSearchParams();
	for (const key of allowed) {
		const value = source.get(key);
		if (value !== null) out.set(key, value);
	}
	return out;
}

/**
 * GET `upstreamPath` on the API with the allowed params and relay the answer.
 *
 * Status and body pass through (a 400 such as "This commander can't have a partner" must reach the
 * page). An unreachable or slow API becomes 502 / 504 with a JSON `detail`: a failure is never
 * turned into an empty 200, so the UI cannot show it as "No matches".
 */
export async function forwardGet(
	apiUrl: string,
	upstreamPath: string,
	incoming: URL,
	allowed: readonly string[]
): Promise<Response> {
	const qs = pickParams(incoming.searchParams, allowed).toString();
	const target = `${apiUrl}${upstreamPath}${qs ? `?${qs}` : ''}`;

	let upstream: Response;
	try {
		upstream = await fetch(target, {
			headers: { accept: 'application/json' },
			signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS)
		});
	} catch (err) {
		const timedOut = err instanceof Error && (err.name === 'TimeoutError' || err.name === 'AbortError');
		return timedOut
			? json(504, { detail: 'card search timed out' })
			: json(502, { detail: 'card search service unreachable' });
	}

	return new Response(await upstream.text(), {
		status: upstream.status,
		headers: {
			'content-type': upstream.headers.get('content-type') ?? 'application/json',
			// Ownership counts change when the user adds a card; the API owns caching of Scryfall data.
			'cache-control': 'no-store'
		}
	});
}

/** `GET /api/card-search` -> FastAPI `/api/v2/cards/search` (docs/ui/card-search.md §5). */
export const searchProxy = (incoming: URL, apiUrl: string) =>
	forwardGet(apiUrl, '/api/v2/cards/search', incoming, SEARCH_PARAMS);

/** `GET /api/card-autocomplete` -> FastAPI `/api/cards/autocomplete` (quick-add, §4). */
export const autocompleteProxy = (incoming: URL, apiUrl: string) =>
	forwardGet(apiUrl, '/api/cards/autocomplete', incoming, AUTOCOMPLETE_PARAMS);
