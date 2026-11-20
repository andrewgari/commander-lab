// Pure derivations for the Home dashboard (docs: page-specs §1.3, §1.4). No I/O, no Svelte,
// so every rule is unit-testable. `now` is always passed in so server and client agree.
import type { DeckSummary, InventoryTotals } from '#lib/api.ts';

export type Source<T> = { ok: true; value: T } | { ok: false; reason: string };

export interface GameLite {
	id: number;
	deck_id: string;
	played_at: string | null;
	imported_at: string | null;
	result: string | null;
}
export interface DraftLite {
	id: string;
	name: string;
	closed_at: string | null;
	created_at: string | null;
	result_deck_id: string | null;
}
export interface PowerLite {
	power_level: number | null;
	estimated_bracket: number | null;
	bracket_name: string | null;
	snapshot_date: string | null;
}

export interface HomeSources {
	decks: Source<DeckSummary[]>;
	inventory: Source<InventoryTotals>;
	games: Source<GameLite[]>;
	drafts: Source<DraftLite[]>;
	committed: Source<DraftLite[]>;
	/** `null` = no snapshot or power not available. */
	profile: Source<PowerLite | null>;
}

export type SourceKey = keyof HomeSources;
export const SOURCE_LABELS: Record<SourceKey, string> = {
	decks: 'Decks',
	inventory: 'Inventory',
	games: 'Games',
	drafts: 'Drafts',
	committed: 'Draft history',
	profile: 'Profile'
};

/** One caption chunk. `datetime` renders as <time datetime title>. */
export interface Chunk {
	text: string;
	datetime?: string;
	title?: string;
}

export type TileKey = 'decks' | 'ready' | 'collection' | 'games' | 'drafts' | 'power';
export interface Tile {
	key: TileKey;
	label: string;
	href: string;
	state: 'ok' | 'empty' | 'error';
	/** Primary value text (ok state). */
	value: string;
	denominator?: string;
	valueTitle?: string;
	chunks: Chunk[];
	/** Empty state: words in the value slot and a CTA as the caption. */
	emptyText?: string;
	emptyCta?: string;
	emptyHref?: string;
	/** Error state. */
	errorSource?: string;
	errorReason?: string;
	/** T5 only: a second, separate link. */
	sublink?: { href: string; text: string };
	/** Accessible name of the whole tile. */
	name: string;
}

const nf = new Intl.NumberFormat('en-US');
export const fmt = (n: number) => nf.format(n);
export const GAMES_CAP = 200;

/** `YYYY-MM-DD` of an ISO-ish string, or null. */
export function isoDate(raw: string | null | undefined): string | null {
	if (!raw) return null;
	const m = /^\d{4}-\d{2}-\d{2}/.exec(raw);
	return m ? m[0] : null;
}

/** "today", "3 d ago"; beyond 60 days (or unparseable/future) the absolute date. */
export function relativeDate(raw: string | null | undefined, nowMs: number): Chunk | null {
	const abs = isoDate(raw);
	if (!raw || !abs) return null;
	const t = Date.parse(raw);
	if (Number.isNaN(t)) return { text: abs, datetime: abs, title: abs };
	const days = Math.floor((nowMs - t) / 86_400_000);
	let text = abs;
	if (days <= 0 && days > -1) text = 'today';
	else if (days > 0 && days <= 60) text = `${days} d ago`;
	return { text, datetime: raw, title: abs };
}

const gameDate = (g: GameLite) => g.played_at ?? g.imported_at;
const cmpDesc = (a: string | null, b: string | null) => (b ?? '').localeCompare(a ?? '');

function plural(n: number, one: string, many = `${one}s`) {
	return `${fmt(n)} ${n === 1 ? one : many}`;
}

function finish(t: Omit<Tile, 'name'>): Tile {
	let name: string;
	if (t.state === 'ok') {
		name = `${t.label}: ${t.value}${t.denominator ?? ''}, ${t.chunks.map((c) => c.text).join(', ')}`;
	} else if (t.state === 'empty') {
		name = `${t.label}: ${t.emptyText}, ${t.emptyCta}`;
	} else {
		name = `${t.label}: couldn't load`;
	}
	return { ...t, name };
}

function errorTile(key: TileKey, label: string, href: string, source: SourceKey, reason: string): Tile {
	return finish({
		key,
		label,
		href,
		state: 'error',
		value: '',
		chunks: [],
		errorSource: SOURCE_LABELS[source],
		errorReason: reason
	});
}

export function computeTiles(s: HomeSources, nowMs: number): Tile[] {
	const out: Tile[] = [];

	// T1 Decks + T2 Ready to play
	if (!s.decks.ok) {
		out.push(errorTile('decks', 'Decks', '/decks', 'decks', s.decks.reason));
		out.push(errorTile('ready', 'Ready to play', '/decks', 'decks', s.decks.reason));
	} else {
		const decks = s.decks.value;
		if (decks.length === 0) {
			const base = { href: '/decks', state: 'empty' as const, value: '', chunks: [], emptyText: 'No decks yet', emptyCta: 'Import a deck →', emptyHref: '/decks' };
			out.push(finish({ ...base, key: 'decks', label: 'Decks' }));
			out.push(finish({ ...base, key: 'ready', label: 'Ready to play' }));
		} else {
			const count = (st: string) => decks.filter((d) => d.status === st).length;
			const have = count('have');
			const chunks: Chunk[] = [{ text: `${fmt(have)} built` }, { text: `${fmt(count('virtual'))} virtual` }];
			const other = count('pending') + count('possible');
			if (other > 0) chunks.push({ text: `${fmt(other)} pending/possible` });
			out.push(finish({ key: 'decks', label: 'Decks', href: '/decks', state: 'ok', value: fmt(decks.length), chunks }));

			const built = decks.filter((d) => d.status === 'have');
			if (built.length === 0) {
				out.push(finish({ key: 'ready', label: 'Ready to play', href: '/decks', state: 'empty', value: '', chunks: [], emptyText: 'No built decks yet', emptyCta: 'See your decks →', emptyHref: '/decks' }));
			} else {
				const complete = built.filter((d) => d.have_buy?.complete === true).length;
				const buySum = decks.reduce((n, d) => n + (d.have_buy?.buy ?? 0), 0);
				const notComputed = decks.filter((d) => d.have_buy === null).length;
				const rc: Chunk[] = [{ text: `${fmt(buySum)} ${buySum === 1 ? 'card' : 'cards'} to buy` }];
				if (notComputed > 0) rc.push({ text: `${plural(notComputed, 'deck')} not computed` });
				out.push(finish({ key: 'ready', label: 'Ready to play', href: '/decks', state: 'ok', value: fmt(complete), denominator: `/${fmt(built.length)}`, chunks: rc }));
			}
		}
	}

	// T3 Collection
	if (!s.inventory.ok) {
		out.push(errorTile('collection', 'Collection', '/inventory', 'inventory', s.inventory.reason));
	} else {
		const t = s.inventory.value;
		if (t.cards === 0 && t.owned === 0) {
			out.push(finish({ key: 'collection', label: 'Collection', href: '/inventory', state: 'empty', value: '', chunks: [], emptyText: 'No cards yet', emptyCta: 'Open inventory →', emptyHref: '/inventory' }));
		} else {
			const chunks: Chunk[] = [{ text: plural(t.cards, 'card') }];
			if (t.in_mail > 0) chunks.push({ text: `${fmt(t.in_mail)} in the mail` });
			out.push(finish({ key: 'collection', label: 'Collection', href: '/inventory', state: 'ok', value: fmt(t.owned), valueTitle: undefined, chunks }));
		}
	}

	// T4 Games
	if (!s.games.ok) {
		out.push(errorTile('games', 'Games', '/games', 'games', s.games.reason));
	} else {
		const games = s.games.value;
		if (games.length === 0) {
			out.push(finish({ key: 'games', label: 'Games', href: '/games', state: 'empty', value: '', chunks: [], emptyText: 'No games yet', emptyCta: 'Import a Forge log →', emptyHref: '/games?import=1' }));
		} else {
			const wins = games.filter((g) => g.result === 'win').length;
			const losses = games.filter((g) => g.result === 'loss').length;
			const capped = games.length >= GAMES_CAP;
			const chunks: Chunk[] = [{ text: `${wins}–${losses}` }];
			const newest = games.map(gameDate).filter((d): d is string => !!d).sort(cmpDesc)[0] ?? null;
			const rel = relativeDate(newest, nowMs);
			if (rel) chunks.push({ ...rel, text: `last ${rel.text}` });
			out.push(finish({
				key: 'games',
				label: 'Games',
				href: '/games',
				state: 'ok',
				value: capped ? `${fmt(GAMES_CAP)}+` : fmt(games.length),
				valueTitle: capped ? `Showing the most recent ${GAMES_CAP} games` : undefined,
				chunks
			}));
		}
	}

	// T5 Open drafts
	if (!s.drafts.ok) {
		out.push(errorTile('drafts', 'Open drafts', '/drafts', 'drafts', s.drafts.reason));
	} else {
		const drafts = s.drafts.value;
		if (drafts.length === 0) {
			out.push(finish({ key: 'drafts', label: 'Open drafts', href: '/drafts', state: 'empty', value: '', chunks: [], emptyText: 'None open', emptyCta: 'Start a draft →', emptyHref: '/drafts' }));
		} else {
			const newest = drafts[0];
			const t = finish({
				key: 'drafts',
				label: 'Open drafts',
				href: '/drafts',
				state: 'ok',
				value: fmt(drafts.length),
				chunks: [{ text: newest.name }],
				sublink: { href: `/drafts/${encodeURIComponent(newest.id)}`, text: newest.name }
			});
			out.push(t);
		}
	}

	// T6 Power level
	if (!s.profile.ok) {
		out.push(errorTile('power', 'Power level', '/profile', 'profile', s.profile.reason));
	} else {
		const p = s.profile.value;
		if (!p || p.power_level === null) {
			out.push(finish({ key: 'power', label: 'Power level', href: '/profile', state: 'empty', value: '', chunks: [], emptyText: 'Not computed yet', emptyCta: 'Open profile →', emptyHref: '/profile' }));
		} else {
			const chunks: Chunk[] = [];
			if (p.estimated_bracket !== null) {
				chunks.push({ text: `Bracket ${p.estimated_bracket}${p.bracket_name ? ` (${p.bracket_name})` : ''}` });
			}
			if (p.snapshot_date) chunks.push({ text: `snapshot ${p.snapshot_date}`, datetime: p.snapshot_date });
			out.push(finish({ key: 'power', label: 'Power level', href: '/profile', state: 'ok', value: String(p.power_level), denominator: '/10', chunks }));
		}
	}
	return out;
}

export interface RecentReason {
	label: 'played' | 'built from draft';
	when: Chunk | null;
}
export interface RecentRow {
	deck: DeckSummary;
	reason: RecentReason | null;
}
export interface Recent {
	heading: 'Recently played' | 'Your decks';
	rows: RecentRow[];
}
export const RECENT_MAX = 5;

/** §1.4 fallback chain: recent games → committed-draft builds → first decks by name. */
export function recentDecks(
	decks: DeckSummary[],
	games: GameLite[],
	committed: DraftLite[],
	nowMs: number
): Recent {
	const byId = new Map(decks.map((d) => [d.id, d]));
	const rows: RecentRow[] = [];
	const seen = new Set<string>();

	const sortedGames = [...games].sort((a, b) => cmpDesc(gameDate(a), gameDate(b)));
	for (const g of sortedGames) {
		if (rows.length >= RECENT_MAX) break;
		const deck = byId.get(g.deck_id);
		if (!deck || seen.has(deck.id)) continue;
		seen.add(deck.id);
		rows.push({ deck, reason: { label: 'played', when: relativeDate(gameDate(g), nowMs) } });
	}

	const sortedDrafts = [...committed].sort((a, b) => cmpDesc(a.closed_at, b.closed_at));
	for (const d of sortedDrafts) {
		if (rows.length >= RECENT_MAX) break;
		const deck = d.result_deck_id ? byId.get(d.result_deck_id) : undefined;
		if (!deck || seen.has(deck.id)) continue;
		seen.add(deck.id);
		rows.push({ deck, reason: { label: 'built from draft', when: relativeDate(d.closed_at, nowMs) } });
	}

	if (rows.length > 0) return { heading: 'Recently played', rows };
	const first = [...decks].sort((a, b) => a.name.localeCompare(b.name)).slice(0, RECENT_MAX);
	return { heading: 'Your decks', rows: first.map((deck) => ({ deck, reason: null })) };
}

export function failedSources(s: HomeSources): { key: SourceKey; reason: string }[] {
	return (Object.keys(s) as SourceKey[]).flatMap((key) => {
		const v = s[key];
		return v.ok ? [] : [{ key, reason: v.reason }];
	});
}
