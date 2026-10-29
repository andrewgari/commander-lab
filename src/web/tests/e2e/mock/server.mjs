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
			activeId: null
		},
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
	state.requests.push({ method, path, body });
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
