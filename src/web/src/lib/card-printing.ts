// URL for the card dossier pinned to one printing. Kept pure so it can be unit-tested
// (node --test tests/card_printing.test.ts) and shared by the client-side switcher.
export function printingHref(oracleId: string, scryfallId: string): string {
	return `/card/${encodeURIComponent(oracleId)}?printing=${encodeURIComponent(scryfallId)}`;
}
