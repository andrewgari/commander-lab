/* Pillar description assist (PD): the "◇ Draft / ◇ Improve" button on the Identity page pillar editor.
   Spec: docs/design/pillar-describe/SPEC.md. Backend: POST /api/decks/{id}/identity/pillars/describe
     body  { name, description, pillar_id? }  (live, unsaved form values; nothing is saved by the call)
     200   { success, description, mode: "generate"|"improve" }
     422 bad input | 503 no model configured | 502 provider failed   ->  { success:false, error, error_kind? }

       var pd = PillarDescribe.mount(tile, { url, cards, pillarId });   // after the tile is in the DOM
       PillarDescribe.fieldHtml({ id, description })                     // markup for the description field
       PillarDescribe.handleKey(e)  -> true when the key was consumed (Alt+G, Esc, Cmd/Ctrl+Enter)
       PillarDescribe.unmount()                                          // aborts any request; call before re-render

   The assist never writes into the field except through Use / Undo. Typing cancels an in-flight
   request and drops a showing suggestion. Pure helpers (modeOf, clamp, classify, ...) need no DOM and
   run in node (tests/test_pillar_describe_ui.py). Everything from the server goes through esc(). */
(function (root) {
    "use strict";
    var MAX = 400, NAME_MAX = 40, TIMEOUT_MS = 20000;

    function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

    // ---- Pure helpers -------------------------------------------------------------------
    function modeOf(text) { return String(text || "").trim() ? "improve" : "draft"; }
    function canGen(name, text) { return !!(String(name || "").trim() || String(text || "").trim()); }
    // Server clamps already; never rely on it. Cut at the last sentence end <= MAX, else hard cut.
    function clamp(text) {
        var t = String(text || "").replace(/\s+/g, " ").trim();
        if (t.length <= MAX) return t;
        var cut = t.slice(0, MAX), end = Math.max(cut.lastIndexOf(". "), cut.lastIndexOf("! "), cut.lastIndexOf("? "));
        if (end < 0 && /[.!?]$/.test(cut)) end = cut.length - 1;
        return end > MAX / 2 ? cut.slice(0, end + 1) : cut.replace(/\s+\S*$/, "").trim() || cut;
    }
    // Map a failed call to a UI state. 422 is an inline hint (server text, escaped by the renderer);
    // everything else is an error row with fixed copy, never a stack trace or the raw server string.
    function classify(status, mode, timedOut) {
        var verb = mode === "improve" ? "improve" : "draft";
        if (timedOut) return { kind: "err", retry: true, msg: "Couldn't " + verb + " the description. It took too long." };
        if (status === 422 || status === 400) return { kind: "hint" };
        if (status === 429) return { kind: "err", retry: true, msg: "Too many requests. Wait a moment, then try again." };
        if (status === 503) return { kind: "na", retry: false, msg: "Drafting is unavailable right now (no model configured). Write it yourself; everything else works." };
        return { kind: "err", retry: true, msg: "Couldn't " + verb + " the description. The service answered with an error." };
    }
    function labelFor(mode, busy) { return busy ? (mode === "improve" ? "Improving…" : "Drafting…") : (mode === "improve" ? "Improve" : "Draft"); }
    function titleFor(ok, mode) {
        return !ok ? "Add a name or a few words first" : mode === "improve" ? "Rewrite my description (Alt+G)" : "Draft a description from the name and this deck's cards (Alt+G)";
    }
    function hintFor(s, cards) {
        var n = cards ? cards + " " : "", ok = canGen(s.name, s.text), m = modeOf(s.text);
        if (s.sugg && s.sugg.status === "busy") return "Reading the name and " + (cards ? cards + " " : "") + "cards. Esc cancels, typing cancels.";
        if (s.sugg && s.sugg.status === "ok") return "Suggestion below. Nothing changes until you press Use.";
        if (!ok) return "Name it or jot a few words, then ◇ Draft.";
        if (m === "improve") return "◇ Improve rewrites your words so cards match them better. Alt+G.";
        return "◇ Draft writes it from the name and this deck's " + n + "cards. Alt+G.";
    }

    // ---- Markup -------------------------------------------------------------------------
    function fieldHtml(o) {
        var id = esc(o.id);
        return '<div class="pd-f" data-pd-root>' +
            '<div class="pd-lab"><label class="hud-label" for="pd-' + id + '">What belongs here</label>' +
            '<span class="pd-src" data-pd-src hidden></span><span class="pd-count" data-pd-count aria-live="off"></span>' +
            '<button class="hud-btn hud-btn--sm pd-gen" type="button" data-pd-gen aria-describedby="pdh-' + id + '" aria-keyshortcuts="Alt+G"><span class="pd-gen-g" aria-hidden="true">◇</span><span data-pd-genl>Draft</span></button></div>' +
            '<textarea id="pd-' + id + '" data-field="description" maxlength="' + MAX + '" aria-describedby="pdh-' + id + '">' + esc(o.description || "") + "</textarea>" +
            '<div class="pd-sugg" data-pd-sugg hidden></div>' +
            '<div class="pd-undo" data-pd-undo hidden><span>Replaced your text.</span><button class="hud-btn hud-btn--sm hud-btn--ghost" type="button" data-pd="undo">Undo</button></div>' +
            '<p class="pd-hint" id="pdh-' + id + '">Describe it in words: cards are scored against this text (embeddings), not tags. <span class="pd-hint-dyn" data-pd-hint></span></p></div>';
    }

    // ---- Controller ---------------------------------------------------------------------
    var current = null;

    function mount(tile, opts) {
        unmount();
        var $ = function (s) { return tile.querySelector(s); };
        var ta = $('[data-field="description"]'), nm = $('[data-field="name"]'), root = $("[data-pd-root]");
        if (!ta || !nm || !root) return null;
        var S = { name: nm.value, text: ta.value, sugg: null, src: null, undo: null, ctl: null, timer: null, inserting: false, warn: "", recent: [] };
        var api = { tile: tile, request: request, use: use, undo: undo, cancel: cancel, state: S, destroy: destroy };

        function abort() { if (S.ctl) { try { S.ctl.abort(); } catch (e) { } } S.ctl = null; clearTimeout(S.timer); S.timer = null; }
        function destroy() { abort(); }

        function grow() { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight + 2, 240) + "px"; }
        function replace(text) {
            S.inserting = true; ta.focus(); ta.select();
            var done = false;
            try { done = !!(document.execCommand && document.execCommand("insertText", false, text)); } catch (e) { }
            if (!done || ta.value !== text) { ta.value = text; }
            S.inserting = false; grow();
        }

        function sync() {
            var busy = !!(S.sugg && S.sugg.status === "busy"), ok = canGen(S.name, S.text), m = modeOf(S.text);
            var gen = $("[data-pd-gen]");
            $("[data-pd-genl]").textContent = labelFor(m, busy);
            gen.setAttribute("aria-busy", String(busy));
            gen.setAttribute("aria-disabled", String(!ok));
            gen.title = titleFor(ok, m);
            var c = $("[data-pd-count]"); c.textContent = S.text.length + "/" + MAX; c.classList.toggle("is-over", S.text.length >= MAX);
            var src = $("[data-pd-src]");
            src.hidden = !S.src; root.dataset.src = S.src || ""; src.dataset.src = S.src || "";
            src.textContent = S.src === "draft" ? "◇ Drafted" : S.src === "yours" ? "◆ Yours" : "";
            var h = $("[data-pd-hint]");
            h.className = "pd-hint-dyn" + (S.warn ? " is-warn" : "");
            h.textContent = S.warn || hintFor(S, opts.cards);
            renderSugg();
            $("[data-pd-undo]").hidden = S.undo == null;
        }

        function renderSugg() {
            var el = $("[data-pd-sugg]"), s = S.sugg;
            if (!s) { el.hidden = true; el.className = "pd-sugg"; el.innerHTML = ""; return; }
            el.hidden = false;
            var improve = s.mode === "improve";
            if (s.status === "busy") {
                el.className = "pd-sugg is-busy"; el.setAttribute("role", "status");
                el.innerHTML = '<span class="pd-sugg-g" aria-hidden="true">◇</span><div><span class="pd-skel"></span><span class="pd-skel"></span><p class="pd-sugg-meta">' +
                    (improve ? "Improving your description…" : "Drafting from the name" + (opts.cards ? " and " + esc(opts.cards) + " cards" : " and this deck's cards") + "…") + "</p></div>" +
                    '<div class="pd-sugg-a"><button class="hud-btn hud-btn--sm hud-btn--ghost" type="button" data-pd="cancel">Cancel <span class="hud-kbd">Esc</span></button></div>';
            } else if (s.status === "ok") {
                el.className = "pd-sugg"; el.setAttribute("role", "status");
                el.innerHTML = '<span class="pd-sugg-g" aria-hidden="true">◇</span><div><p class="pd-sugg-t">' + esc(s.text) + '</p><p class="pd-sugg-meta">' + s.text.length + "/" + MAX + " · " + (improve ? "rewrite of your text" : "drafted from name + cards") + "</p></div>" +
                    '<div class="pd-sugg-a"><button class="hud-btn hud-btn--sm hud-btn--primary pd-act-use" type="button" data-pd="use">' + (improve ? "Replace" : "Use") + ' <span class="hud-kbd">⌘↵</span></button>' +
                    '<button class="hud-btn hud-btn--sm pd-act-more" type="button" data-pd="more">Another</button>' +
                    '<button class="hud-btn hud-btn--sm hud-btn--ghost pd-sugg-end" type="button" data-pd="dismiss" aria-label="Dismiss suggestion">✕ <span class="hud-kbd">Esc</span></button></div>';
            } else {
                el.className = "pd-sugg is-err"; el.setAttribute("role", "alert");
                el.innerHTML = '<span class="pd-sugg-g" aria-hidden="true">!</span><div><p class="pd-sugg-t">' + esc(s.msg) + '</p><p class="pd-sugg-meta">Your text is untouched.</p></div>' +
                    '<div class="pd-sugg-a">' + (s.retry ? '<button class="hud-btn hud-btn--sm" type="button" data-pd="retry">Try again</button>' : "") +
                    '<button class="hud-btn hud-btn--sm hud-btn--ghost pd-sugg-end" type="button" data-pd="dismiss">Dismiss <span class="hud-kbd">Esc</span></button></div>';
            }
        }

        function request() {
            if (!canGen(S.name, S.text)) { S.warn = "Add a name or a few words first."; sync(); nm.focus(); return; }
            if (S.sugg && S.sugg.status === "busy") return;
            abort(); S.warn = "";
            var mode = modeOf(S.text), ctl = new AbortController(), timedOut = false;
            S.ctl = ctl; S.sugg = { status: "busy", mode: mode }; sync();
            S.timer = setTimeout(function () { timedOut = true; ctl.abort(); }, opts.timeout || TIMEOUT_MS);
            var body = { name: S.name.trim(), description: S.text.trim() };
            if (opts.pillarId && opts.pillarId !== "new" && String(opts.pillarId).indexOf("tmp-") !== 0) body.pillar_id = opts.pillarId;
            var status = 0;
            fetch(opts.url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: ctl.signal })
                .then(function (r) { status = r.status; return r.json().catch(function () { return {}; }); })
                .then(function (j) {
                    if (S.ctl !== ctl) return;                                   // cancelled or superseded
                    clearTimeout(S.timer); S.ctl = null;
                    var text = j && j.success !== false && status < 400 && typeof j.description === "string" ? clamp(j.description) : "";
                    if (text) {
                        S.sugg = { status: "ok", mode: j.mode === "improve" ? "improve" : mode, text: text };
                    } else {
                        var c = classify(status >= 400 ? status : 502, mode, false);
                        if (c.kind === "hint") { S.sugg = null; S.warn = (j && typeof j.error === "string" && j.error) || "Add a name or a few words first."; }
                        else S.sugg = { status: c.kind, mode: mode, msg: c.msg, retry: c.retry };
                    }
                    sync();
                }, function () {
                    if (S.ctl !== ctl) return;
                    clearTimeout(S.timer); S.ctl = null;
                    var c = classify(0, mode, timedOut);
                    S.sugg = { status: timedOut ? "err" : c.kind, mode: mode, msg: c.msg, retry: c.retry };
                    sync();
                });
        }
        function cancel() { abort(); S.sugg = null; S.warn = ""; sync(); ta.focus(); }
        function more() { if (S.sugg && S.sugg.text) S.recent = S.recent.concat([S.sugg.text]).slice(-3); S.sugg = null; request(); }
        function use() {
            var s = S.sugg; if (!s || s.status !== "ok") return;
            var prev = S.text;
            replace(s.text);                                                      // insertText keeps native Ctrl+Z
            S.undo = prev.trim() ? prev : null; S.text = ta.value; S.src = "draft"; S.sugg = null; S.warn = ""; sync();
        }
        function undo() {
            if (S.undo == null) return;
            replace(S.undo); S.text = ta.value; S.src = "yours"; S.undo = null; sync();
        }

        nm.addEventListener("input", function () { S.name = nm.value; S.warn = ""; if (S.sugg && (S.sugg.status === "busy" || S.sugg.status === "ok")) { abort(); S.sugg = null; } sync(); });
        ta.addEventListener("input", function () {
            grow(); if (S.inserting) return;
            S.text = ta.value; S.undo = null; S.warn = "";
            if (S.src === "draft") S.src = "yours";
            if (S.sugg && (S.sugg.status === "busy" || S.sugg.status === "ok")) { abort(); S.sugg = null; }
            sync();
        });
        tile.addEventListener("click", function (e) {
            var g = e.target.closest("[data-pd-gen]"); if (g) { request(); return; }
            var b = e.target.closest("[data-pd]"); if (!b) return;
            ({ use: use, more: more, dismiss: cancel, cancel: cancel, retry: request, undo: undo })[b.dataset.pd]();
        });
        grow(); sync();
        current = api;
        return api;
    }

    function unmount() { if (current) { current.destroy(); current = null; } }

    // Returns true when the key belongs to the assist (caller must stop handling it).
    function handleKey(e) {
        if (!current || !e.target || !e.target.closest) return false;
        var tile = e.target.closest(".is-editing");
        if (!tile || tile !== current.tile) return false;
        var s = current.state.sugg;
        if (e.altKey && e.code === "KeyG") { e.preventDefault(); current.request(); return true; }
        if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && s && s.status === "ok") { e.preventDefault(); current.use(); return true; }
        if (e.key === "Escape" && s) { e.preventDefault(); current.cancel(); return true; }
        return false;
    }

    var api = { MAX: MAX, NAME_MAX: NAME_MAX, esc: esc, modeOf: modeOf, canGen: canGen, clamp: clamp, classify: classify, labelFor: labelFor, titleFor: titleFor, hintFor: hintFor, fieldHtml: fieldHtml, mount: mount, unmount: unmount, handleKey: handleKey };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    root.PillarDescribe = api;
})(typeof window !== "undefined" ? window : this);
