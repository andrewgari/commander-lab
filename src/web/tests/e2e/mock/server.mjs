// Mock Commander Lab API for the Playwright suite.
//
// SvelteKit's load functions and actions call the API from the Node server, so browser-level
// request interception cannot reach them. This process stands in for FastAPI instead: the web
// server is started with COMMANDER_API_URL pointing here, and tests steer it through /__mock/*.
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '../../../../..');
const reviewDoc = JSON.parse(readFileSync(resolve(root, 'tests/fixtures/review_document/example.json'), 'utf8'));
const seedProfile = JSON.parse(readFileSync(resolve(root, 'profiles/andrew.json'), 'utf8'));

const PORT = Number(process.env.MOCK_API_PORT ?? 8799);
const AT = '2026-10-01T12:00:00+00:00';

// --- provenance, mirroring `leaf_keys` / `apply_stated_edit` in player_profile.py --------------
const VALUE_FIELDS = [
	'builder_identity', 'color_affinities', 'archetype_affinities', 'favored_mechanics', 'pet_cards',
	'dislikes', 'power_tendencies', 'budget_norms', 'review_preferences', 'done_when'
];
function leafEntries(data) {
	const out = new Map();
	const walk = (prefix, value) => {
		if (Array.isArray(value)) for (const item of value) out.set(`${prefix}/${item}`, item);
		else if (value !== null && typeof value === 'object') for (const [k, v] of Object.entries(value)) walk(`${prefix}.${k}`, v);
		else if (value !== null && value !== undefined && value !== '') out.set(prefix, value);
	};
	for (const f of VALUE_FIELDS) if (f in data) walk(f, data[f]);
	if (data.budget_norms?.currency === 'USD') out.delete('budget_norms.currency');
	return out;
}

// --- drafts: ids deliberately look like the real ones (uuids, `archidekt:` ids) so the no-ids test bites ---
const CARDS = [
	{ oracle_id: '11111111-aaaa-4bbb-8ccc-000000000001', name: 'Sol Ring', mana_cost: '{1}', cmc: 1, type_line: 'Artifact', color_identity: [], rarity: 'uncommon', owned_qty: 3, free_qty: 2 },
	{ oracle_id: '11111111-aaaa-4bbb-8ccc-000000000002', name: 'Solemn Simulacrum', mana_cost: '{4}', cmc: 4, type_line: 'Artifact Creature — Golem', color_identity: [], rarity: 'rare', owned_qty: 1, free_qty: 1 },
	{ oracle_id: '11111111-aaaa-4bbb-8ccc-000000000003', name: 'Sol Talisman', mana_cost: '{3}', cmc: 3, type_line: 'Artifact', color_identity: [], rarity: 'uncommon', owned_qty: 1, free_qty: 0 },
	{ oracle_id: '11111111-aaaa-4bbb-8ccc-000000000004', name: 'Command Tower', mana_cost: '', cmc: 0, type_line: 'Land', color_identity: [], rarity: 'common', owned_qty: 2, free_qty: 1 },
	{ oracle_id: '11111111-aaaa-4bbb-8ccc-000000000005', name: 'Atraxa, Praetors’ Voice', mana_cost: '{G}{W}{U}{B}', cmc: 4, type_line: 'Legendary Creature — Phyrexian Angel Horror', color_identity: ['W', 'U', 'B', 'G'], rarity: 'mythic', owned_qty: 1, free_qty: 1 }
];
const cardLine = (c, qty, board) => ({
	oracle_id: c.oracle_id, name: c.name, qty, board, category: null, type_line: c.type_line, cmc: c.cmc,
	color_identity: c.color_identity, image_uri: ''
});
function freshDraft() {
	return {
		draft: {
			id: 'draft-1', kind: 'combine', status: 'open', name: 'Atraxa rebuild',
			source_decks: [{ id: 'archidekt:1001', name: 'Sliver Swarm' }, { id: 'archidekt:1002', name: 'Elf Ball' }],
			target_name: 'Atraxa Superfriends', target_commanders: [], result_deck_id: null, notes: '',
			created_at: AT, updated_at: AT, closed_at: null
		},
		cards: [cardLine(CARDS[4], 1, 'commander'), cardLine(CARDS[3], 1, 'main')],
		moves: [{
			instance_id: '22222222-bbbb-4ccc-8ddd-000000000001', action: 'assign', from_deck: 'archidekt:1001',
			oracle_id: CARDS[0].oracle_id, card_name: 'Sol Ring', status: 'in_deck', deck_id: 'archidekt:1001'
		}]
	};
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function freshState() {
	const profile = { metadata: { source: 'seed' }, ...structuredClone(seedProfile), pet_cards: ['Elspeth, Sun\u2019s Champion'] };
	const provenance = {};
	for (const key of leafEntries(profile).keys()) provenance[key] = { source: 'stated', at: AT };
	provenance['archetype_affinities.equipment'] = { source: 'learned_from_feedback', at: AT, evidence: ['Kept equipment in 3 reviews'] };
	return {
		profile,
		provenance,
		config: {
			down: false,
			profileMissing: false,
			putStatus: 200,
			putDetail: '',
			listStatus: 200,
			rowStatus: 200,
			feedbackStatus: 200,
			generateStatus: 202,
			pdfStatus: 200,
			documents: [],
			activeId: null,
			// drafts: commit/abandon answer `commitStatus` / `abandonStatus` after `*DelayMs`; the card search
			// answers `searchStatus`, optionally empty / degraded / slow.
			commitStatus: 200,
			commitDetail: 'The draft changed since you opened it. Refresh and try again.',
			commitDelayMs: 0,
			abandonStatus: 200,
			abandonDelayMs: 0,
			searchStatus: 200,
			searchDelayMs: 0,
			searchEmpty: false,
			searchDegraded: false
		},
		draft: freshDraft(),
		requests: []
	};
}
let state = freshState();

const summary = (d) => ({
	id: d.id, deck_id: d.deck_id ?? 'deck-1', status: d.status, stage: d.stage ?? '', error: d.error ?? null,
	grade: d.status === 'ok' ? 'B' : null, headline: d.status === 'ok' ? 'Solid shell' : null,
	created_at: d.created_at, started_at: d.started_at ?? d.created_at, finished_at: d.finished_at ?? null
});

function send(res, status, body, type = 'application/json') {
	res.writeHead(status, { 'content-type': type });
	res.end(typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body));
}
const readJson = (req) => new Promise((ok) => {
	let b = '';
	req.on('data', (c) => (b += c));
	req.on('end', () => ok(b ? JSON.parse(b) : {}));
});

const server = createServer(async (req, res) => {
	const url = new URL(req.url, `http://${req.headers.host}`);
	const path = url.pathname;
	const method = req.method;

	// Control plane: never counted, never subject to `down`.
	if (path === '/__mock/reset') { state = freshState(); return send(res, 200, { ok: true }); }
	if (path === '/__mock/config') {
		Object.assign(state.config, await readJson(req));
		return send(res, 200, state.config);
	}
	if (path === '/__mock/requests') return send(res, 200, state.requests);
	if (path === '/__mock/state') return send(res, 200, { profile: state.profile, provenance: state.provenance });

	const body = method === 'GET' || method === 'HEAD' ? {} : await readJson(req);
	state.requests.push({ method, path, body, query: url.search });
	const c = state.config;
	if (c.down) return req.socket.destroy();
	const fail = (status, detail) => send(res, status, { detail });

	// --- profile ---------------------------------------------------------------------------------
	if (path === '/api/v2/profile/player/andrew') {
		if (method === 'GET') {
			if (c.profileMissing) return fail(404, 'No profile for andrew');
			return send(res, 200, {
				name: 'andrew', profile: state.profile, provenance: state.provenance,
				versions: [{ version: 1, kind: 'seed', created_at: AT }], pending_proposals: []
			});
		}
		if (method === 'PUT') {
			if (c.putStatus !== 200) return fail(c.putStatus, c.putDetail || 'Internal Server Error');
			const before = leafEntries(state.profile);
			const after = leafEntries(body.values);
			const next = {};
			for (const [key, value] of after) {
				const old = before.get(key);
				next[key] = old !== undefined && JSON.stringify(old) === JSON.stringify(value) && state.provenance[key]
					? state.provenance[key]
					: { source: 'stated', at: new Date().toISOString() };
			}
			state.profile = body.values;
			state.provenance = next;
			return send(res, 200, { ok: true });
		}
	}
	if (path === '/api/v2/profile/latest') return send(res, 200, { available: false, reason: 'no deck imported yet', snapshot: null });
	if (path === '/api/v2/profile/history') return send(res, 200, { snapshots: [] });
	if (path === '/api/v2/profile/overrides') return send(res, 200, { overrides: {}, known_keys: {} });
	if (path === '/api/cards/autocomplete') return send(res, 200, []);


	// --- drafts ------------------------------------------------------------------------------------
	if (path === '/api/v2/decks' && method === 'GET') {
		const d = (id, name) => ({ id, name, status: 'have', source: 'archidekt', archidekt_id: null, color_identity: [], commanders: [], card_qty: 100, have_buy: null });
		return send(res, 200, { decks: [d('archidekt:1001', 'Sliver Swarm'), d('archidekt:1002', 'Elf Ball')] });
	}
	const builtDeck = path.match(/^\/api\/v2\/decks\/(built-deck-1)$/);
	if (builtDeck) {
		const totals = { have: 0, need: 0, pending: 0, buy: 0, have_pct: null, complete: false };
		return send(res, 200, {
			deck: { id: builtDeck[1], name: 'Atraxa Superfriends', archidekt_id: null, status: 'virtual', color_identity: ['W', 'U', 'B', 'G'], commanders: ['Atraxa, Praetors’ Voice'], source: 'draft', fingerprint: 'x', created_at: AT, updated_at: AT, archived_at: null },
			boards: {}, have_buy: { deck_id: builtDeck[1], totals, lines: [] }, snapshot: null
		});
	}
	if (path === '/api/v2/cards/search' && method === 'GET') {
		if (c.searchDelayMs) await sleep(c.searchDelayMs);
		if (c.searchStatus !== 200) return fail(c.searchStatus, 'card search service unreachable');
		const q = (url.searchParams.get('name') ?? '').toLowerCase();
		const hits = c.searchEmpty ? [] : CARDS.filter((x) => x.name.toLowerCase().includes(q));
		return send(res, 200, {
			results: hits.map((x) => ({ ...x, oracle_text: '', image: x.name, can_be_commander: false, pairing: null })),
			page: 1, has_more: false, total: hits.length, query: q, source: 'local', degraded: c.searchDegraded
		});
	}
	const dr = path.match(/^\/api\/v2\/drafts\/([^/]+)(?:\/(cards|moves|commit|abandon|suggestions)(?:\/(.+))?)?$/);
	if (dr) {
		const [, , sub, rest] = dr;
		const dst = state.draft;
		if (!sub && method === 'GET') return send(res, 200, dst);
		if (sub === 'suggestions' && method === 'GET') {
			return send(res, 200, { adds: [], hidden_donor_pulls: 0, hidden_reason: null, cuts: [], inputs_unavailable: {}, note: 'Suggestions are advisory.' });
		}
		if (sub === 'cards' && method === 'POST') {
			const i = dst.cards.findIndex((x) => x.oracle_id === body.oracle_id && x.board === body.board);
			if (i >= 0) dst.cards[i].qty = body.qty;
			else dst.cards.push(cardLine(CARDS.find((x) => x.oracle_id === body.oracle_id) ?? { ...body, type_line: '', cmc: 0, color_identity: [] }, body.qty, body.board));
			return send(res, 200, dst);
		}
		if (sub === 'cards' && method === 'DELETE') {
			const [oracle, board] = (rest ?? '').split('/');
			dst.cards = dst.cards.filter((x) => !(x.oracle_id === oracle && x.board === board));
			return send(res, 200, dst);
		}
		if (sub === 'moves' && method === 'POST') {
			dst.moves.push({ instance_id: body.instance_id, action: body.action, from_deck: null, oracle_id: '', card_name: 'Unknown card', status: 'in_collection', deck_id: null });
			return send(res, 200, dst);
		}
		if (sub === 'moves' && method === 'DELETE') {
			dst.moves = dst.moves.filter((m) => m.instance_id !== rest);
			return send(res, 200, dst);
		}
		if (sub === 'commit' && method === 'POST') {
			if (c.commitDelayMs) await sleep(c.commitDelayMs);
			if (c.commitStatus !== 200) return fail(c.commitStatus, c.commitDetail);
			dst.draft.status = 'committed';
			dst.draft.result_deck_id = 'built-deck-1';
			return send(res, 200, { deck_id: 'built-deck-1' });
		}
		if (sub === 'abandon' && method === 'POST') {
			if (c.abandonDelayMs) await sleep(c.abandonDelayMs);
			if (c.abandonStatus !== 200) return fail(c.abandonStatus, 'The draft could not be abandoned.');
			dst.draft.status = 'abandoned';
			return send(res, 200, { ok: true });
		}
	}
	if (path === '/api/v2/drafts' && method === 'GET') return send(res, 200, { drafts: [state.draft.draft] });

	// --- review documents --------------------------------------------------------------------------
	const list = path.match(/^\/api\/v2\/decks\/([^/]+)\/review-documents$/);
	if (list) {
		if (method === 'POST') {
			if (c.generateStatus >= 400) return fail(c.generateStatus, 'The generator is unavailable');
			const id = Math.max(0, ...c.documents.map((d) => d.id)) + 1;
			c.documents.unshift({ id, status: 'queued', stage: 'queued', created_at: new Date().toISOString() });
			c.activeId = id;
			return send(res, 202, { ...summary(c.documents[0]), already_running: false });
		}
		if (c.listStatus !== 200) return fail(c.listStatus, 'Reviews are unavailable');
		const docs = c.documents.map(summary);
		return send(res, 200, { documents: docs, active: docs.find((d) => d.id === c.activeId) ?? null });
	}
	const one = path.match(/^\/api\/v2\/review-documents\/(\d+)(\/feedback|\/pdf)?$/);
	if (one) {
		const id = Number(one[1]);
		const d = c.documents.find((x) => x.id === id);
		if (one[2] === '/feedback') {
			if (c.feedbackStatus !== 200) return fail(c.feedbackStatus, 'Feedback store unavailable');
			state.feedback ??= {};
			if (method === 'POST') state.feedback[body.swap_id] = { cut: '', add: '', decision: body.decision, reason: body.reason ?? '' };
			return send(res, 200, { feedback: state.feedback });
		}
		if (one[2] === '/pdf') {
			if (c.pdfStatus !== 200) return fail(c.pdfStatus, 'The PDF renderer is offline');
			return send(res, 200, '%PDF-1.4\n%mock\n', 'application/pdf');
		}
		if (c.rowStatus !== 200) return fail(c.rowStatus, 'Document store unavailable');
		if (!d) return fail(404, 'No such review');
		return send(res, 200, { ...summary(d), doc: d.status === 'ok' ? reviewDoc : null });
	}
	return fail(404, `mock API has no route for ${method} ${path}`);
});

server.listen(PORT, '127.0.0.1', () => console.log(`mock API on http://127.0.0.1:${PORT}`));
