/* Category registry (CT): page behaviour. Backend contract: analytics/categories.py, routes in app.py
   (GET/POST /api/categories, PUT/DELETE /api/categories/{id}?deck_id=, .../promote, .../members,
   PUT/DELETE /api/decks/{id}/category-targets/{core_id}, POST /api/categories/describe).
   The page does no counting: counts, targets, floors and statuses come from the server so Deep Review
   and this page agree. Everything model-returned or user-typed goes through esc() / textContent. */
(function () {
    "use strict";
    var $ = function (s, r) { return (r || document).querySelector(s); };
    var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
    var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };

    var TONE = { low: "danger", short: "signal", over: "neutral", ok: "ok" };
    var FLAGS = { game_changer: ["GC", "signal", "Game Changer"], stax: ["Stax", "neutral", "Stax"], mass_land_denial: ["MLD", "neutral", "Mass land denial"], extra_turns: ["XT", "neutral", "Extra turns"] };
    var S = { scope: "global", deck: "", decks: [], data: null, sel: null, q: "", F: null, ctl: null, ticket: 0, loadTicket: 0 };

    // ---- API --------------------------------------------------------------------------------
    function api(method, url, body) {
        return fetch(url, { method: method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined })
            .then(function (res) { return res.json().catch(function () { return {}; }).then(function (data) { return { ok: res.ok, status: res.status, data: data }; }); })
            .catch(function () { return { ok: false, status: 0, data: { error: "Network error. Check your connection and try again." } }; });
    }
    function q(params) { var p = Object.keys(params).filter(function (k) { return params[k]; }).map(function (k) { return k + "=" + encodeURIComponent(params[k]); }); return p.length ? "?" + p.join("&") : ""; }
    function deckParam() { return S.scope === "deck" ? S.deck : ""; }

    // ---- Data accessors ------------------------------------------------------------------------
    function cats() { return S.data ? S.data.categories : []; }
    function byId(id) { return cats().filter(function (c) { return c.id === id; })[0]; }
    function cores() { return cats().filter(function (c) { return c.scope === "core"; }); }
    function globals() { return cats().filter(function (c) { return c.scope === "global"; }); }
    function deckCats() { return cats().filter(function (c) { return c.scope === "deck"; }); }
    function deckInfo() { return (S.data && S.data.deck) || null; }
    function deckName() { var d = S.decks.filter(function (x) { return x.id === S.deck; })[0]; return d ? d.name : (deckInfo() ? deckInfo().name : ""); }
    function cap() { return S.data && S.data.caps ? S.data.caps.deck : 10; }
    function lim(k, d) { return S.data && S.data.limits ? S.data.limits[k] : d; }
    function full() { return S.scope === "deck" && deckCats().length >= cap(); }
    function matchQ(c) { return !S.q || (c.name + " " + (c.description || "")).toLowerCase().indexOf(S.q.toLowerCase()) > -1; }
    function dash(v) { return v == null ? "–" : v; }

    // ---- Alias check (client mirror of the server rule; the server stays authoritative) ---------
    function norm(s) { return s.trim().replace(/\s+/g, " ").toLowerCase().replace(/s$/, ""); }
    function aliasHit(name) {
        var n = norm(name || ""); if (!n || !S.data) return null;
        if ((S.data.group_aliases || []).indexOf(n) > -1) return { group: true };
        var al = S.data.aliases || {};
        for (var id in al) if (al[id].indexOf(n) > -1) return { id: id };
        return null;
    }

    // ---- Registry list ---------------------------------------------------------------------------
    function chip(t, tone, title) { return '<span class="hud-chip hud-chip--' + tone + '"' + (title ? ' title="' + esc(title) + '"' : "") + '>' + esc(t) + '</span>'; }
    function item(c) {
        var cur = S.sel === c.id, dk = S.scope === "deck", big, capt, chips = "", core = c.scope === "core";
        if (core) {
            if (dk) {
                big = c.shared ? "↑" : c.count; capt = c.shared ? "with Removal" : "tgt " + dash(c.target) + " · flr " + dash(c.floor);
                if (c.status) chips += chip(c.status, TONE[c.status] || "neutral");
                if (c.commander_does_this) chips += chip("★ Commander", "data", "Your commander does this job");
                if (c.gc) chips += chip(c.gc + " GC", "signal", c.gc + " Game Changers");
            } else { big = c.shared ? "↑" : dash(c.target); capt = c.shared ? "with Removal" : "floor " + dash(c.floor); }
        } else {
            big = dk ? (c.count == null ? 0 : c.count) : (c.decks || 0); capt = dk ? "cards" : "decks";
            if (c.scope === "deck") chips += chip("this deck", "data");
        }
        return '<li><button type="button" class="ct-item' + (core ? " is-dense" : "") + '" data-id="' + esc(c.id) + '" aria-current="' + cur + '">' +
            '<span class="ct-item-name"><span>' + esc(c.name) + '</span>' + chips + '</span>' +
            '<span class="ct-item-n">' + esc(big) + '<small>' + esc(capt) + '</small></span><p class="ct-item-desc">' + esc(c.description) + '</p></button></li>';
    }
    function grp(label, right, note) { return '<li class="ct-grp" role="presentation"><span>' + label + '</span>' + (note ? '<span class="ct-grp-note">' + esc(note) + '</span>' : "") + '<span class="ct-grp-n">' + right + '</span></li>'; }
    function themeSuggestions() {
        if (S.scope !== "deck") return "";
        var list = (S.data.suggestions || []); if (!list.length) return "";
        return grp("Suggested", list.length, "from this deck's themes") + '<li class="ct-sugg-row">' + list.map(function (t) {
            return '<button type="button" class="hud-btn hud-btn--sm ct-addtheme" data-name="' + esc(t.name) + '" data-rule="' + esc(t.rule) + '"' + (full() ? ' aria-disabled="true" title="Deck categories are capped at ' + cap() + '"' : "") + '>+ ' + esc(t.name) + '</button>';
        }).join("") + '<span class="ct-sugg-note">One click opens it pre-filled. Nothing is created until you Save.</span></li>';
    }
    function renderList() {
        if (!S.data) return;
        var dk = S.scope === "deck", h = "", count = 0;
        S.data.groups.forEach(function (g) {
            var rows = cores().filter(function (c) { return c.group === g.id && matchQ(c); }); if (!rows.length) return;
            count += rows.length;
            var right = g.id === "interaction" ? (dk && S.data.interaction_distinct != null ? "<b>" + S.data.interaction_distinct + "</b> distinct cards" : rows.length) : rows.length;
            h += grp(esc(g.label), right, g.note) + rows.map(item).join("");
        });
        var gl = globals().filter(matchQ); count += gl.length;
        h += grp("Custom", gl.length, "all decks") + (gl.length ? gl.map(item).join("") : '<li class="hud-empty">' + (S.q ? "No match." : "No custom categories yet.") + '</li>');
        if (dk) {
            var all = deckCats(), dc = all.filter(matchQ); count += dc.length;
            h += grp(esc(deckName()), all.length + "/" + cap(), "this deck only");
            h += dc.length ? dc.map(item).join("") : '<li class="ct-empty"><strong>' + (S.q ? "No match." : "No deck-specific categories yet.") + '</strong>' + (S.q ? "" : 'The 11 core categories already apply. Add one only for a role unique to this deck.<br><button type="button" class="hud-btn hud-btn--sm hud-btn--primary" data-act="new">New for this deck</button>') + '</li>';
            h += themeSuggestions();
        }
        $("#ctList").innerHTML = h; $("#ctCount").textContent = count + " categories";
        $("#ctVersion").textContent = "Registry v" + S.data.version;
        var d = deckInfo(), ncore = cores().length;
        $("#ctSum").innerHTML = dk && d ? (d.built ? "<b>" + d.cards + "</b> cards" : "<b>" + d.cards + "</b> cards · no statuses below 90") + " · <b>" + d.categories_used + "</b>/" + cap() + " deck categories" : "<b>" + ncore + "</b> core · <b>" + globals().length + "</b> custom";
        $("#ctNew").setAttribute("aria-disabled", String(full()));
        $("#ctNew").title = full() ? "This deck already has " + cap() + " categories" : "";
    }

    // ---- Editor ----------------------------------------------------------------------------------
    function guard() { return !S.F || !dirty() || confirm("Discard unsaved changes?"); }
    function open(id, pre, force) {
        if (!force && !guard()) return;
        abortAssist(); S.sel = id; S.ticket++;
        var c = id === "new" ? null : byId(id), core = c && c.scope === "core"; pre = pre || {};
        S.F = c ? { id: c.id, kind: c.scope, name: c.name, group: c.group || "", desc: c.description || "", rule: c.rule || "", pillar: c.pillar_id || "", tgt: core ? c.target : null, snap: "", sugg: null, src: null, undo: null }
                : { id: "new", kind: S.scope === "global" ? "global" : "deck", name: pre.name || "", group: "", desc: "", rule: pre.rule || "", pillar: "", tgt: null, snap: "", sugg: null, src: null, undo: null };
        S.F.snap = c ? snapOf() : JSON.stringify(["", "", "", "", null, ""]);
        $("#ctLayout").dataset.view = "detail";   /* before render: grow() needs a laid-out textarea */
        renderList(); renderForm();
        if (c) loadMembers();
        if (!c && pre.name) request();
    }
    function snapOf() { var F = S.F; return JSON.stringify([F.name, F.group, F.desc, F.rule, F.tgt, F.pillar]); }
    function dirty() { return !!S.F && S.F.snap !== snapOf(); }
    function mode() { return S.F.desc.trim() ? "improve" : "draft"; }
    function aliasErr() {
        var F = S.F; if (F.kind === "core") return null;
        var a = aliasHit(F.name); if (!a) return null;
        if (a.group) return { text: "That's the Interaction group (Removal, Counterspells, Board Wipes). Pick a narrower name, or open one of them.", id: "removal", label: "Open Removal" };
        var c = byId(a.id); return c ? { text: "That's the core category " + c.name + ". Use it instead.", id: a.id, label: "Open " + c.name } : null;
    }
    function canGen() { return !!(S.F.name.trim() || S.F.desc.trim()) && !aliasErr(); }
    function ctxLabel() { return S.F.kind === "deck" ? "this deck's commander, themes and cards" : "the core definitions"; }
    function flagBadge(f) { var m = FLAGS[f]; return m ? chip(m[0], m[1], m[2]) : ""; }
    function memberList(list, sugg) {
        if (!list.length) return "";
        return '<ul class="ct-members' + (sugg ? " is-sugg" : "") + '">' + list.map(function (m) {
            return '<li><span class="hud-card-name">' + esc(m.name) + '</span>' + (m.flags || []).map(flagBadge).join("") +
                (sugg ? '<button type="button" class="hud-btn hud-btn--sm" data-addm="' + esc(m.name) + '" aria-label="Add ' + esc(m.name) + ' to category">Add</button>' : "") + '</li>';
        }).join("") + '</ul>';
    }
    function ruleParts(rule) { var i = (rule || "").indexOf(":"); return i < 0 ? ["", ""] : [rule.slice(0, i), rule.slice(i + 1)]; }
    function ruleField() {
        var F = S.F, p = ruleParts(F.rule), k = p[0], v = p[1], themes = S.data.themes || [];
        var kinds = [["", "No rule"], ["autotag", "Autotag theme"], ["kindred", "Kindred type"], ["type", "Type line contains"]];
        var valHtml = k === "autotag" ? '<select id="ctRuleV" aria-label="Theme">' + themes.map(function (t) { return '<option' + (t === v ? " selected" : "") + '>' + esc(t) + '</option>'; }).join("") + '</select>' : k ? '<input id="ctRuleV" aria-label="Value" value="' + esc(v) + '" maxlength="' + lim("rule_value", 30) + '">' : "";
        return '<div class="hud-field" id="ctRuleF"><span class="hud-label">Suggestion rule</span><span class="hud-field-hint">Optional. A rule only fills the Suggested list, which you accept card by card. It never adds cards by itself.</span><div class="ct-rule"><select id="ctRuleK" aria-label="Rule kind">' + kinds.map(function (x) { return '<option value="' + x[0] + '"' + (x[0] === k ? " selected" : "") + '>' + x[1] + '</option>'; }).join("") + '</select>' + valHtml + '</div></div>';
    }
    function coreNote(c, dk) {
        if (c.target == null && c.floor == null && !c.shared) return "No published number for this one. The count is shown, nothing is judged.";
        var m = c.floor_note ? c.floor_note + " " : "";
        if (c.id === "ramp") m += "Commander MV ≥ 5 adds 2 to the floor. ";
        if (dk && c.commander_does_this) m += "Your commander does this job, which lowers the floor by 3. ";
        return m + "Targets: Command Zone template. Floors: auto-tag health.";
    }
    function renderForm() {
        var F = S.F, c = F.id === "new" ? null : byId(F.id), core = F.kind === "core", dk = S.scope === "deck", max = lim("description", 400);
        $("#ctTitle").textContent = c ? c.name : (S.scope === "global" ? "New global category" : "New category for " + deckName());
        var h = '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost ct-back" id="ctBack">← Categories</button>';
        if (core && c) {
            var gname = S.data.groups.filter(function (g) { return g.id === c.group; })[0].label;
            var editable = c.editable_target;
            h += '<div class="ct-note ct-note--core" role="note"><span aria-hidden="true">⌖</span><span>Core category. The definition is <strong>locked</strong>: auto-tag, health floors and the composition template depend on it.' + (dk ? "" : " Per-deck targets are set under Deck-specific.") + '</span></div>' +
                '<div class="hud-field"><span class="hud-label">Name</span><div class="ct-readonly">' + esc(c.name) + '<span class="ct-lock">⌖ locked</span></div></div>' +
                '<div class="ct-two"><div class="hud-field"><span class="hud-label">Group</span><div class="ct-readonly">' + esc(gname) + '</div></div><div class="hud-field"><span class="hud-label">Cards come from</span><div class="ct-readonly">' + esc(c.source) + '</div></div></div>' +
                '<div class="hud-field"><span class="hud-label">Definition</span><p class="ct-readonly ct-def">' + esc(c.description) + '</p></div>' +
                '<div class="ct-two"><div class="hud-field"><label class="hud-label" for="ctTgt">Target' + (dk ? " for this deck" : " (template)") + '</label>' + (editable ? '<input id="ctTgt" type="number" min="0" max="' + lim("target", 99) + '" value="' + esc(F.tgt) + '">' : '<div class="ct-readonly" id="ctTgt">' + (c.shared ? "with Removal" : dash(c.target)) + '</div>') + '</div>' +
                '<div class="hud-field"><span class="hud-label">Floor</span><div class="ct-readonly">' + (c.shared ? "with Removal" : dash(c.floor)) + '</div></div></div>' +
                '<p class="ct-msg">' + esc(coreNote(c, dk)) + '</p>';
            if (dk) h += '<div class="hud-field"><span class="hud-label" id="ctMemLabel">In this deck · ' + (c.shared ? "counted with Removal" : esc(c.count) + (c.gc ? " (" + c.gc + " GC)" : "")) + '</span>' + (c.commander_does_this ? '<p class="ct-msg">' + chip("★ Commander", "data", "Your commander") + ' Your commander does this job. It is not in the count.</p>' : "") + '<div id="ctMembers"></div></div>';
            else h += '<p class="ct-msg">Members are per deck. <a href="#" data-act="todeck">Switch to Deck-specific</a> to see one deck\'s cards and set its targets.</p>';
            h += '<p class="ct-msg" id="ctMsg" role="status"></p><div class="ct-actions">' + (editable ? '<button type="submit" class="hud-btn hud-btn--primary" id="ctSave">Save target</button><button type="button" class="hud-btn hud-btn--ghost" id="ctReset">Reset to ' + esc(c.default_target) + '</button><span class="ct-dirty" id="ctDirty" hidden>Unsaved</span>' : "") + '</div>';
        } else {
            var ge = '<div class="hud-field"><label class="hud-label" for="ctGroup">Group</label><select id="ctGroup"><option value="">Deck roles (none)</option>' + S.data.groups.map(function (g) { return '<option value="' + g.id + '"' + (F.group === g.id ? " selected" : "") + '>' + esc(g.label) + '</option>'; }).join("") + '</select></div>';
            var pillars = (deckInfo() && deckInfo().pillars) || [];
            var pe = F.kind === "deck" ? '<div class="hud-field"><label class="hud-label" for="ctPillar">Serves pillar</label><select id="ctPillar"><option value="">None</option>' + pillars.map(function (p) { return '<option value="' + esc(p.id) + '"' + (F.pillar === p.id ? " selected" : "") + '>' + esc(p.name) + '</option>'; }).join("") + '</select></div>' : "";
            h += '<div class="hud-field"><label class="hud-label" for="ctName">Name</label><input id="ctName" maxlength="' + lim("name", 32) + '" required aria-describedby="ctNameErr"><p class="ct-msg is-error" id="ctNameErr" role="alert"></p></div>' +
                '<div class="ct-two"><div class="hud-field"><span class="hud-label">Applies to</span><div class="ct-readonly">' + (F.kind === "deck" ? "Deck · " + esc(deckName()) : "Global · every deck") + '</div></div>' + ge + '</div>' + (pe ? '<div class="ct-two">' + pe + '<div></div></div>' : "") +
                '<div class="pd-f" id="pdF"><div class="pd-lab"><label class="hud-label" for="pd">Description</label><span class="pd-src" id="pdSrc" hidden></span><span class="pd-count" id="pdCount"></span><button class="hud-btn hud-btn--sm pd-gen" type="button" id="pdGen" aria-describedby="pdHint" aria-keyshortcuts="Alt+G"><span class="pd-gen-g" aria-hidden="true">◇</span><span id="pdGenL">Draft</span></button></div>' +
                '<textarea id="pd" rows="4" maxlength="' + max + '" aria-describedby="pdHint"></textarea><div class="pd-sugg" id="pdSugg" hidden></div><div class="pd-undo" id="pdUndo" hidden><span>Replaced your text.</span><button class="hud-btn hud-btn--sm hud-btn--ghost" type="button" id="pdUndoBtn">Undo</button></div>' +
                '<p class="pd-hint" id="pdHint">Start with "Cards that…", then say what does <em>not</em> count. Describe the job, not the power level. <span class="pd-hint-dyn" id="pdHint2"></span></p></div>' + '<div id="ctRuleBox">' + ruleField() + '</div>';
            if (c && dk) h += '<div class="hud-field"><span class="hud-label" id="ctMemLabel">Cards · ' + esc(c.count == null ? 0 : c.count) + '</span><div id="ctMembers"></div></div>';
            else if (c && !dk) h += '<p class="ct-msg">Members are per deck. Open a deck to see its cards.</p>';
            h += '<p class="ct-msg" id="ctMsg" role="status"></p><div class="ct-actions"><button type="submit" class="hud-btn hud-btn--primary" id="ctSave">Save</button><button type="button" class="hud-btn hud-btn--ghost" id="ctReset">Reset</button><span class="ct-dirty" id="ctDirty" hidden>Unsaved</span><span class="ct-spacer"></span>' +
                (F.kind === "deck" && c ? '<button type="button" class="hud-btn hud-btn--sm" id="ctPromote">Make global</button>' : "") + (c ? '<button type="button" class="hud-btn hud-btn--danger hud-btn--sm" id="ctDel">Delete</button>' : "") + '</div>';
        }
        $("#ctForm").innerHTML = h;
        if (!core) { $("#ctName").value = F.name; var ta = $("#pd"); ta.value = F.desc; grow(ta); }
        wire(); sync();
    }

    function loadMembers() {
        var F = S.F, box = $("#ctMembers"); if (!box || !F || F.id === "new") return;
        var ticket = S.ticket; box.innerHTML = '<p class="ct-msg">Loading cards…</p>';
        api("GET", "/api/categories/" + encodeURIComponent(F.id) + "/members" + q({ deck_id: S.deck })).then(function (r) {
            if (ticket !== S.ticket || !$("#ctMembers")) return;   // a newer selection superseded this response
            if (!r.ok) { $("#ctMembers").innerHTML = '<p class="ct-msg is-error">' + esc(r.data.error || "Could not load cards.") + '</p>'; return; }
            var m = r.data.members || [], sg = r.data.suggested || [], h = memberList(m, false);
            if (!m.length && !sg.length) h = '<p class="ct-msg">No cards yet.</p>';
            if (sg.length) h += '<p class="ct-sub">Suggested by rule <code>' + esc((byId(F.id) || {}).rule || "") + '</code> · accept one by one</p>' + memberList(sg, true);
            $("#ctMembers").innerHTML = h;
            $$("[data-addm]", $("#ctMembers")).forEach(function (b) { b.addEventListener("click", function () { addMember(b.dataset.addm); }); });
        });
    }
    function addMember(name) {
        var F = S.F; if (!F) return;
        api("POST", "/api/categories/" + encodeURIComponent(F.id) + "/members", { deck_id: S.deck, card: name }).then(function (r) {
            if (!r.ok) { msg(r.data.error || "Could not add the card.", "error"); return; }
            reload(F.id);
        });
    }

    // ---- Page state sync (no re-render per keystroke) ------------------------------------------------
    function sync() {
        var F = S.F; if (!F) return;
        var d = $("#ctDirty"); if (d) d.hidden = !dirty();
        $("#ctMeta").textContent = dirty() ? "unsaved" : (F.id === "new" ? "" : F.kind);
        if (F.kind === "core") return;
        var gen = $("#pdGen"), m = mode(), busy = F.sugg && F.sugg.status === "busy", al = aliasErr(), ok = canGen(), max = lim("description", 400);
        $("#pdGenL").textContent = busy ? (m === "improve" ? "Improving…" : "Drafting…") : (m === "improve" ? "Improve" : "Draft");
        gen.setAttribute("aria-busy", String(!!busy)); gen.setAttribute("aria-disabled", String(!ok));
        gen.title = al ? "That name is a core category" : !ok ? "Add a name or a few words first" : (m === "improve" ? "Rewrite my description (Alt+G)" : "Draft a membership test from the name and " + ctxLabel() + " (Alt+G)");
        var c = $("#pdCount"), len = F.desc.length; c.textContent = len + "/" + max; c.classList.toggle("is-over", len >= max);
        var src = $("#pdSrc"), f = $("#pdF"); src.hidden = !F.src; f.dataset.src = F.src || ""; src.dataset.src = F.src || "";
        src.textContent = F.src === "draft" ? "◇ Drafted" : F.src === "yours" ? "◆ Yours" : "";
        var h = $("#pdHint2"); h.className = "pd-hint-dyn";
        if (al) { h.className = "pd-hint-dyn is-warn"; h.textContent = "Drafting is off while the name is a core category."; }
        else if (busy) h.textContent = "Reading the name and " + ctxLabel() + ". Esc cancels, typing cancels.";
        else if (F.sugg && F.sugg.status === "ok") h.textContent = "Suggestion below. Nothing changes until you press Use.";
        else if (!ok) h.textContent = "Name it or jot a few words, then ◇ Draft.";
        else if (m === "improve") h.textContent = "◇ Improve rewrites your words into a membership test. Alt+G.";
        else h.textContent = "◇ Draft writes it from the name and " + ctxLabel() + ". Alt+G.";
        var ne = $("#ctNameErr");
        if (ne) { if (al) { ne.textContent = al.text + " "; var a = document.createElement("a"); a.href = "#"; a.dataset.open = al.id; a.textContent = al.label; ne.appendChild(a); } else ne.textContent = ne.dataset.manual || ""; }
        renderSugg(); $("#pdUndo").hidden = !F.undo;
    }

    function renderSugg() {
        var el = $("#pdSugg"), s = S.F.sugg;
        if (!s) { el.hidden = true; el.className = "pd-sugg"; el.innerHTML = ""; return; }
        el.hidden = false; var max = lim("description", 400);
        if (s.status === "busy") {
            el.className = "pd-sugg is-busy"; el.setAttribute("role", "status");
            el.innerHTML = '<span class="pd-sugg-g" aria-hidden="true">◇</span><div><span class="pd-skel"></span><span class="pd-skel"></span><p class="pd-sugg-meta">' + (s.mode === "improve" ? "Improving your description…" : "Drafting from the name and " + esc(ctxLabel()) + "…") + '</p></div><div class="pd-sugg-a"><button class="hud-btn hud-btn--sm hud-btn--ghost" type="button" data-pd="cancel">Cancel <span class="hud-kbd">Esc</span></button></div>';
        } else if (s.status === "dup") {
            var c = byId(s.dup); if (!c) { S.F.sugg = null; el.hidden = true; return; }
            el.className = "pd-sugg"; el.setAttribute("role", "status");
            el.innerHTML = '<span class="pd-sugg-g" aria-hidden="true">◇</span><div><p class="pd-sugg-t">This is the core category <strong>' + esc(c.name) + '</strong>. Use it instead?</p><p class="pd-sugg-meta">Its definition is fixed, so cards, counts and floors stay consistent. Your text is untouched.</p></div><div class="pd-sugg-a"><button class="hud-btn hud-btn--sm hud-btn--primary" type="button" data-pd="opendup" data-dup="' + esc(c.id) + '">Open ' + esc(c.name) + '</button><button class="hud-btn hud-btn--sm hud-btn--ghost pd-sugg-end" type="button" data-pd="dismiss">Keep mine <span class="hud-kbd">Esc</span></button></div>';
        } else if (s.status === "ok") {
            var pend = s.rule ? '<button type="button" class="hud-chip ct-tag-pending" data-pt="rule" aria-pressed="' + !s.off + '">+ rule ' + esc(s.rule) + '</button>' : "";
            el.className = "pd-sugg"; el.setAttribute("role", "status");
            el.innerHTML = '<span class="pd-sugg-g" aria-hidden="true">◇</span><div><p class="pd-sugg-t">' + esc(s.text) + '</p>' + (pend ? '<div class="pd-sugg-tags" aria-label="Suggested rule (press to leave it out)">' + pend + '</div>' : "") +
                (s.flagged ? '<p class="pd-sugg-meta is-warn">Dropped a rule that isn\'t allowed: ' + esc(s.flagged) + '.</p>' : "") +
                '<p class="pd-sugg-meta">' + s.text.length + "/" + max + ' · ' + (s.mode === "improve" ? "rewrite of your text" : "drafted from name + " + (S.F.kind === "deck" ? "deck context" : "core definitions")) + '</p></div>' +
                '<div class="pd-sugg-a"><button class="hud-btn hud-btn--sm hud-btn--primary pd-act-use" type="button" data-pd="use">' + (s.mode === "improve" ? "Replace" : "Use") + ' <span class="hud-kbd">⌘↵</span></button><button class="hud-btn hud-btn--sm pd-act-more" type="button" data-pd="more">Another</button><button class="hud-btn hud-btn--sm hud-btn--ghost pd-sugg-end" type="button" data-pd="dismiss" aria-label="Dismiss suggestion">✕ <span class="hud-kbd">Esc</span></button></div>';
        } else {
            el.className = "pd-sugg is-err"; el.setAttribute("role", "alert");
            el.innerHTML = '<span class="pd-sugg-g" aria-hidden="true">!</span><div><p class="pd-sugg-t">' + esc(s.msg) + '</p><p class="pd-sugg-meta">Your text is untouched.</p></div><div class="pd-sugg-a">' + (s.status === "na" ? "" : '<button class="hud-btn hud-btn--sm" type="button" data-pd="retry">Try again</button>') + '<button class="hud-btn hud-btn--sm hud-btn--ghost pd-sugg-end" type="button" data-pd="dismiss">Dismiss <span class="hud-kbd">Esc</span></button></div>';
        }
    }

    // ---- Assist (describe) ----------------------------------------------------------------------------------
    function abortAssist() { if (S.ctl) { S.ctl.aborted = true; S.ctl = null; } }
    function request(avoid) {
        var F = S.F;
        if (aliasErr()) { sync(); var n = $("#ctName"); n && n.focus(); return; }            /* blocked before any request */
        if (!canGen()) { var h = $("#pdHint2"); h.textContent = "Add a name or a few words first."; h.className = "pd-hint-dyn is-warn"; $("#ctName").focus(); return; }
        if (F.sugg && F.sugg.status === "busy") return;
        abortAssist();
        var m = mode(), ctl = { aborted: false }; S.ctl = ctl; F.sugg = { status: "busy", mode: m }; sync();
        var body = { name: F.name.trim(), description: F.desc.trim(), group: F.group, deck_id: F.kind === "deck" ? S.deck : undefined };
        if (avoid && avoid.length) body.avoid = avoid;
        api("POST", "/api/categories/describe", body).then(function (r) {
            if (ctl.aborted || S.F !== F) return;   // cancelled, typed over, or another category was opened
            S.ctl = null;
            var d = r.data || {};
            if (r.ok && d.duplicate_of && byId(d.duplicate_of)) F.sugg = { status: "dup", mode: m, dup: d.duplicate_of };
            else if (r.ok) F.sugg = { status: "ok", mode: m, text: d.description || "", rule: F.rule ? null : (d.rule || null), off: false, flagged: (d.flagged || []).map(function (x) { return x.reason; }).join("; ") };
            else if (d.error_kind === "alias") { F.sugg = null; sync(); return; }
            else if (r.status === 503 || d.error_kind === "no_llm") F.sugg = { status: "na", mode: m, msg: "Drafting is unavailable right now (no model configured). Write it yourself; everything else works." };
            else F.sugg = { status: "err", mode: m, msg: "Couldn't " + (m === "improve" ? "improve" : "draft") + " the description. " + (d.error || "The service answered with an error.") };
            sync();
        });
    }
    function cancel() { abortAssist(); S.F.sugg = null; sync(); var t = $("#pd"); t && t.focus(); }
    var inserting = false;
    function grow(ta) { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight + 2, 240) + "px"; }
    function replace(ta, text) { inserting = true; ta.focus(); ta.select(); if (!document.execCommand || !document.execCommand("insertText", false, text)) ta.value = text; inserting = false; grow(ta); }
    function use() {
        var F = S.F, s = F.sugg; if (!s || s.status !== "ok") return;
        var ta = $("#pd"), prev = F.desc;
        replace(ta, s.text);                      /* execCommand: native Ctrl+Z keeps working */
        F.desc = ta.value;
        if (s.rule && !s.off && !F.rule) { F.rule = s.rule; refreshRule(); }
        F.undo = prev.trim() ? prev : null; F.src = "draft"; F.sugg = null; sync(); ta.focus();
    }
    function undo() { var F = S.F; if (F.undo == null) return; replace($("#pd"), F.undo); F.desc = $("#pd").value; F.src = "yours"; F.undo = null; sync(); }

    // ---- Save / delete / promote / targets --------------------------------------------------------------------
    function msg(t, tone) { var m = $("#ctMsg"); if (m) { m.textContent = t || ""; m.className = "ct-msg" + (tone ? " is-" + tone : ""); } }
    function nameErr() {
        var F = S.F, n = F.name.trim(); if (!n) return "Give it a name.";
        if (aliasErr()) return "alias";
        var lc = n.toLowerCase().replace(/\s+/g, " ");
        var pool = (F.kind === "global" ? globals() : globals().concat(deckCats()));
        var clash = pool.filter(function (c) { return c.id !== F.id && c.name.toLowerCase() === lc; })[0];
        return clash ? "A category with that name already exists" + (clash.scope === "deck" ? " in this deck." : ".") : "";
    }
    function reload(selectId) {
        var ticket = ++S.loadTicket, ids = selectId || S.sel;
        return api("GET", "/api/categories" + q({ deck_id: deckParam() })).then(function (r) {
            if (ticket !== S.loadTicket) return;
            if (!r.ok) { $("#ctPageMsg").textContent = r.data.error || "Could not load categories."; $("#ctPageMsg").className = "ct-msg is-error"; return; }
            $("#ctPageMsg").textContent = "";
            S.data = r.data; renderList();
            if (ids === "new" || !byId(ids)) ids = byId("ramp") ? "ramp" : (cats()[0] || {}).id;
            open(ids, null, true);
        });
    }
    function save(ev) {
        ev && ev.preventDefault();
        var F = S.F, c = F.id === "new" ? null : byId(F.id), btn = $("#ctSave");
        if (F.kind === "core") {
            var t = $("#ctTgt"); if (!t || t.tagName !== "INPUT") return;
            var v = t.value.trim() === "" ? NaN : Number(t.value);
            if (!isFinite(v) || v < 0 || v !== Math.floor(v)) { msg("Target is a whole number.", "error"); return; }
            btn.disabled = true; msg("Saving…");
            api("PUT", "/api/decks/" + encodeURIComponent(S.deck) + "/category-targets/" + encodeURIComponent(F.id), { target: v }).then(function (r) {
                if (!r.ok) { btn.disabled = false; msg(r.data.error || "Save failed.", "error"); return; }
                reload(F.id).then(function () { msg("Target saved for " + deckName() + ".", "ok"); });
            });
            return;
        }
        var e = nameErr(), ne = $("#ctNameErr");
        if (e) { if (e !== "alias") { ne.dataset.manual = e; ne.textContent = e; } $("#ctName").focus(); return; }
        if (!c && full()) { msg("This deck already has " + cap() + " categories. Past that they stop being roles.", "error"); return; }
        var body = { name: F.name.trim(), description: F.desc.trim(), group: F.group, rule: F.rule, pillar_id: F.kind === "deck" ? F.pillar : "" };
        btn.disabled = true; msg("Saving…");
        var req = c ? api("PUT", "/api/categories/" + encodeURIComponent(c.id) + q({ deck_id: c.scope === "deck" ? S.deck : "" }), body)
                    : api("POST", "/api/categories", Object.assign({ scope: F.kind, deck_id: F.kind === "deck" ? S.deck : undefined }, body));
        req.then(function (r) {
            btn.disabled = false;
            if (!r.ok) {
                if (r.data.error_kind === "alias") { sync(); return; }
                if (r.status === 409 && r.data.error_kind === "duplicate") { ne.dataset.manual = r.data.error; ne.textContent = r.data.error; $("#ctName").focus(); return; }
                msg(r.data.error || "Save failed.", "error"); return;
            }
            S.F.snap = snapOf();   // saved: leaving no longer asks
            reload(r.data.category.id).then(function () { msg("Saved.", "ok"); });
        });
    }
    function del() {
        var F = S.F, c = byId(F.id); if (!c || !confirm("Delete this category? Cards keep their other categories.")) return;
        api("DELETE", "/api/categories/" + encodeURIComponent(c.id) + q({ deck_id: c.scope === "deck" ? S.deck : "" })).then(function (r) {
            if (!r.ok) { msg(r.data.error || "Delete failed.", "error"); return; }
            S.F = null; reload("ramp").then(function () { $("#ctLayout").dataset.view = "list"; });
        });
    }
    function promote() {
        var F = S.F, c = byId(F.id); if (!c) return;
        if (dirty() && !confirm("Make global saves the stored version, not your unsaved edits. Continue?")) return;
        api("POST", "/api/categories/" + encodeURIComponent(c.id) + "/promote" + q({ deck_id: S.deck })).then(function (r) {
            if (!r.ok) { msg(r.data.error || "Could not make it global.", "error"); return; }
            S.F = null; setScope("global", r.data.category.id, true).then(function () { msg("Moved to Global. Every deck can use it now.", "ok"); });
        });
    }
    function resetTarget() {
        var F = S.F;
        api("DELETE", "/api/decks/" + encodeURIComponent(S.deck) + "/category-targets/" + encodeURIComponent(F.id)).then(function (r) {
            if (!r.ok) { msg(r.data.error || "Reset failed.", "error"); return; }
            reload(F.id).then(function () { msg("Back to the template target.", "ok"); });
        });
    }

    function refreshRule() {
        var box = $("#ctRuleBox"); if (!box) return; box.innerHTML = ruleField(); bindRule();
    }
    function bindRule() {
        var F = S.F, k = $("#ctRuleK"); if (!k) return;
        k.addEventListener("change", function () { F.rule = k.value ? k.value + ":" + (k.value === "autotag" ? (S.data.themes || [""])[0] : "") : ""; refreshRule(); sync(); });
        var v = $("#ctRuleV"); if (v) { var hh = function () { F.rule = k.value + ":" + v.value; sync(); }; v.addEventListener("input", hh); v.addEventListener("change", hh); }
    }
    function wire() {
        var F = S.F, form = $("#ctForm");
        $("#ctBack").addEventListener("click", function () { if (guard()) { S.F.snap = snapOf(); $("#ctLayout").dataset.view = "list"; } });
        form.addEventListener("submit", save);
        var rs = $("#ctReset"); if (rs) rs.addEventListener("click", function () { if (F.kind === "core") resetTarget(); else open(F.id, null, true); });
        if (F.kind === "core") { var t = $("#ctTgt"); if (t && t.tagName === "INPUT") t.addEventListener("input", function () { F.tgt = t.value.trim() === "" ? null : Number(t.value); sync(); }); return; }
        var ta = $("#pd"), nm = $("#ctName");
        nm.addEventListener("input", function () { F.name = nm.value; $("#ctNameErr").dataset.manual = ""; if (F.sugg && F.sugg.status === "busy") { abortAssist(); F.sugg = null; } sync(); });
        ta.addEventListener("input", function () { grow(ta); if (inserting) return; F.desc = ta.value; F.undo = null; if (F.src === "draft") F.src = "yours"; if (F.sugg && /busy|ok|dup/.test(F.sugg.status)) { abortAssist(); F.sugg = null; } sync(); });
        $("#ctGroup").addEventListener("change", function () { F.group = this.value; sync(); });
        var pl = $("#ctPillar"); if (pl) pl.addEventListener("change", function () { F.pillar = this.value; sync(); });
        bindRule();
        $("#pdGen").addEventListener("click", function () { request(); });
        $("#pdUndoBtn").addEventListener("click", undo);
        $("#pdSugg").addEventListener("click", function (e) {
            if (e.target.closest("[data-pt]")) { F.sugg.off = !F.sugg.off; renderSugg(); return; }
            var b = e.target.closest("[data-pd]"); if (!b) return;
            if (b.dataset.pd === "opendup") { open(b.dataset.dup, null, true); return; }
            var prev = F.sugg && F.sugg.text;
            ({ use: use, more: function () { F.sugg = null; request(prev ? [prev] : null); }, dismiss: cancel, cancel: cancel, retry: function () { request(); } })[b.dataset.pd]();
        });
        var d = $("#ctDel"); if (d) d.addEventListener("click", del);
        var pr = $("#ctPromote"); if (pr) pr.addEventListener("click", promote);
    }
    function showNone() {
        $("#ctTitle").textContent = "Category"; $("#ctMeta").textContent = "";
        $("#ctForm").innerHTML = '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost ct-back" id="ctBack">← Categories</button><div class="ct-empty"><strong>Nothing selected</strong>Pick a category from the registry, or add a new one.</div>';
        $("#ctBack").addEventListener("click", function () { $("#ctLayout").dataset.view = "list"; });
    }

    // ---- Page chrome -------------------------------------------------------------------------------------------
    function setScope(scope, selectId, force) {
        if (!force && !guard()) { $("#" + (S.scope === "deck" ? "scD" : "scG")).checked = true; return Promise.resolve(); }
        S.scope = scope; S.F = null; S.q = ""; $("#ctQ").value = "";
        $(scope === "deck" ? "#scD" : "#scG").checked = true;
        $("#ctDeckF").hidden = scope !== "deck";
        if (scope === "deck" && !S.deck) { $("#ctList").innerHTML = '<li class="hud-empty">No decks to show.</li>'; showNone(); return Promise.resolve(); }
        var url = new URL(location.href); if (scope === "deck") url.searchParams.set("deck", S.deck); else url.searchParams.delete("deck");
        history.replaceState(null, "", url);
        return reload(selectId || "ramp").then(function () { $("#ctLayout").dataset.view = selectId ? "detail" : "list"; });
    }
    document.addEventListener("click", function (e) {
        var it = e.target.closest(".ct-item"); if (it) { open(it.dataset.id); return; }
        var th = e.target.closest(".ct-addtheme");
        if (th) { if (th.getAttribute("aria-disabled") === "true") return; open("new", { name: th.dataset.name, rule: th.dataset.rule }); return; }
        var o = e.target.closest("[data-open]"); if (o) { e.preventDefault(); open(o.dataset.open, null, true); return; }
        var a = e.target.closest("[data-act]");
        if (a) { e.preventDefault(); if (a.dataset.act === "new") open("new"); if (a.dataset.act === "todeck") { var id = S.sel; setScope("deck", id).then(function () {}); } }
    });
    $("#ctNew").addEventListener("click", function () { if (full()) return; open("new"); var n = $("#ctName"); n && n.focus(); });
    $$("input[name=scope]").forEach(function (rd) { rd.addEventListener("change", function () { setScope(rd.value); }); });
    $("#ctDeck").addEventListener("change", function () {
        var prev = S.deck; if (!guard()) { this.value = prev; return; }
        S.deck = this.value; setScope("deck", null, true);
    });
    $("#ctQ").addEventListener("input", function () { S.q = this.value; renderList(); });
    window.addEventListener("beforeunload", function (e) { if (dirty()) { e.preventDefault(); e.returnValue = ""; } });

    document.addEventListener("keydown", function (e) {
        var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName), F = S.F, inForm = e.target.closest && e.target.closest("#ctForm");
        if (F && inForm) {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); if (F.sugg && F.sugg.status === "ok") use(); else save(); return; }
            if (F.kind !== "core") {
                if (e.altKey && e.code === "KeyG") { e.preventDefault(); request(); return; }
                if (e.key === "Escape" && F.sugg) { e.preventDefault(); cancel(); return; }
            }
        }
        if (typing) { if (e.key === "Escape" && e.target.id === "ctQ") { e.target.value = ""; S.q = ""; renderList(); e.target.blur(); } return; }
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        if (e.key === "n") { e.preventDefault(); $("#ctNew").click(); }
        else if (e.key === "/") { e.preventDefault(); $("#ctQ").focus(); }
        else if (e.key === "j" || e.key === "k") {
            var ids = $$(".ct-item").map(function (b) { return b.dataset.id; }), i = ids.indexOf(S.sel), nx = ids[Math.max(0, Math.min(ids.length - 1, i + (e.key === "j" ? 1 : -1)))];
            if (nx) { open(nx); var b = $('.ct-item[data-id="' + nx + '"]'); b && b.focus(); }
        }
    });

    // ---- Boot ----------------------------------------------------------------------------------------------------
    function boot() {
        var want = new URLSearchParams(location.search).get("deck");
        api("GET", "/api/decks").then(function (r) {
            var list = (r.data && r.data.decks) || [];
            S.decks = list.map(function (d) { return { id: d.registry_id || d.id, name: d.name || String(d.id) }; }).filter(function (d) { return d.id != null; })
                .sort(function (a, b) { return a.name.localeCompare(b.name); });
            var sel = $("#ctDeck"); sel.replaceChildren();
            S.decks.forEach(function (d) { var o = document.createElement("option"); o.value = d.id; o.textContent = d.name; sel.appendChild(o); });
            if (want && S.decks.some(function (d) { return d.id === want; })) S.deck = want; else if (S.decks.length) S.deck = S.decks[0].id;
            sel.value = S.deck;
            return want && S.deck === want ? setScope("deck", null, true) : setScope("global", null, true);
        }).then(function () { if (!window.matchMedia("(max-width:900px)").matches) $("#ctLayout").dataset.view = "detail"; });
    }
    boot();
}());
