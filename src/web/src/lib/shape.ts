// Runtime shape guards for API payloads. A partial or empty object from the API must degrade to a
// controlled error page (see routes/+error.svelte), never an unhandled TypeError in a template.
import { error } from '@sveltejs/kit';

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

/** Throws a 502 naming the missing field when `value` lacks any dotted `paths` of the expected kind. */
export function requireShape(value: unknown, what: string, spec: Record<string, 'object' | 'array' | 'string' | 'number'>): void {
	const bad = (field: string) => error(502, `${what} came back incomplete (missing "${field}")`);
	if (!isObj(value)) bad('response');
	for (const [path, kind] of Object.entries(spec)) {
		let cur: unknown = value;
		for (const part of path.split('.')) cur = isObj(cur) ? cur[part] : undefined;
		const ok =
			kind === 'array' ? Array.isArray(cur) : kind === 'object' ? isObj(cur) : kind === 'string' ? typeof cur === 'string' : typeof cur === 'number';
		if (!ok) bad(path);
	}
}
