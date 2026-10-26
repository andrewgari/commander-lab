// Server-side API client for the FastAPI backend (docs/ARCHITECTURE.md §3).
// The web container proxies to the API; the base URL comes from the
// environment so dev (vite) and prod (adapter-node) share the code path.
import { env } from '$env/dynamic/private';

const base = () => (env.API_URL || 'http://localhost:8000').replace(/\/$/, '');

export async function apiGet<T>(path: string, fetchFn: typeof fetch): Promise<T> {
	const res = await fetchFn(`${base()}${path}`);
	if (!res.ok) {
		throw new Error(`API ${path} -> ${res.status}`);
	}
	return (await res.json()) as T;
}
