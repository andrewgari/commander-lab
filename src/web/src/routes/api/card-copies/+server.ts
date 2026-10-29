import { API_URL } from '#lib/api.ts';
import { copiesProxy } from '#lib/card-search/proxy.server.ts';
import type { RequestHandler } from './$types';

/** Physical-copy search for the draft move picker: proxies FastAPI's /api/instances. */
export const GET: RequestHandler = ({ url }) => copiesProxy(url, API_URL);
