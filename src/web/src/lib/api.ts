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

export interface DraftRecord {
	id: string;
	kind: string;
	status: string;
	name: string;
	source_decks: Array<{ id: string; name: string }>;
	target_name: string;
	target_commanders: unknown[];
	result_deck_id: string | null;
	notes: string;
	created_at: string | null;
	updated_at: string | null;
	closed_at: string | null;
}

export interface DraftCardLine {
	oracle_id: string;
	name: string;
	qty: number;
	board: string;
	category: string | null;
	type_line: string;
	cmc: number;
	color_identity: string[];
	image_uri: string;
}

export interface DraftMoveLine {
	instance_id: string;
	action: string;
	from_deck: string | null;
	oracle_id: string;
	card_name: string;
	status: string;
	deck_id: string | null;
}

export interface DraftState {
	draft: DraftRecord;
	cards: DraftCardLine[];
	moves: DraftMoveLine[];
}

export interface DraftSuggestions {
	adds: Array<{
		oracle_id: string;
		name: string;
		tier: string;
		qty_available: number;
		score: number | null;
		score_unavailable: boolean;
		donor: { deck_id: string; replacement_oracle_id: string; replacement_name: string } | null;
		reasons: string[];
	}>;
	hidden_donor_pulls: number;
	hidden_reason: string | null;
	cuts: Array<{
		oracle_id: string;
		name: string;
		board: string | null;
		weak_score: number;
		parts: Record<string, number>;
		reasons: string[];
	}>;
	inputs_unavailable: Record<string, string>;
	note: string;
}

export interface ProvenancePath {
	instance_id: string;
	oracle_id: string;
	card_name: string;
	current_status: string;
	current_deck_id: string | null;
	steps: Array<{
		ts: string | null;
		kind: string;
		from_deck: string | null;
		from_deck_name: string | null;
		to_deck: string | null;
		to_deck_name: string | null;
		draft_id: string | null;
		summary: string;
	}>;
	count: number;
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

export async function apiDelete<T>(path: string): Promise<T> {
	const res = await fetch(`${API_URL}${path}`, { method: 'DELETE' });
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

export interface DossierCard {
	oracle_id: string;
	name: string;
	mana_cost: string;
	type_line: string;
	oracle_text: string;
	colors: string[];
	color_identity: string[];
	cmc: number;
	keywords: string[];
	image_uri: string;
}

export interface DossierPrinting {
	scryfall_id: string;
	oracle_id: string;
	set_code: string;
	set_name: string;
	collector_number: string;
	rarity: string;
	released_at: string | null;
	is_default: boolean;
	image_uri_normal: string;
	image_uri_art_crop: string;
	prices: Record<string, unknown>;
}

export interface DossierTag {
	tag: string;
	source: string;
	confidence: number | null;
}

export interface DossierDeckLine {
	deck_id: string;
	name: string;
	status: string;
	board: string;
	category: string | null;
	qty: number;
}

export interface DossierSynergyNeighbor {
	oracle_id: string;
	name: string;
	direction: string;
	weight: number;
	reasons: string[];
	reason_labels: string[];
	reason_text: string;
}

export interface DossierAffinity {
	oracle_id: string;
	affinity: number | null;
	notes: string;
	updated_at: string | null;
}

export interface DossierCollection {
	owned: boolean;
	copies: number;
	statuses: string[];
	finishes: string[];
}

export interface CardDossier {
	card: DossierCard;
	printing: DossierPrinting | null;
	printings: DossierPrinting[];
	tags: DossierTag[];
	decks: DossierDeckLine[];
	synergy: DossierSynergyNeighbor[];
	affinity: DossierAffinity | null;
	collection: DossierCollection;
}

export interface ReviewJobRecord {
	id: number;
	review_id: number;
	stage: string;
	status: string;
	error: string | null;
	ts: string | null;
}

export interface ReviewSection {
	type: string;
	title: string;
	data: Record<string, unknown>;
}

export interface ReviewExplanation {
	status: string;
	text: string;
	reason?: string | null;
	cards?: string[];
	rejected_cards?: string[];
}

export interface ReviewPanel {
	key: string;
	label: string;
	value: unknown;
	note?: string;
	unavailable?: string | null;
}

export interface ReviewResponse {
	id: number;
	deck_id: string;
	version: number;
	fingerprint: string;
	status: string;
	fact_pack: Record<string, unknown>;
	analysis: { adds?: Array<{ name: string; why?: string }>; cuts?: Array<{ name: string; why?: string }> } & Record<string, unknown>;
	doc: { sections: ReviewSection[] } & Record<string, unknown>;
	panels: ReviewPanel[];
	created_at: string | null;
	jobs: ReviewJobRecord[];
}
