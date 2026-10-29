// Pure: DraftState (+ deck-name map) -> CommitSummary shown before Commit (page-specs §2.6).
// Mirrors `plan_commit` (domain/draft/draft_mode.py) for display only; the server stays the gate.
// No Svelte or DOM imports, so it runs under `node --test`.

import { BOARD_ORDER, boardLabel } from './boards.ts';

export interface SummaryCard {
	oracle_id: string;
	name: string;
	qty: number;
	board: string;
	color_identity: readonly string[];
}

export interface SummaryMove {
	instance_id: string;
	action: string;
	from_deck: string | null;
	oracle_id: string;
	card_name: string;
	status: string;
	deck_id: string | null;
}

export interface SummaryDraft {
	target_name: string;
	name?: string;
}

export interface SummaryInput {
	draft: SummaryDraft;
	cards: readonly SummaryCard[];
	moves: readonly SummaryMove[];
}

/** deck id -> deck name; `null` when the deck list could not be loaded. */
export type DeckNames = ReadonlyMap<string, string> | null;

export const FALLBACK_DECK_NAME = 'another deck';
export const COMMANDER_DECK_SIZE = 100;
const WUBRG = ['W', 'U', 'B', 'R', 'G'];

export interface DonorPull {
	deckId: string;
	deckName: string;
	/** Card names pulled out of that deck, sorted, with repeats listed once per copy. */
	cards: string[];
	count: number;
}

export interface CommitSummary {
	newDeckName: string;
	totalCards: number;
	/** Per board, in display order, empty boards omitted. */
	boardCounts: Array<{ board: string; label: string; qty: number }>;
	/** "100 cards: 1 commander · 99 main" */
	cardsLine: string;
	commanders: string[];
	/** Union of staged colour identities in WUBRG order; empty = colourless. */
	identity: string[];
	assignCount: number;
	unassignCount: number;
	/** "7 copies move into the new deck · 2 return to the collection"; null with no moves. */
	movesLine: string | null;
	donors: DonorPull[];
	donorTotal: number;
	/** `plan_commit`: any assign -> "have", otherwise "virtual". */
	expectedStatus: 'have' | 'virtual';
	expectedStatusLabel: string;
	warnings: string[];
	/** Set when Commit must be blocked (an empty draft); the server rejects it too. */
	blockedReason: string | null;
	irreversibleNote: string;
}

export const IRREVERSIBLE_NOTE =
	'This applies in one step. Staged rows are cleared; it cannot be undone from here.';
export const EMPTY_BLOCKED_REASON = 'Stage at least one card to commit.';

function plural(n: number, one: string, many: string = `${one}s`): string {
	return `${n} ${n === 1 ? one : many}`;
}

/** Display name for a deck id; never the id itself. */
export function deckName(id: string | null | undefined, names: DeckNames): string {
	if (!id) return FALLBACK_DECK_NAME;
	return names?.get(id) ?? FALLBACK_DECK_NAME;
}

export function summarize(input: SummaryInput, names: DeckNames = null): CommitSummary {
	const { draft, cards, moves } = input;

	const byBoard = new Map<string, number>();
	for (const c of cards) byBoard.set(c.board, (byBoard.get(c.board) ?? 0) + c.qty);
	const boardKeys = [
		...BOARD_ORDER.filter((b) => byBoard.has(b)),
		...[...byBoard.keys()].filter((b) => !(BOARD_ORDER as readonly string[]).includes(b))
	];
	const boardCounts = boardKeys.map((board) => ({ board, label: boardLabel(board), qty: byBoard.get(board) ?? 0 }));
	const totalCards = boardCounts.reduce((n, b) => n + b.qty, 0);
	const cardsLine =
		totalCards === 0
			? '0 cards'
			: `${plural(totalCards, 'card')}: ${boardCounts
					.map((b) => `${b.qty} ${b.board}`)
					.join(' · ')}`;

	const commanders = cards.filter((c) => c.board === 'commander').map((c) => c.name);

	const ci = new Set<string>();
	for (const c of cards) for (const col of c.color_identity ?? []) ci.add(col);
	const identity = [...WUBRG.filter((c) => ci.has(c)), ...[...ci].filter((c) => !WUBRG.includes(c)).sort()];

	const assigns = moves.filter((m) => m.action === 'assign');
	const unassigns = moves.filter((m) => m.action === 'unassign');
	const movesLine =
		moves.length === 0
			? null
			: [
					assigns.length ? `${plural(assigns.length, 'copy', 'copies')} ${assigns.length === 1 ? 'moves' : 'move'} into the new deck` : '',
					unassigns.length ? `${unassigns.length} ${unassigns.length === 1 ? 'returns' : 'return'} to the collection` : ''
				]
					.filter(Boolean)
					.join(' · ');

	// Donor pulls: an assign of a copy that is currently inside another deck takes it out of there.
	const donorMap = new Map<string, string[]>();
	for (const m of assigns) {
		if (m.status !== 'in_deck') continue;
		const id = m.deck_id ?? m.from_deck ?? '';
		const list = donorMap.get(id);
		if (list) list.push(m.card_name);
		else donorMap.set(id, [m.card_name]);
	}
	const donors: DonorPull[] = [...donorMap.entries()]
		.map(([deckId, list]) => ({
			deckId,
			deckName: deckName(deckId, names),
			cards: [...list].sort((a, b) => a.localeCompare(b)),
			count: list.length
		}))
		.sort((a, b) => b.count - a.count || a.deckName.localeCompare(b.deckName));
	const donorTotal = donors.reduce((n, d) => n + d.count, 0);

	const deckCards = cards
		.filter((c) => c.board === 'commander' || c.board === 'main')
		.reduce((n, c) => n + c.qty, 0);

	const warnings: string[] = [];
	if (cards.length > 0 && commanders.length === 0) warnings.push('No commander staged.');
	if (cards.length > 0 && deckCards !== COMMANDER_DECK_SIZE) {
		warnings.push(`Commander decks have ${COMMANDER_DECK_SIZE} cards; this one will have ${deckCards}.`);
	}
	for (const d of donors) {
		warnings.push(`${d.deckName} will lose ${d.count === 1 ? 'this copy' : 'these copies'}: ${d.cards.join(', ')}.`);
	}

	const expectedStatus = assigns.length > 0 ? 'have' : 'virtual';

	return {
		newDeckName: draft.target_name || draft.name || 'New deck',
		totalCards,
		boardCounts,
		cardsLine,
		commanders,
		identity,
		assignCount: assigns.length,
		unassignCount: unassigns.length,
		movesLine,
		donors,
		donorTotal,
		expectedStatus,
		expectedStatusLabel: expectedStatus === 'have' ? 'Built (have)' : 'Virtual',
		warnings,
		blockedReason: cards.length === 0 ? EMPTY_BLOCKED_REASON : null,
		irreversibleNote: IRREVERSIBLE_NOTE
	};
}

/** "Pulls 3 copies out of Sliver Swarm, 1 out of Elves"; null when nothing is pulled. */
export function donorSentence(donors: readonly DonorPull[]): string | null {
	if (donors.length === 0) return null;
	const parts = donors.map((d, i) =>
		i === 0 ? `${plural(d.count, 'copy', 'copies')} out of ${d.deckName}` : `${d.count} out of ${d.deckName}`
	);
	return `Pulls ${parts.join(', ')}`;
}

// ---- Abandon ----

export function abandonSentence(cards: readonly { qty: number }[], moves: readonly unknown[]): string {
	const staged = cards.reduce((n, c) => n + c.qty, 0);
	return `Deletes ${plural(staged, 'staged card')} and ${plural(moves.length, 'staged move')}. Your decks and collection are untouched.`;
}

// ---- Moves list (no ids, page-specs §2.5) ----

export interface MoveRow {
	instanceId: string;
	cardName: string;
	/** "Into new deck" | "Back to collection" */
	moveLabel: string;
	from: string;
	/** "In collection" | "In {deck}" | "In the mail" */
	state: string;
	/** "Sol Ring (copy 2)" when several rows share card and source, else the card name. */
	displayName: string;
}

const STATE_LABELS: Record<string, string> = {
	in_collection: 'In collection',
	in_mail: 'In the mail',
	pending: 'In the mail'
};

export function moveRows(moves: readonly SummaryMove[], names: DeckNames): MoveRow[] {
	const groupTotals = new Map<string, number>();
	const key = (m: SummaryMove) => `${m.oracle_id}|${m.from_deck ?? ''}|${m.action}`;
	for (const m of moves) groupTotals.set(key(m), (groupTotals.get(key(m)) ?? 0) + 1);
	const seen = new Map<string, number>();
	return moves.map((m) => {
		const k = key(m);
		const n = (seen.get(k) ?? 0) + 1;
		seen.set(k, n);
		const state =
			m.status === 'in_deck' ? `In ${deckName(m.deck_id ?? m.from_deck, names)}` : (STATE_LABELS[m.status] ?? 'In collection');
		return {
			instanceId: m.instance_id,
			cardName: m.card_name,
			moveLabel: m.action === 'assign' ? 'Into new deck' : 'Back to collection',
			from: m.from_deck ? deckName(m.from_deck, names) : 'Collection',
			state,
			displayName: (groupTotals.get(k) ?? 1) > 1 ? `${m.card_name} (copy ${n})` : m.card_name
		};
	});
}
