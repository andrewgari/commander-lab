import { API_URL } from '#lib/api.ts';
import { searchProxy } from '#lib/card-search/proxy.server.ts';
import type { RequestHandler } from './$types';

/** Card search with filters (docs/ui/card-search.md §5): proxies FastAPI's /api/v2/cards/search. */
export const GET: RequestHandler = ({ url }) => searchProxy(url, API_URL);
