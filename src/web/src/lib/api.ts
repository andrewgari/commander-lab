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

export class ApiError extends Error {
	constructor(
		public status: number,
		message: string
	) {
		super(message);
	}
}
