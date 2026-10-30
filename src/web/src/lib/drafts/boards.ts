// Pure board helpers for the draft staging page (docs: UI/UX audit page-specs §2.2, §2.4).
// The API's board set is `commander | main | sideboard | maybe | considering`
// (domain/draft/draft_mode.py BOARDS). The shared card-search `Board` type says `maybeboard`,
// so the host maps it on the way in.

export type DraftBoard = 'commander' | 'main' | 'sideboard' | 'maybe' | 'considering';

/** Display and grouping order (designer §2.10). */
export const BOARD_ORDER: readonly DraftBoard[] = ['commander', 'main', 'sideboard', 'maybe', 'considering'];

export const BOARD_LABELS: Record<DraftBoard, string> = {
	commander: 'Commander',
	main: 'Main',
	sideboard: 'Sideboard',
	maybe: 'Maybeboard',
	considering: 'Considering'
};

/** Accepts the API value or the card-search value (`maybeboard`); null for anything else. */
export function toApiBoard(board: string): DraftBoard | null {
	const b = board.trim().toLowerCase();
	if (b === 'maybeboard') return 'maybe';
	return (BOARD_ORDER as readonly string[]).includes(b) ? (b as DraftBoard) : null;
}

/** Human label for any board string; unknown boards are shown as given, capitalised. */
export function boardLabel(board: string): string {
	const api = toApiBoard(board);
	if (api) return BOARD_LABELS[api];
	return board ? board.charAt(0).toUpperCase() + board.slice(1) : 'Unknown';
}

export interface StagedLike {
	oracle_id: string;
	board: string;
	qty: number;
}

/** Staged qty of one card on one board (0 when absent). */
export function stagedQty(cards: readonly StagedLike[], oracleId: string, board: string): number {
	const target = toApiBoard(board) ?? board;
	return cards.find((c) => c.oracle_id === oracleId && c.board === target)?.qty ?? 0;
}

/** Total staged qty per oracle id across boards (drives the "In draft ×N" badge). */
export function stagedQtyByOracle(cards: readonly StagedLike[]): Map<string, number> {
	const out = new Map<string, number>();
	for (const c of cards) out.set(c.oracle_id, (out.get(c.oracle_id) ?? 0) + c.qty);
	return out;
}

/** Upsert SETS the quantity, so adding means sending existing + requested. */
export function nextQty(existing: number, requested: number): number {
	return Math.max(0, Math.trunc(existing)) + Math.max(1, Math.trunc(requested));
}

export interface BoardGroup<T> {
	board: string;
	label: string;
	rows: T[];
	qty: number;
}

/** Group staged rows by board in display order; empty boards are omitted; unknown boards go last. */
export function groupByBoard<T extends { board: string; qty: number }>(cards: readonly T[]): BoardGroup<T>[] {
	const known = new Map<string, T[]>();
	const extra = new Map<string, T[]>();
	for (const c of cards) {
		const bucket = (BOARD_ORDER as readonly string[]).includes(c.board) ? known : extra;
		const list = bucket.get(c.board);
		if (list) list.push(c);
		else bucket.set(c.board, [c]);
	}
	const groups: BoardGroup<T>[] = [];
	for (const board of [...BOARD_ORDER, ...extra.keys()]) {
		const rows = known.get(board) ?? extra.get(board);
		if (rows?.length) groups.push({ board, label: boardLabel(board), rows, qty: rows.reduce((n, r) => n + r.qty, 0) });
	}
	return groups;
}
