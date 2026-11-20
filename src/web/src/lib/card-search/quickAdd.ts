// Pure: parseQuickAdd("2x Sol Ring") -> {qty, name} (docs/ui/card-search.md §4).
// Same grammar as infra/providers/raw_paste.py: ^(\d+)\s*[xX]?\s+(.+)$

const QTY_PREFIX = /^(\d+)\s*[xX]?\s+(.+)$/s;
/** A bare quantity with no name typed yet ("2", "2x", "2 x "). */
const QTY_ONLY = /^\d+\s*[xX]?\s*$/;

export type QuickAddFailure = 'empty' | 'multiline' | 'bad_qty';

export type QuickAddResult =
	| { ok: true; qty: number; name: string }
	| { ok: false; reason: QuickAddFailure };

export interface QuickAddOptions {
	/** `fixedQty: 1` (the `commander` preset): a "2x" prefix is read and dropped, never added twice. */
	fixedQty?: 1;
}

function clean(name: string): string {
	return name.replace(/\s+/g, ' ').trim();
}

/**
 * Parse what the user typed into the quick-add input.
 *
 * - `2 Sol Ring`, `2 x Sol Ring`, `2x Sol Ring`, `2X Sol Ring` -> qty 2
 * - `Sol Ring` -> qty 1
 * - `0 Sol Ring`, or a quantity beyond the safe-integer range -> `bad_qty` (nothing is added)
 * - a multi-line paste -> `multiline` (v1 does not handle list pastes; the UI says "Paste a list via Import")
 * - nothing but whitespace, or only a quantity -> `empty`
 */
export function parseQuickAdd(input: string, opts: QuickAddOptions = {}): QuickAddResult {
	const text = input.trim();
	if (!text) return { ok: false, reason: 'empty' };
	if (/[\r\n]/.test(text)) return { ok: false, reason: 'multiline' };
	if (QTY_ONLY.test(text)) return { ok: false, reason: 'empty' };

	const m = QTY_PREFIX.exec(text);
	if (!m) return { ok: true, qty: 1, name: clean(text) };

	const qty = Number(m[1]);
	if (!Number.isSafeInteger(qty) || qty < 1) return { ok: false, reason: 'bad_qty' };
	const name = clean(m[2]);
	if (!name) return { ok: false, reason: 'empty' };
	return { ok: true, qty: opts.fixedQty ?? qty, name };
}

/**
 * The part of the input that goes to autocomplete: the name without any quantity prefix. Unlike
 * `parseQuickAdd` this never fails, since the user is still typing ("2x" -> "").
 */
export function autocompleteTerm(input: string): string {
	const text = input.trim();
	if (QTY_ONLY.test(text)) return '';
	const m = QTY_PREFIX.exec(text);
	return clean(m ? m[2] : text);
}
