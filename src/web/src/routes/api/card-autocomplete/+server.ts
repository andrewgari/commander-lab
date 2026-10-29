import { API_URL } from '#lib/api.ts';
import { autocompleteProxy } from '#lib/card-search/proxy.server.ts';
import type { RequestHandler } from './$types';

/** Quick-add name suggestions (docs/ui/card-search.md §4): proxies FastAPI's /api/cards/autocomplete. */
export const GET: RequestHandler = ({ url }) => autocompleteProxy(url, API_URL);
