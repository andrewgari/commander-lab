/* Guided Primer Builder (PF). Five-step wizard; answers autosave as a server-side draft.
   Spec: Guided Primer Builder UX (t_13329c50). API (app.py):
     PUT  /api/decks/{id}/primer/draft      { answers }                         -> 204
     POST /api/decks/{id}/primer/suggest    { answers, field? }                 -> { suggestions:{field:[text]}, why:{card:text}, facts }
     POST /api/decks/{id}/primer/generate   { answers, options, keep:{sec:html} } -> { sections:[{id,title,html,inferred}], description?, warnings }
     POST /api/decks/{id}/primer            { html, answers, sections }          -> { version, has_undo }
     POST /api/decks/{id}/primer/undo
   Suggestions never write into a field; only "Fill the blanks" does, and only into empty ones. */
(function () {
    "use strict";
    var bootEl = document.getElementById("pfBoot");
    var page = document.querySelector(".pf-page");
    if (!bootEl || !page) return;
    var BOOT = JSON.parse(bootEl.textContent || "{}");
    var DECK = BOOT.deck || {};
    var API = "/api/decks/" + encodeURIComponent(DECK.id || page.dataset.deckId || page.dataset.deckName);
    var DECK_URL = "/deck/" + encodeURIComponent(DECK.name || page.dataset.deckName);
    var $ = function (s, r) { return (r || document).querySelector(s); };
    var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
    function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
    var REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var GEN_TIMEOUT_MS = 120000;

    var STEPS = [
        { id: "deck", t: "The deck", s: "Pitch · identity" },
        { id: "plan", t: "Game plan", s: "Win · three phases" },
        { id: "cards", t: "Key cards", s: "3–8 pillars" },
        { id: "table", t: "Table & piloting", s: "Mulligan · Rule 0" },
        { id: "gen", t: "Review & generate", s: "Draft · save" }
    ];
    var F = {
        pitch: { step: 0, kind: "area", autosize: true, label: "One-line pitch", max: 140, ph: "What does this deck do, in one breath?", hint: "Becomes the Overview's first line and the short description." },
        early: { step: 1, kind: "area", label: "Early game · turns 1–3", ph: "Ramp, first tokens, set up the engine…" },
        mid: { step: 1, kind: "area", label: "Mid game · turns 4–7", ph: "Deploy the engine, protect the board…" },
        late: { step: 1, kind: "area", label: "Closing · turn 8+", ph: "How the game actually ends…" },
        mull: { step: 3, kind: "area", label: "Mulligan: what to keep", ph: "Land count, what makes a hand a keeper…" },
        mist: { step: 3, kind: "area", label: "Common piloting mistakes", ph: "Over-committing into a wipe, attacking too early…", opt: true },
        weak: { step: 3, kind: "area", label: "What beats you", ph: "Wraths, graveyard hate, fast combo…", opt: true }
    };
    var SECS = [
        { id: "overview", t: "Overview", fields: ["pitch"], step: 0 },
        { id: "plan", t: "Game plan", fields: ["early", "mid", "late"], step: 1 },
        { id: "cards", t: "Key cards", fields: [], step: 2 },
        { id: "mull", t: "Mulligan & piloting", fields: ["mull", "mist"], step: 3 },
        { id: "table", t: "Table talk", fields: ["weak"], step: 3 }
    ];
    var INTRO = [
        ["The deck", "Almost all of this is already known. Check it, write the pitch (or take ours), move on."],
        ["Game plan", "How the game goes, turn by turn. Leave any box blank and it is drafted from the decklist."],
        ["Key cards", "The cards the deck is built around. One line each on why. Blank reasons are drafted."],
        ["Table & piloting", "What to keep, what goes wrong, and what the table should hear before shuffling."],
        ["Review & generate", "Pick length and voice, generate, then edit, lock or redraft each section."]
    ];

    /* ---- State ---- */
    var SUG = {};      // field -> [text] from the last suggest call (this step)
    var WHY = {};      // card -> reason
    var S;
    function fresh() {
        var a = BOOT.answers || {}, src = a.src || {};
        var o = { step: 0, f: {}, sugg: {}, gen: "idle", doc: [], desc: null, warnings: [], hasUndo: false };
        Object.keys(F).forEach(function (k) {
            var v = typeof a[k] === "string" ? a[k] : "";
            o.f[k] = { v: v, src: v.trim() ? (src[k] === "draft" ? "draft" : "yours") : "empty" };
        });
        o.tone = { v: a.tone || DECK.tone || "focused", src: a.tone ? "yours" : "journal" };
        o.bracket = a.bracket != null ? +a.bracket : (DECK.bracket != null ? +DECK.bracket : null);
        o.bracketSrc = a.bracket != null ? "yours" : "journal";
        o.ttw = a.ttw != null ? a.ttw : (DECK.ttw != null ? DECK.ttw : "");
        var wins = Array.isArray(a.win) && a.win.length ? a.win : (BOOT.win || []);
        o.win = wins.map(function (w) { return { id: w.id, t: w.t, on: w.on !== false && (Array.isArray(a.win) ? !!w.on : !!w.on), src: w.src || "draft" }; });
        var saved = {}; (a.cards || []).forEach(function (c) { if (c && c.name) saved[c.name] = c; });
        var seen = {};
        o.cards = (BOOT.cards || []).map(function (c) {
            var s = saved[c.name]; seen[c.name] = 1;
            return { n: c.name, r: c.roles || [], on: s ? s.on !== false : !!c.on, why: { v: s ? (s.why || "") : "", src: s && s.why ? (s.src === "draft" ? "draft" : "yours") : "empty" } };
        });
        Object.keys(saved).forEach(function (n) { if (!seen[n] && o.cards.length < 8) o.cards.push({ n: n, r: saved[n].roles || [], on: saved[n].on !== false, why: { v: saved[n].why || "", src: saved[n].why ? "yours" : "empty" } }); });
        var chosen = Array.isArray(a.facts) ? a.facts : null;
        o.facts = (BOOT.facts || []).map(function (x) { return { id: x.id, t: x.title, s: x.detail || "", det: !!x.on, say: !!x.on && (chosen ? chosen.indexOf(x.id) !== -1 : true) }; });
        var op = a.options || {};
        o.opts = { length: op.length || "standard", voice: op.voice || "plain", desc: op.description !== false, secs: {} };
        SECS.forEach(function (s) { o.opts.secs[s.id] = !(op.sections && op.sections.indexOf(s.id) === -1); });
        return o;
    }
    S = fresh();

    function answers() {
        var a = { src: {}, tone: S.tone.v, bracket: S.bracket, ttw: S.ttw === "" ? null : +S.ttw, options: {
            length: S.opts.length, voice: S.opts.voice, description: S.opts.desc,
            sections: SECS.filter(function (s) { return S.opts.secs[s.id]; }).map(function (s) { return s.id; }) } };
        Object.keys(F).forEach(function (k) { a[k] = S.f[k].v; if (S.f[k].src !== "empty") a.src[k] = S.f[k].src; });
        a.win = S.win.map(function (w) { return { id: w.id, t: w.t, on: w.on, src: w.src }; });
        a.cards = S.cards.map(function (c) { return { name: c.n, roles: c.r, on: c.on, why: c.why.v, src: c.why.src }; });
        a.facts = S.facts.filter(function (f) { return f.say; }).map(function (f) { return f.id; });
        return a;
    }

    /* ---- Network ---- */
    function api(method, path, body, ms) {
        var ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
        var timer = ctl && ms ? setTimeout(function () { ctl.abort(); }, ms) : null;
        return fetch(API + path, {
            method: method, headers: { "Content-Type": "application/json" },
            body: body === undefined ? undefined : JSON.stringify(body), signal: ctl ? ctl.signal : undefined
        }).then(function (res) {
            if (timer) clearTimeout(timer);
            if (res.status === 204) return { ok: true, status: 204, data: {} };
            return res.json().catch(function () { return {}; }).then(function (data) { return { ok: res.ok, status: res.status, data: data }; });
        }, function (err) {
            if (timer) clearTimeout(timer);
            return { ok: false, status: 0, data: { error: err && err.name === "AbortError" ? "The request timed out." : "The server could not be reached." } };
        });
    }

    /* ---- Helpers ---- */
    function glyph(src) { return src === "draft" ? "◇ Drafted" : src === "yours" ? "◆ Yours" : src === "journal" ? "From Journal" : ""; }
    function filled() {
        var n = 0, t = 0;
        Object.keys(F).forEach(function (k) { if (!F[k].opt) { t++; if (S.f[k].v.trim()) n++; } });
        t++; if (S.win.some(function (w) { return w.on; })) n++;
        t++; if (S.cards.filter(function (c) { return c.on; }).length >= 3) n++;
        return [n, t];
    }
    function say(msg) { var el = $("#pfLive"); el.textContent = ""; setTimeout(function () { el.textContent = msg; }, 20); }
    function toast(msg, undoFn, link) {
        var t = $("#pfToast"); t.hidden = false;
        t.innerHTML = "<span>" + esc(msg) + "</span>" + (link ? '<a class="hud-btn hud-btn--sm" href="' + esc(link.href) + '">' + esc(link.label) + "</a>" : "") +
            (undoFn ? '<button type="button" class="hud-btn hud-btn--sm" id="pfUndo">Undo</button>' : "");
        if (undoFn) $("#pfUndo").onclick = function () { undoFn(); t.hidden = true; };
        clearTimeout(toast._t); toast._t = setTimeout(function () { t.hidden = true; }, 8000);
    }

    /* ---- Autosave (debounced server draft) ---- */
    var saveTimer = null, saveSeq = 0;
    function saved() {
        var el = $("#pfSaved"); el.classList.add("is-saving"); el.textContent = "Saving…";
        clearTimeout(saveTimer);
        saveTimer = setTimeout(function () {
            var seq = ++saveSeq;
            api("PUT", "/primer/draft", { answers: answers() }).then(function (r) {
                if (seq !== saveSeq) return;
                el.classList.remove("is-saving");
                el.textContent = r.ok ? "Draft saved " + new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "Draft not saved: " + (r.data.error || "try again");
            });
        }, 500);
    }

    /* ---- Suggestions ---- */
    function sugIdx(k) { return (S.sugg[k] && S.sugg[k].i) || 0; }
    function suggRow(k) {
        var s = S.sugg[k]; if (!s) return "";
        var opts = SUG[k] || [], text = opts.length ? opts[s.i % opts.length] : "";
        var busy = s.busy, failed = s.failed;
        var body = busy ? '<span class="pf-skel"></span><span class="pf-skel"></span><span class="hud-muted">Drafting from your deck…</span>'
            : failed ? '<span class="hud-chip hud-chip--danger">Failed</span> <span class="hud-muted">Could not draft a suggestion. The field is untouched.</span>'
            : esc(text) + " <em>· from " + esc(DECK.cards || 100) + " cards + Journal</em>";
        return '<div class="pf-sugg' + (busy ? " is-busy" : "") + '" data-sugg="' + k + '" role="group" aria-label="Suggestion for ' + esc(F[k].label) + '">' +
            '<span class="pf-sugg-g" aria-hidden="true">◇</span><div class="pf-sugg-t" aria-live="polite">' + body + "</div>" +
            '<div class="pf-sugg-a">' + (failed
                ? '<button type="button" class="hud-btn hud-btn--sm" data-act="ask" data-k="' + k + '">Retry</button>'
                : '<button type="button" class="hud-btn hud-btn--sm hud-btn--primary" data-act="use" data-k="' + k + '"' + (busy ? " disabled" : "") + '>Use <span class="hud-kbd">⌘↵</span></button>' +
                  (opts.length > 1 ? '<button type="button" class="hud-btn hud-btn--sm" data-act="another" data-k="' + k + '"' + (busy ? " disabled" : "") + ">Another</button>" : "")) +
            '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost" data-act="dismiss" data-k="' + k + '" aria-label="Dismiss suggestion">✕</button></div></div>';
    }
    function field(k) {
        var d = F[k], f = S.f[k], id = "pf-" + k;
        var ctl = d.kind === "input"
            ? '<input type="text" id="' + id + '" data-k="' + k + '" maxlength="' + d.max + '" value="' + esc(f.v) + '" placeholder="' + esc(d.ph) + '">'
            : '<textarea id="' + id + '" data-k="' + k + '" class="pf-area' + (d.autosize ? " pf-area--auto" : "") + '" maxlength="' + (d.max || 600) + '" placeholder="' + esc(d.ph) + '">' + esc(f.v) + "</textarea>";
        var cnt = d.max ? '<span class="pf-count' + (f.v.length > d.max * 0.9 ? " is-over" : "") + '" id="' + id + '-n">' + f.v.length + "/" + d.max + "</span>" : "";
        return '<div class="pf-f" data-src="' + f.src + '" data-field="' + k + '">' +
            '<label for="' + id + '">' + esc(d.label) + (d.opt ? ' <span class="pf-opt">optional</span>' : "") + '<span class="pf-src" data-src="' + f.src + '">' + glyph(f.src) + "</span>" + cnt + "</label>" +
            ctl + (d.hint ? '<span class="hud-field-hint">' + esc(d.hint) + "</span>" : "") +
            (S.sugg[k] ? suggRow(k) : '<div><button type="button" class="hud-btn hud-btn--sm pf-ask" data-act="ask" data-k="' + k + '">' + (f.v ? "Suggest another" : "Suggest") + "</button></div>") + "</div>";
    }
    function seg(name, opts, val, label, src) {
        return '<div class="pf-f" data-src="' + (src || "") + '"><span class="pf-lab" id="l-' + name + '">' + label + (src ? '<span class="pf-src" data-src="' + src + '">' + glyph(src) + "</span>" : "") + "</span>" +
            '<div class="hud-seg" role="radiogroup" aria-labelledby="l-' + name + '">' + opts.map(function (o) {
                return '<input type="radio" name="' + name + '" id="' + name + "-" + o[0] + '" value="' + o[0] + '"' + (String(val) === String(o[0]) ? " checked" : "") + '><label for="' + name + "-" + o[0] + '">' + o[1] + "</label>";
            }).join("") + "</div></div>";
    }
    function chips(list, cls) { return (list || []).map(function (a) { return '<span class="hud-chip ' + cls + '">◆ ' + esc(a) + "</span>"; }).join(" ") || '<span class="hud-muted">None set</span>'; }

    /* Fetch suggestions; `only` limits to one field (Suggest / Another / Retry). */
    function fetchSuggestions(only) {
        return api("POST", "/primer/suggest", { answers: answers(), field: only || undefined }, 20000).then(function (r) {
            if (!r.ok) return false;
            Object.keys(r.data.suggestions || {}).forEach(function (k) { SUG[k] = r.data.suggestions[k]; });
            Object.keys(r.data.why || {}).forEach(function (k) { WHY[k] = r.data.why[k]; });
            return true;
        });
    }
    function ask(k, another) {
        var cur = S.sugg[k];
        S.sugg[k] = { i: another && cur ? cur.i + 1 : 0, busy: true };
        redrawField(k);
        var have = SUG[k] && SUG[k].length;
        (have ? Promise.resolve(true) : fetchSuggestions(k)).then(function (ok) {
            if (!S.sugg[k]) return;
            S.sugg[k].busy = false; S.sugg[k].failed = !ok || !(SUG[k] && SUG[k].length);
            redrawField(k);
            if (!S.sugg[k].failed) say("Suggestion ready for " + F[k].label);
        });
    }
    function redrawField(k) {
        var old = $('[data-field="' + k + '"]'); if (!old) return;
        var had = document.activeElement && document.activeElement.dataset && document.activeElement.dataset.k === k && document.activeElement.tagName !== "BUTTON";
        var tmp = document.createElement("div"); tmp.innerHTML = field(k); old.replaceWith(tmp.firstChild);
        if (had) { var el = $("#pf-" + k); if (el) el.focus(); }
        autosizeAll();
    }
    /* Grow textarea.pf-area--auto to fit its content (no CSS field-sizing fallback needed). */
    function autosizeTA(t) {
        if (!t || t.tagName !== "TEXTAREA" || !t.classList.contains("pf-area--auto")) return;
        t.style.height = "auto";
        t.style.height = t.scrollHeight + "px";
    }
    function autosizeAll() {
        var list = document.querySelectorAll("textarea.pf-area--auto");
        for (var i = 0; i < list.length; i++) autosizeTA(list[i]);
    }
    function use(k) {
        var s = S.sugg[k]; if (!s || s.busy || s.failed) return;
        var opts = SUG[k] || []; if (!opts.length) return;
        var prev = S.f[k], before = { v: prev.v, src: prev.src };
        S.f[k] = { v: opts[s.i % opts.length].slice(0, F[k].max || 600), src: "draft" }; delete S.sugg[k];
        redrawField(k); renderSide(); saved();
        var el = $("#pf-" + k); if (el) el.focus();
        toast("Used suggestion for " + F[k].label.toLowerCase(), function () { S.f[k] = before; redrawField(k); renderSide(); saved(); });
    }
    function fillAll() {
        var snap = JSON.parse(JSON.stringify(S));
        fetchSuggestions().then(function (ok) {
            if (!ok) { toast("Could not draft suggestions right now. Nothing was changed."); return; }
            var n = 0;
            Object.keys(F).forEach(function (k) { if (!S.f[k].v.trim() && SUG[k] && SUG[k][0]) { S.f[k] = { v: SUG[k][0].slice(0, F[k].max || 600), src: "draft" }; n++; } });
            var picked = S.win.filter(function (w) { return w.on; }).length;
            S.win.forEach(function (w) { if (!picked && !w.on && n >= 0 && S.win.indexOf(w) < 2) { w.on = true; } });
            S.cards.forEach(function (c) { if (c.on && !c.why.v.trim() && WHY[c.n]) { c.why = { v: WHY[c.n], src: "draft" }; n++; } });
            S.sugg = {}; render(); saved();
            toast("Filled " + n + " blanks. Nothing you wrote was touched.", function () { S = snap; render(); saved(); });
        });
    }

    /* ---- Steps ---- */
    function stepDeck() {
        return '<div class="pf-fixed"><dl class="hud-kv">' +
            "<dt>Commander</dt><dd>" + esc(DECK.cmdr || "—") + "</dd>" +
            "<dt>Archetype</dt><dd>" + chips(DECK.archetype, "hud-chip--signal") + "</dd>" +
            '<dt>Themes</dt><dd><span class="hud-chips">' + chips(DECK.themes, "hud-chip--signal") + "</span></dd>" +
            '<dt>Kindred</dt><dd><span class="hud-chips">' + chips(DECK.kindred, "hud-chip--signal") + "</span></dd>" +
            '</dl><p class="hud-field-hint" style="margin:var(--sp-2) 0 0">Pulled from the deck and its Journal. <a href="' + esc(BOOT.journal_url || "#") + '">Edit in Journal ↗</a></p></div>' +
            field("pitch") +
            '<div class="pf-row pf-row--3">' +
            seg("tone", [["casual", "Casual"], ["focused", "Focused"], ["competitive", "Competitive"]], S.tone.v, "Table tone", S.tone.src) +
            seg("bracket", [[1, "1"], [2, "2"], [3, "3"], [4, "4"], [5, "5"]], S.bracket, "Bracket", S.bracket == null ? "" : S.bracketSrc) +
            '<div class="pf-f" data-src="' + (DECK.ttw != null && String(S.ttw) === String(DECK.ttw) ? "journal" : "yours") + '"><label for="pf-ttw">Target win turn<span class="pf-src" data-src="journal">' + (DECK.ttw != null ? "From Journal" : "") + '</span></label><input type="number" id="pf-ttw" min="1" max="20" value="' + esc(S.ttw) + '"></div></div>';
    }
    function stepPlan() {
        return '<div class="pf-f"><span class="pf-lab" id="l-win">How does it win?<span class="pf-opt">pick one or more</span></span>' +
            '<div class="pf-picks" role="group" aria-labelledby="l-win">' + S.win.map(function (w) {
                return '<button type="button" class="pf-pick' + (w.src === "yours" ? " is-yours" : "") + '" aria-pressed="' + w.on + '" data-act="win" data-id="' + esc(w.id) + '">' + esc(w.t) + "</button>";
            }).join("") + '<button type="button" class="pf-pick is-plain" data-act="addwin">Add your own</button></div>' +
            '<span class="hud-field-hint">Dashed = proposed from the decklist and tags. Solid = in the primer.</span></div>' +
            '<div class="pf-row pf-row--3" style="grid-template-columns:1fr">' + field("early") + field("mid") + field("late") + "</div>";
    }
    function stepCards() {
        var on = S.cards.filter(function (c) { return c.on; }).length;
        return '<div class="pf-cards-head"><div><b style="color:var(--text-1)" id="pfCardN">' + on + ' of 8</b> <span class="hud-muted">picked · suggested from role tags, synergy and your Journal pet cards</span></div>' +
            '<button type="button" class="hud-btn hud-btn--sm pf-ask" data-act="whyall">Draft all reasons</button></div>' +
            (S.cards.length ? '<div class="pf-cards" role="list">' + S.cards.map(function (c, i) {
                var w = c.why;
                return '<div class="pf-cr' + (c.on ? "" : " is-off") + '" role="listitem"><input type="checkbox" data-act="pick" data-i="' + i + '" aria-label="Feature ' + esc(c.n) + '"' + (c.on ? " checked" : "") + (!c.on && on >= 8 ? " disabled" : "") + ">" +
                    '<div class="pf-thumb" aria-hidden="true"></div>' +
                    '<div class="pf-cr-name"><b class="pf-cardname" tabindex="0" data-card-preview data-cp-name="' + esc(c.n) + '">' + esc(c.n) + '</b><span class="hud-chips">' + c.r.map(function (r) { return '<span class="hud-chip hud-chip--ghost">' + esc(r) + "</span>"; }).join("") + "</span></div>" +
                    '<div class="pf-cr-why"><div class="pf-f" data-src="' + w.src + '"><input type="text" data-why="' + i + '" value="' + esc(w.v) + '" maxlength="120" placeholder="' + (c.on ? "Why is it here? (blank = drafted for you)" : "Not featured") + '" aria-label="Why ' + esc(c.n) + ' is in the deck"' + (c.on ? "" : " disabled") + "></div></div></div>";
            }).join("") + "</div>" : '<p class="hud-empty">No cards to suggest yet. Sync the deck first.</p>');
    }
    function stepTable() {
        return field("mull") + '<div class="pf-row">' + field("mist") + field("weak") + "</div>" +
            '<div class="pf-f"><span class="pf-lab" id="l-facts">Rule 0 · what to tell the table<span class="pf-src" data-src="draft">◇ Detected</span></span>' +
            '<div class="pf-facts" role="group" aria-labelledby="l-facts">' + S.facts.map(function (x) {
                return '<label class="pf-fact"><span><b>' + esc(x.t) + "</b>" + (x.s ? "<span>" + esc(x.s) + "</span>" : "") + '</span><span class="hud-toggle"><input type="checkbox" data-act="fact" data-id="' + esc(x.id) + '"' + (x.say ? " checked" : "") + (x.det ? "" : " disabled") + '><span class="hud-muted">Say it</span></span></label>';
            }).join("") + '</div><span class="hud-field-hint">Detected from the decklist, not written by the model. Switched-on facts become a "Before you sit down" list in the primer.</span></div>';
    }
    function stepGen() {
        var fl = filled(), miss = fl[1] - fl[0];
        var secToggles = SECS.map(function (s) { return '<label class="hud-toggle"><input type="checkbox" data-act="sec" data-id="' + s.id + '"' + (S.opts.secs[s.id] ? " checked" : "") + "> " + s.t + "</label>"; }).join("");
        return '<div class="pf-opts">' +
            seg("length", [["brief", "Brief"], ["standard", "Standard"], ["deep", "Deep"]], S.opts.length, "Length") +
            seg("voice", [["plain", "Plain"], ["playful", "Playful"], ["tactical", "Tactical"]], S.opts.voice, "Voice") +
            '<div class="pf-f"><span class="pf-lab">Sections</span><div style="display:flex;flex-direction:column;gap:var(--sp-2)">' + secToggles + "</div></div>" +
            '<div class="pf-f"><span class="pf-lab">Also</span><label class="hud-toggle"><input type="checkbox" id="optDesc"' + (S.opts.desc ? " checked" : "") + "> Write the short deck description (≤ 280 chars)</label><span class=\"hud-field-hint\">Staged in the Description panel as a suggestion. Nothing is written until you use it.</span></div></div>" +
            '<div class="pf-gen"><p class="pf-gen-t">' + (miss ? "<b>" + miss + " blank" + (miss > 1 ? "s" : "") + ".</b> Blanks are inferred from card data and marked ◇ so you can see what the model made up." : "<b>Everything answered.</b> The draft will lean on what you wrote.") + "</p>" +
            '<button type="button" class="hud-btn hud-btn--primary pf-btn-lg" data-act="generate"' + (S.gen === "run" ? ' disabled aria-busy="true"' : "") + ">" + (S.doc.length ? "Regenerate unlocked" : "Generate primer") + ' <span class="hud-kbd">⌘↵</span></button></div>' +
            '<div class="pf-status" id="pfStatus" role="status" aria-live="polite" hidden></div><div id="pfOut"></div>';
    }
    var BODY = [stepDeck, stepPlan, stepCards, stepTable, stepGen];

    /* ---- Draft document ---- */
    function renderDoc() {
        var out = $("#pfOut"); if (!out) return;
        if (!S.doc.length) { out.innerHTML = ""; return; }
        var desc = S.desc ? '<div class="pf-desc"><span class="hud-label">Deck description <span class="pf-src" data-src="draft" style="margin-left:var(--sp-2)">◇ Drafted</span></span><p>' + esc(S.desc) + '</p><p class="hud-field-hint">Staged on the deck page. Use it there to save it.</p></div>' : "";
        var warn = S.warnings.length ? '<div class="pf-status" role="status" data-s="err"><span class="hud-chip hud-chip--signal">Check</span><span>' + S.warnings.map(esc).join("<br>") + "</span></div>" : "";
        out.innerHTML = desc + warn + '<div class="pf-doc" aria-label="Primer draft">' + S.doc.map(function (d, i) {
            var src = d.locked ? "locked" : d.edited ? "yours" : d.src;
            var tag = d.locked ? "◆ Locked" : d.edited ? "◆ Yours" : d.src === "yours" ? "◆ Yours" : "◇ Drafted";
            return '<section class="pf-sec' + (d.busy ? " is-busy" : "") + '" data-id="' + d.id + '" data-src="' + src + '" style="--i:' + i + '" aria-busy="' + !!d.busy + '">' +
                "<h3>" + esc(d.t) + ' <span class="pf-src" data-src="' + (src === "draft" ? "draft" : "yours") + '">' + tag + "</span></h3>" +
                '<div class="pf-sec-a"><button type="button" class="hud-btn hud-btn--sm" data-act="redraft" data-id="' + d.id + '"' + (d.locked || d.busy ? " disabled" : "") + ">Redraft</button>" +
                '<button type="button" class="hud-btn hud-btn--sm" data-act="edit" data-id="' + d.id + '">' + (d.editing ? "Done" : "Edit") + "</button>" +
                '<button type="button" class="hud-btn hud-btn--sm' + (d.locked ? " hud-btn--primary" : "") + '" data-act="lock" data-id="' + d.id + '" aria-pressed="' + !!d.locked + '">' + (d.locked ? "Locked" : "Lock") + "</button></div>" +
                '<div class="pf-sec-b"><span class="pf-skel"></span><span class="pf-skel"></span>' + (d.editing ? '<textarea data-doc="' + d.id + '" aria-label="Edit ' + esc(d.t) + '">' + esc(d.html) + "</textarea>" : d.html) + "</div></section>";
        }).join("") + '</div><p class="pf-replace">Saving replaces the current primer. The previous version is kept; Undo is on the confirmation.</p>';
    }
    function status(s, msg, act) {
        var el = $("#pfStatus"); if (!el) return;
        if (!s) { el.hidden = true; return; }
        el.hidden = false; el.dataset.s = s;
        var chip = { run: '<span class="pf-spin" aria-hidden="true"></span><span class="hud-chip hud-chip--data">Drafting</span>', ok: '<span class="hud-chip hud-chip--ok">Done</span>', err: '<span class="hud-chip hud-chip--danger">Failed</span>', na: '<span class="hud-chip hud-chip--signal">Not available yet</span>' }[s];
        el.innerHTML = chip + "<span>" + esc(msg) + "</span>" + (act || "");
    }
    function setSave(enabled) { var sv = $('[data-act="save"]'); if (sv) sv.disabled = !enabled; }
    function docById(id) { return S.doc.filter(function (x) { return x.id === id; })[0]; }
    /* ◇ when the server inferred a blank, or any answer feeding the section was accepted from a suggestion. */
    function secSrc(inferred, id) {
        var sec = SECS.filter(function (s) { return s.id === id; })[0];
        var drafted = sec && sec.fields.some(function (k) { return S.f[k].src === "draft"; });
        return (inferred && inferred.length) || drafted ? "draft" : "yours";
    }

    function generate() {
        if (S.gen === "run") return;
        var wasEdit = $$("[data-doc]"); wasEdit.forEach(function (ta) { var d = docById(ta.dataset.doc); if (d && d.editing) { if (ta.value !== d.html) { d.html = ta.value; d.edited = true; } d.editing = false; } });
        var keep = {};
        S.doc.forEach(function (d) { if (d.locked || d.edited) keep[d.id] = d.html; });
        var opts = answers().options;
        S.gen = "run"; renderDoc(); setSave(false);
        var b = $('[data-act="generate"]'); if (b) { b.disabled = true; b.setAttribute("aria-busy", "true"); }
        status("run", "Drafting " + opts.sections.length + " sections from " + (DECK.cards || 100) + " cards and your answers…");
        api("POST", "/primer/generate", { answers: answers(), options: opts, keep: keep }, GEN_TIMEOUT_MS).then(function (r) {
            S.gen = "idle";
            if (b) { b.disabled = false; b.removeAttribute("aria-busy"); }
            if (r.status === 501) return status("na", "The drafting service is not available yet. Your answers are saved, and you can still write the primer by hand.", '<a class="hud-btn hud-btn--sm" href="' + esc(DECK_URL) + '/primer#edit">Write by hand</a>');
            if (!r.ok || !r.data.success) {
                var offline = r.data.error_kind === "oracle_offline";
                return status("err", (r.data.error || "The draft failed.") + " Your answers are untouched.", '<button type="button" class="hud-btn hud-btn--sm" data-act="generate">Retry</button>' + (offline ? "" : ""));
            }
            var old = {}; S.doc.forEach(function (d) { old[d.id] = d; });
            S.doc = (r.data.sections || []).map(function (s) {
                var o = old[s.id] || {};
                var kept = o.locked || o.edited;
                return { id: s.id, t: s.title || o.t, html: s.html, src: kept ? o.src : secSrc(s.inferred, s.id), locked: o.locked, edited: o.edited };
            });
            S.warnings = r.data.warnings || [];
            if (r.data.description) S.desc = r.data.description;
            renderDoc(); renderSide(); setSave(S.doc.length > 0);
            var lbl = $('[data-act="generate"]'); if (lbl) lbl.firstChild.textContent = "Regenerate unlocked ";
            status("ok", "Drafted " + S.doc.filter(function (d) { return !keep[d.id]; }).length + " sections" + (r.data.description ? " and a description" : "") + ". " + Object.keys(keep).length + " locked or edited kept as written.");
            say("Primer draft ready");
        });
    }
    function redraft(id) {
        var d = docById(id); if (!d || d.locked) return;
        d.busy = true; d.edited = false; renderDoc();
        var opts = answers().options; opts.sections = [id]; opts.description = false;
        api("POST", "/primer/generate", { answers: answers(), options: opts, keep: {} }, GEN_TIMEOUT_MS).then(function (r) {
            d.busy = false;
            var sec = r.ok && r.data.success ? (r.data.sections || []).filter(function (s) { return s.id === id; })[0] : null;
            if (sec) { d.html = sec.html; d.src = secSrc(sec.inferred, id); say(d.t + " redrafted"); }
            else toast("Could not redraft " + d.t + ": " + (r.data.error || "try again") + ". The section is unchanged.");
            renderDoc();
        });
    }
    function savePrimer() {
        $$("[data-doc]").forEach(function (ta) { var d = docById(ta.dataset.doc); if (d && d.editing) { if (ta.value !== d.html) { d.html = ta.value; d.edited = true; } d.editing = false; } });
        var html = S.doc.map(function (d) { return "<h3>" + esc(d.t) + "</h3>" + d.html; }).join("");
        var btn = $('[data-act="save"]'); if (btn) btn.disabled = true;
        api("POST", "/primer", { html: html, answers: answers(), sections: S.doc.map(function (d) { return { id: d.id, title: d.t }; }) }).then(function (r) {
            if (btn) btn.disabled = false;
            if (!r.ok || !r.data.success) return toast("Could not save the primer: " + (r.data.error || "try again") + ".");
            S.hasUndo = !!r.data.has_undo;
            toast("Primer saved to " + (DECK.name || "the deck") + "." + (r.data.has_undo ? " Previous version kept." : ""), r.data.has_undo ? function () {
                api("POST", "/primer/undo").then(function (u) { toast(u.ok ? "Restored the previous primer." : "Nothing to undo."); });
            } : null, { href: DECK_URL, label: "View deck" });
            renderRail();
        });
    }

    /* ---- Chrome ---- */
    function renderRail() {
        $("#pfRail").innerHTML = STEPS.map(function (s, i) {
            var done = i < S.step;
            return '<li><button type="button" class="pf-step' + (done ? " is-done" : "") + '" data-act="go" data-i="' + i + '"' + (i === S.step ? ' aria-current="step"' : "") + '><span class="pf-step-n">' + (done ? "✓" : i + 1) + '</span><span class="pf-step-t">' + s.t + '</span><span class="pf-step-s">' + s.s + "</span></button></li>";
        }).join("");
        $("#pfBar").innerHTML = '<div class="pf-bar-seg" aria-hidden="true">' + STEPS.map(function (s, i) { return '<i class="' + (i < S.step ? "is-done" : i === S.step ? "is-now" : "") + '"></i>'; }).join("") + '</div><div class="pf-bar-row"><b>' + STEPS[S.step].t + "</b><span>" + (S.step + 1) + " / " + STEPS.length + "</span></div>";
    }
    function renderSide() {
        var fl = filled(), segs = "";
        for (var i = 0; i < fl[1]; i++) segs += '<i class="' + (i < fl[0] ? "is-done" : "") + '"></i>';
        var rows = SECS.map(function (s) {
            var chip;
            if (!S.opts.secs[s.id]) chip = '<span class="hud-chip hud-chip--ghost">Off</span>';
            else if (s.id === "cards") { var n = S.cards.filter(function (c) { return c.on; }).length; chip = n >= 3 ? '<span class="hud-chip hud-chip--neutral">' + n + " cards</span>" : '<span class="hud-chip hud-chip--signal">Needs 3</span>'; }
            else {
                var have = s.fields.filter(function (k) { return S.f[k].v.trim(); }).length;
                var yours = s.fields.some(function (k) { return S.f[k].src === "yours"; });
                chip = have === 0 ? '<span class="hud-chip hud-chip--ghost">◇ Inferred</span>' : have === s.fields.length ? '<span class="hud-chip ' + (yours ? "hud-chip--signal" : "hud-chip--data") + '">' + (yours ? "◆ Yours" : "◇ Drafted") + "</span>" : '<span class="hud-chip hud-chip--neutral">' + have + "/" + s.fields.length + "</span>";
            }
            return '<li><button type="button" data-act="go" data-i="' + s.step + '">' + s.t + "</button>" + chip + "</li>";
        }).join("");
        $("#pfSide").innerHTML =
            '<section class="hud-panel"><header class="hud-panel-head"><h3 class="hud-panel-title">Primer outline</h3><div class="hud-panel-meta">' + fl[0] + " / " + fl[1] + " answered</div></header><div class=\"hud-panel-body\">" +
            '<div class="pf-meter"><b>' + Math.round(fl[0] / fl[1] * 100) + '<small style="font-size:var(--fs-sm);color:var(--text-3)">%</small></b><span>' + (fl[0] === fl[1] ? "ready to generate" : "blanks get drafted for you") + '</span></div><div class="hud-progress-bar" style="margin-bottom:var(--sp-3)" aria-hidden="true">' + segs + "</div>" +
            '<ul class="pf-ol">' + rows + "</ul></div>" +
            '<footer class="hud-panel-foot"><button type="button" class="hud-btn hud-btn--sm pf-ask" data-act="fillall" style="width:100%;justify-content:center">Fill the blanks</button></footer></section>' +
            '<div class="pf-keys-wrap hud-panel"><div class="hud-panel-body"><dl class="pf-keys"><dt><span class="hud-kbd">⌘↵</span></dt><dd>Use suggestion / generate</dd><dt><span class="hud-kbd">Alt</span> <span class="hud-kbd">→</span></dt><dd>Next step</dd><dt><span class="hud-kbd">Alt</span> <span class="hud-kbd">←</span></dt><dd>Back</dd><dt><span class="hud-kbd">Esc</span></dt><dd>Dismiss suggestion</dd></dl></div></div>';
    }
    function render(dir) {
        renderRail(); renderSide();
        var i = S.step, last = i === STEPS.length - 1, card = $("#pfCard");
        card.className = "pf-card" + (dir && !REDUCED ? " is-enter" : "");
        if (dir) card.style.setProperty("--pf-dir", (dir > 0 ? 14 : -14) + "px");
        card.innerHTML = '<header class="pf-card-head"><div><h2 id="pfH" tabindex="-1">' + INTRO[i][0] + "</h2><p>" + INTRO[i][1] + "</p></div>" +
            (i < 4 ? '<span class="hud-label" style="white-space:nowrap">Step ' + (i + 1) + " of 5</span>" : "") + "</header>" +
            '<div class="pf-card-body">' + BODY[i]() + "</div>" +
            '<footer class="pf-card-foot"><button type="button" class="hud-btn" data-act="back"' + (i === 0 ? " disabled" : "") + ">← Back</button>" +
            (last ? '<div style="display:flex;gap:var(--sp-2);align-items:center"><span class="hud-field-hint pf-keyhint">Nothing is written to the deck until you save.</span><button type="button" class="hud-btn hud-btn--primary" data-act="save"' + (S.doc.length ? "" : " disabled") + ">Save primer</button></div>"
                : '<div style="display:flex;gap:var(--sp-2)">' + (i >= 1 ? '<button type="button" class="hud-btn hud-btn--ghost" data-act="skip">Skip to draft</button>' : "") + '<button type="button" class="hud-btn hud-btn--primary" data-act="next">Next →</button></div>') + "</footer>";
        if (last) { renderDoc(); }
        $("#pfStep").textContent = "Step " + (i + 1) + " of 5: " + STEPS[i].t;
        history.replaceState(null, "", "#step=" + (i + 1));
        autosizeAll();
    }
    function go(i, dir) {
        S.step = Math.max(0, Math.min(STEPS.length - 1, i)); S.sugg = {}; SUG = {};
        render(dir === undefined ? 1 : dir);
        var h = $("#pfH"); if (h) h.focus({ preventScroll: true });
        window.scrollTo({ top: 0 });
        autoAsk();
    }
    /* On entering a step, blank text fields get ONE round trip of proposals. */
    function autoAsk() {
        var keys = Object.keys(F).filter(function (k) { return F[k].step === S.step && !S.f[k].v.trim() && !F[k].opt; });
        if (!keys.length) return;
        var step = S.step;
        keys.forEach(function (k) { S.sugg[k] = { i: 0, busy: true }; redrawField(k); });
        fetchSuggestions().then(function (ok) {
            if (S.step !== step) return;
            keys.forEach(function (k) {
                if (!S.sugg[k] || S.f[k].v.trim()) { delete S.sugg[k]; redrawField(k); return; }
                S.sugg[k].busy = false; S.sugg[k].failed = !ok || !(SUG[k] && SUG[k].length);
                redrawField(k);
            });
        });
    }

    /* ---- Events ---- */
    document.addEventListener("click", function (e) {
        var b = e.target.closest("[data-act]"); if (!b || !page.contains(b) && b.closest("#pfToast") == null) return;
        var a = b.dataset.act, k = b.dataset.k, id = b.dataset.id, d;
        if (a === "go") go(+b.dataset.i, +b.dataset.i > S.step ? 1 : -1);
        else if (a === "next") go(S.step + 1, 1);
        else if (a === "back") go(S.step - 1, -1);
        else if (a === "skip") go(STEPS.length - 1, 1);
        else if (a === "ask") ask(k);
        else if (a === "another") ask(k, true);
        else if (a === "use") use(k);
        else if (a === "dismiss") { delete S.sugg[k]; redrawField(k); var el = $("#pf-" + k); if (el) el.focus(); }
        else if (a === "win") { var w = S.win.filter(function (x) { return x.id === id; })[0]; w.on = !w.on; if (w.on && w.src === "draft") w.src = "draft"; b.setAttribute("aria-pressed", w.on); renderSide(); saved(); }
        else if (a === "addwin") { var t = window.prompt("Win condition"); if (t && t.trim()) { S.win.push({ id: "c" + Date.now(), t: t.trim().slice(0, 60), on: true, src: "yours" }); render(); saved(); } }
        else if (a === "whyall") {
            fetchSuggestions().then(function (ok) {
                var n = 0; S.cards.forEach(function (c) { if (c.on && !c.why.v.trim() && WHY[c.n]) { c.why = { v: WHY[c.n], src: "draft" }; n++; } });
                render(); if (n) saved(); toast(ok ? (n ? "Drafted " + n + " reasons for the blank rows." : "No blank rows to draft.") : "Could not draft reasons right now.");
            });
        }
        else if (a === "fillall") fillAll();
        else if (a === "generate") generate();
        else if (a === "redraft") redraft(id);
        else if (a === "edit") { d = docById(id); if (d.editing) { var ta = $('[data-doc="' + id + '"]'); if (ta && ta.value !== d.html) { d.html = ta.value; d.edited = true; } } d.editing = !d.editing; renderDoc(); }
        else if (a === "lock") { d = docById(id); d.locked = !d.locked; renderDoc(); say(d.t + (d.locked ? " locked. Regenerate will skip it." : " unlocked.")); }
        else if (a === "save") savePrimer();
    });
    document.addEventListener("input", function (e) {
        var t = e.target, k = t.dataset && t.dataset.k;
        if (k && F[k]) {
            S.f[k].v = t.value; S.f[k].src = t.value.trim() ? "yours" : "empty";
            var wrap = t.closest(".pf-f"); wrap.dataset.src = S.f[k].src;
            var sp = $(".pf-src", wrap); if (sp) { sp.dataset.src = S.f[k].src; sp.textContent = glyph(S.f[k].src); }
            var n = $("#pf-" + k + "-n"); if (n) { n.textContent = t.value.length + "/" + F[k].max; n.classList.toggle("is-over", t.value.length > F[k].max * 0.9); }
            if (S.sugg[k]) { delete S.sugg[k]; var row = $(".pf-sugg", wrap); if (row) { var holder = document.createElement("div"); holder.innerHTML = '<button type="button" class="hud-btn hud-btn--sm pf-ask" data-act="ask" data-k="' + k + '">Suggest another</button>'; row.replaceWith(holder); } }
            autosizeTA(t);
            renderSide(); saved();
        } else if (t.dataset && t.dataset.why != null) {
            var c = S.cards[+t.dataset.why]; c.why = { v: t.value, src: t.value.trim() ? "yours" : "empty" }; t.closest(".pf-f").dataset.src = c.why.src; saved();
        } else if (t.id === "pf-ttw") { S.ttw = t.value; saved(); }
    });
    document.addEventListener("change", function (e) {
        var t = e.target;
        if (!page.contains(t)) return;
        if (t.name === "tone") { S.tone = { v: t.value, src: "yours" }; var s = t.closest(".pf-f"); s.dataset.src = "yours"; var sp = $(".pf-src", s); if (sp) { sp.dataset.src = "yours"; sp.textContent = glyph("yours"); } saved(); }
        else if (t.name === "length" || t.name === "voice") { S.opts[t.name] = t.value; saved(); }
        else if (t.name === "bracket") { S.bracket = +t.value; S.bracketSrc = "yours"; saved(); }
        else if (t.dataset.act === "pick") { S.cards[+t.dataset.i].on = t.checked; var at = +t.dataset.i; render(); var cb = $$('[data-act="pick"]')[at]; if (cb) cb.focus(); saved(); }
        else if (t.dataset.act === "fact") { S.facts.filter(function (x) { return x.id === t.dataset.id; })[0].say = t.checked; saved(); }
        else if (t.dataset.act === "sec") { S.opts.secs[t.dataset.id] = t.checked; renderSide(); saved(); }
        else if (t.id === "optDesc") { S.opts.desc = t.checked; saved(); }
    });
    document.addEventListener("keydown", function (e) {
        if (!page.contains(e.target) && e.target !== document.body) return;
        var mod = e.metaKey || e.ctrlKey;
        if (mod && e.key === "Enter") {
            e.preventDefault();
            var f = document.activeElement && document.activeElement.closest && document.activeElement.closest("[data-field]");
            var k = f && f.dataset.field;
            if (k && S.sugg[k]) use(k); else if (S.step === 4) generate();
        } else if (e.altKey && e.key === "ArrowRight") { e.preventDefault(); go(S.step + 1, 1); }
        else if (e.altKey && e.key === "ArrowLeft") { e.preventDefault(); go(S.step - 1, -1); }
        else if (e.key === "Escape") {
            var f2 = document.activeElement && document.activeElement.closest && document.activeElement.closest("[data-field]");
            if (f2 && S.sugg[f2.dataset.field]) { var kk = f2.dataset.field; delete S.sugg[kk]; redrawField(kk); var el = $("#pf-" + kk); if (el) el.focus(); }
        }
    });

    var m = /step=(\d)/.exec(location.hash);
    S.step = m ? Math.max(0, Math.min(4, +m[1] - 1)) : 0;
    render(0);
    if (BOOT.has_draft || BOOT.has_primer) { var sv = $("#pfSaved"); sv.textContent = BOOT.has_draft ? "Draft restored" : "Started from the saved primer's answers"; }
    autoAsk();
})();
