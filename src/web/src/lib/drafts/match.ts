// Pure: pick the exact (case-insensitive) name match from owned-search results, for the no-JS
// add-by-name action (page-specs §2.4). Anything but an exact match is refused, never guessed.

export interface NamedHit {
	oracle_id: string;
	name: string;
}

function norm(s: string): string {
	return s.replace(/\s+/g, ' ').trim().toLowerCase();
}

export function pickExactMatch<T extends NamedHit>(results: readonly T[], name: string): T | null {
	const want = norm(name);
	if (!want) return null;
	// A double-faced card is found by either face name as well as the full "A // B" name.
	return (
		results.find((r) => norm(r.name) === want) ??
		results.find((r) => norm(r.name).split(' // ').some((face) => face === want)) ??
		null
	);
}
