// Server-only module (imported from +page.server.ts files only).
/** Server-side base URL of the FastAPI app (compose: http://app:8000). */
export const API_URL = process.env.COMMANDER_API_URL ?? 'http://localhost:8000';

export interface HaveBuyTotals {
	need: number;
	have: number;
	pending: number;
	buy: number;
	complete: boolean;
	have_pct: number | null;
}

export interface DeckSummary {
	id: string;
	name: string;
	status: string;
	source: string;
	archidekt_id: string | null;
	color_identity: string[];
	commanders: string[];
	card_qty: number;
	have_buy: HaveBuyTotals | null;
}

export interface DeckCardLine {
	oracle_id: string;
	name: string;
	qty: number;
	board: string;
	category: string | null;
	type_line: string;
	cmc: number;
	mana_cost: string;
	colors: string[];
	image_uri: string;
	have: number;
	pending: number;
	buy: number;
	elsewhere: number;
}

export interface ProfileSnapshot {
	id: number;
	created_at: string | null;
	trigger: string;
	inputs: Record<string, unknown>;
	metrics: Record<string, unknown>;
	summary_text: string;
}

export interface ProfileLatestResponse {
	available: boolean;
	reason: string | null;
	snapshot: ProfileSnapshot | null;
}

export interface ProfileHistoryResponse {
	snapshots: ProfileSnapshot[];
}

export interface ProfileOverride {
	key: string;
	value: unknown;
	set_at: string | null;
}

export interface ProfileOverridesResponse {
	overrides: Record<string, ProfileOverride>;
	known_keys: Record<string, string>;
}

export interface OverridePutResponse {
	override: ProfileOverride;
	snapshot_id: number;
}

export interface InventoryRow {
	oracle_id: string;
	name: string;
	type_line: string;
	image_uri: string;
	owned: number;
	in_collection: number;
	in_deck: number;
	in_mail: number;
	not_owned: number;
	decks: string[];
}

export interface InventoryTotals {
	cards: number;
	owned: number;
	in_collection: number;
	in_deck: number;
	in_mail: number;
}

export interface InventoryResponse {
	inventory: InventoryRow[];
	totals: InventoryTotals;
}

export interface DeckDetail {
	deck: {
		id: string;
		name: string;
		archidekt_id: string | null;
		status: string;
		color_identity: string[];
		commanders: string[];
		source: string;
		fingerprint: string;
		created_at: string | null;
		updated_at: string | null;
		archived_at: string | null;
	};
	boards: Record<string, DeckCardLine[]>;
	have_buy: {
		deck_id: string;
		totals: HaveBuyTotals;
		lines: Array<{
			oracle_id: string;
			name: string;
			board: string;
			category: string | null;
			need: number;
			have: number;
			pending: number;
			buy: number;
			elsewhere: number;
		}>;
	};
	snapshot: ProfileSnapshot | null;
}

export interface GameRecord {
	id: number;
	import_id: number;
	deck_id: string;
	deck_name: string | null;
	played_at: string | null;
	seat_count: number;
	result: string | null;
	turns: number | null;
	imported_at: string | null;
}

export interface PlayerCurve {
	seat: number;
	deck: string;
	is_user_seat: boolean;
	points: Array<{
		turn: number;
		land_played: string | null;
		mana_spent: number | null;
		spells_cast: number;
	}>;
	unavailable: Record<string, string>;
}

export interface CardCast {
	seat: number;
	deck: string;
	card: string;
	oracle_id: string | null;
	oracle_resolved: boolean;
	cast_turn: number | null;
	drawn: number | null;
	stuck: boolean | null;
	unavailable: Record<string, string>;
}

export interface GameReport {
	game: GameRecord & { notes: string; parser_version: string; raw_log_uri: string };
	meta: {
		user_seat?: number;
		game_number?: number;
		duration_ms?: number | null;
		unavailable?: Record<string, string | null>;
	};
	players: PlayerCurve[];
	cards: CardCast[];
}

export interface ForgeLogImportResult {
	duplicate: boolean;
	import_id: number;
	status: string;
	error: string | null;
	deck_id: string;
	deck_name: string;
	games: Array<{ game_id: number; result: string | null; turns: number | null; seat_count: number }>;
	snapshot_id: number | null;
}

export async function apiGet<T>(path: string): Promise<T> {
	const res = await fetch(`${API_URL}${path}`);
	if (!res.ok) {
		let reason = res.statusText;
		try {
			const body = await res.json();
			reason = body?.detail?.reason ?? body?.detail ?? reason;
		} catch {
			/* keep statusText */
		}
		throw new ApiError(res.status, String(reason));
	}
	return (await res.json()) as T;
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
	const res = await fetch(`${API_URL}${path}`, {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify(body)
	});
	if (!res.ok) {
		let reason = res.statusText;
		try {
			const payload = await res.json();
			reason = payload?.detail?.reason ?? payload?.detail ?? reason;
		} catch {
			/* keep statusText */
		}
		throw new ApiError(res.status, String(reason));
	}
	return (await res.json()) as T;
}

export class ApiError extends Error {
	constructor(
		public status: number,
		message: string
	) {
		super(message);
	}
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
	const res = await fetch(`${API_URL}${path}`, {
		method: 'PUT',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify(body)
	});
	if (!res.ok) {
		let reason = res.statusText;
		try {
			const data = await res.json();
			reason = data?.detail?.reason ?? data?.detail ?? reason;
		} catch {
			/* keep statusText */
		}
		throw new ApiError(res.status, String(reason));
	}
	return (await res.json()) as T;
}
