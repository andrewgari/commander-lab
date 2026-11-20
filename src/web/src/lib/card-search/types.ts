// Shared types for the card-search-to-add module (docs/ui/card-search.md §2.1, §3, §5.1).
// Types only: no runtime code, so every file can import from here freely.

export type Board = 'commander' | 'main' | 'sideboard' | 'maybeboard' | 'considering';
export type Color = 'W' | 'U' | 'B' | 'R' | 'G' | 'C';
export type OwnedFilter = 'any' | 'owned' | 'not_owned';
/** Color identity match mode: `id<=` / `id=` / `id>=` on Scryfall (§3). */
export type CiMode = 'within' | 'exact' | 'includes';
export type CardType =
	| 'creature'
	| 'instant'
	| 'sorcery'
	| 'artifact'
	| 'enchantment'
	| 'planeswalker'
	| 'land'
	| 'battle';
/** Mana value chip: `0`..`6`, or `7+` for "7 or more". */
export type ManaValue = '0' | '1' | '2' | '3' | '4' | '5' | '6' | '7+';
export type Rarity = 'common' | 'uncommon' | 'rare' | 'mythic';

export type Pairing =
	| 'partner'
	| 'partner_group'
	| 'partner_with'
	| 'friends_forever'
	| 'choose_background'
	| 'background'
	| 'doctors_companion'
	| 'doctor';

/** The filter state of one card search. All filters AND together; values inside one filter OR (§3). */
export interface Filters {
	name: string;
	ci: Color[];
	ci_mode: CiMode;
	type: CardType[];
	/** Free text for subtype / supertype (`elf`, `legendary`). */
	type_text: string;
	mv: ManaValue[];
	rarity: Rarity[];
	/** Oracle text terms; `"quoted phrase"` stays one term. */
	oracle: string;
	owned: OwnedFilter;
	/** "Can be commander" (`cmdr=1`, Scryfall `is:commander`, §3.1). */
	commander: boolean;
	/** oracle_id of the first commander; narrows to its legal partners (§3.1.3). Needs `commander`. */
	pair: string | null;
}

export interface CardHit {
	oracle_id: string;
	name: string;
	mana_cost: string;
	cmc: number;
	type_line: string;
	color_identity: Color[];
	/** Rarity of the printing Scryfall returned. */
	rarity: string;
	oracle_text: string;
	/** Pass to `cardImage(name)`; never hotlinked. */
	image: string;
	/** in_collection + in_deck + in_mail (never not_owned). */
	owned_qty: number;
	/** in_collection: owned and not bound to a deck. */
	free_qty: number;
	/** §3.1 eligibility; always present, drives the `Commander` badge. */
	can_be_commander: boolean;
	/** §3.1.3, from partner_profile; null = cannot take a partner. */
	pairing: Pairing | null;
}

/** Named bundles of defaults (presets.ts). v1: `commander`, the /brew/new commander-pick step. */
export type PresetName = 'commander';

export interface AddRequest {
	card: CardHit;
	qty: number;
	board: Board;
}

export interface SearchResponse {
	results: CardHit[];
	page: number;
	has_more: boolean;
	/** null when the server cannot count honestly (not_owned mode, §5.2). */
	total: number | null;
	/** The translated Scryfall query (footer "Open on Scryfall"). */
	query: string;
	source: 'scryfall' | 'cache' | 'local';
	degraded: boolean;
}

/** One row of `GET /api/cards/autocomplete` (name-only suggestions; no oracle_id until CS-1 adds it, §5.3). */
export interface AutocompleteHit {
	name: string;
	uid: string;
	image: string;
	type_line: string;
	color_identity: Color[];
	colors: Color[];
	keywords: string[];
	mana_cost: string;
	oracle_id?: string;
}

export interface AutocompleteResponse {
	results: AutocompleteHit[];
	source: 'scryfall' | 'cache' | 'local';
	degraded: boolean;
}
