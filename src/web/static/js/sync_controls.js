/* ==========================================================================
   Commander Lab: explicit Update / Push controls (replaces "Sync to External").
   Spec: docs/design/sync_controls/SPEC.md. API: docs/SYNC_UPDATE_PUSH.md.

     Update = external -> commander.lab (overwrites the commander.lab deck)
     Push   = commander.lab -> external (overwrites the external deck)

   Nothing here runs on load, on a timer or on a poll except the read-only
   GET /sync/status. Every write goes: click -> dry_run preview -> confirm
   dialog naming the deck that will be overwritten -> real POST.
   Pure string builders + a small controller; exposes window.SyncControls.
   ========================================================================== */
(function () {
    "use strict";
    var NAMES = { archidekt: "Archidekt", moxfield: "Moxfield", commandersalt: "Commander Salt" };
    var ICON = {
        update: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>',
        push: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="17 8 12 3 7 8"></polyline><line x1="12" y1="3" x2="12" y2="15"></line></svg>',
        spin: '<svg class="sync-spin" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 12a9 9 0 1 1-6.2-8.6"></path></svg>',
        ok: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="20 6 9 17 4 12"></polyline></svg>',
        fail: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>'
    };

    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }
    function label(p) { return NAMES[p] || p; }

    // "2 hours ago" / "Never". `now` is injectable for tests.
    function ago(iso, now) {
        if (!iso) return "never";
        var t = Date.parse(iso);
        if (isNaN(t)) return "never";
        var s = Math.max(0, Math.round(((now == null ? Date.now() : now) - t) / 1000));
        if (s < 45) return "just now";
        var m = Math.round(s / 60);
        if (m < 60) return m + (m === 1 ? " minute ago" : " minutes ago");
        var h = Math.round(m / 60);
        if (h < 48) return h + (h === 1 ? " hour ago" : " hours ago");
        var d = Math.round(h / 24);
        return d + " days ago";
    }

    // GET /sync/status -> { state: "none" | "ready", providers: [...] }.
    // Only update/push-capable providers (archidekt, moxfield) get controls;
    // a link-only provider (commandersalt) never does.
    function viewModel(status) {
        var list = ((status && status.providers) || []).filter(function (p) {
            return p.linked && (p.provider === "archidekt" || p.provider === "moxfield");
        });
        return {
            state: list.length ? "ready" : "none",
            providers: list.map(function (p) {
                return {
                    provider: p.provider, name: label(p.provider), identifier: p.identifier,
                    canUpdate: p.can_update !== false,
                    canPush: !!p.can_push,
                    pushReason: p.push_unavailable_reason || (label(p.provider) + " API does not support pushing changes."),
                    lastUpdated: p.last_updated_at || null, lastPushed: p.last_pushed_at || null
                };
            })
        };
    }

    function tooltip(op, name) {
        return op === "update"
            ? "Pull the latest changes from " + name + ". This overwrites your local commander.lab deck."
            : "Send your current commander.lab deck to " + name + ". This overwrites the external deck.";
    }

    // One button. phase: idle | busy | done | failed.
    function buttonHtml(op, p, phase) {
        var name = p.name, text, icon, cls = "hud-btn hud-btn--sm sync-btn sync-btn--" + op, disabled = false, title;
        var busy = phase === "busy", done = phase === "done", failed = phase === "failed";
        if (op === "update") {
            text = busy ? "Updating\u2026" : done ? "Updated!" : failed ? "Update failed" : "Update from " + name;
        } else {
            text = busy ? "Pushing\u2026" : done ? "Pushed!" : failed ? "Push failed" : "Push to " + name;
        }
        icon = busy ? ICON.spin : done ? ICON.ok : failed ? ICON.fail : ICON[op];
        title = tooltip(op, name);
        if (done) cls += " sync-btn--done";
        if (failed) cls += " sync-btn--failed";
        if (busy) disabled = true;
        if (op === "push" && !p.canPush) { disabled = true; title = p.pushReason; }
        if (op === "update" && !p.canUpdate) { disabled = true; title = name + " cannot be updated from."; }
        return '<button type="button" class="' + cls + '" data-sync-op="' + op + '" data-sync-provider="' + esc(p.provider) +
            '"' + (disabled ? " disabled" : "") + ' title="' + esc(title) + '">' + icon + " <span>" + esc(text) + "</span></button>";
    }

    function metaHtml(p, now) {
        return '<div class="sync-meta hud-muted" data-sync-meta="' + esc(p.provider) + '">Last updated: ' + esc(ago(p.lastUpdated, now)) +
            " \u00b7 Last pushed: " + esc(ago(p.lastPushed, now)) + "</div>";
    }

    // phases: { "update:archidekt": "busy", ... }
    function panelHtml(vm, phases, now) {
        phases = phases || {};
        if (vm.state === "none") {
            return '<div class="sync-empty hud-muted">No external deck linked.</div>' +
                '<a class="hud-btn hud-btn--sm" data-sync-link href="/decks" title="Link an Archidekt or Moxfield deck">Link External Deck</a>';
        }
        return vm.providers.map(function (p) {
            return '<div class="sync-group" data-sync-group="' + esc(p.provider) + '">' +
                '<div class="sync-group-name">' + esc(p.name) + (p.identifier ? ' <span class="hud-muted">#' + esc(p.identifier) + "</span>" : "") + "</div>" +
                '<div class="sync-row">' + buttonHtml("update", p, phases["update:" + p.provider]) +
                buttonHtml("push", p, phases["push:" + p.provider]) + "</div>" +
                (p.canPush ? "" : '<div class="sync-note hud-muted">' + esc(p.pushReason) + "</div>") +
                metaHtml(p, now) + "</div>";
        }).join("");
    }

    function diffHtml(changes) {
        if (!changes) return "";
        var lines = [];
        (changes.added || []).forEach(function (c) { lines.push('<div class="diff-add">+' + esc(c.quantity || 1) + " " + esc(c.name) + "</div>"); });
        (changes.removed || []).forEach(function (c) { lines.push('<div class="diff-sub">\u2212' + esc(c.quantity || 1) + " " + esc(c.name) + "</div>"); });
        (changes.changed || []).forEach(function (c) { lines.push("<div>~ " + esc(c.name) + " " + esc(c.from) + " \u2192 " + esc(c.to) + "</div>"); });
        return '<div class="diff-summary" data-sync-diff>' + (lines.length ? lines.join("") : '<span class="hud-muted">No card changes.</span>') + "</div>";
    }

    // Confirmation dialog body. deckName = the commander.lab deck's name.
    function confirmHtml(op, name, deckName, changes) {
        var d = esc(deckName || "this deck");
        if (op === "update") {
            return '<h3 class="hud-panel-title" id="syncConfirmTitle">Overwrite local deck?</h3>' +
                '<p>This pulls the latest list from <b>' + esc(name) + "</b> into the commander.lab deck <b>" + d +
                "</b>. Local changes you haven't pushed will be lost. " + esc(name) + " is not modified.</p>" + diffHtml(changes);
        }
        return '<h3 class="hud-panel-title" id="syncConfirmTitle">Overwrite ' + esc(name) + " deck?</h3>" +
            "<p>This replaces the list on <b>" + esc(name) + "</b> with the commander.lab deck <b>" + d +
            "</b>. Changes made directly on " + esc(name) + " since the last sync will be lost. commander.lab is not modified.</p>" + diffHtml(changes);
    }

    function confirmLabel(op, name) { return (op === "update" ? "Update from " : "Push to ") + name; }

    function errorMessage(op, name, data, status) {
        var msg = (data && data.error) || ("HTTP " + status);
        return (op === "update" ? "Update from " : "Push to ") + name + " failed: " + msg;
    }

    // ---- controller ------------------------------------------------------
    // opts: { root, modal, deckName, registryId, fetch, now, flashMs, onToast, onChanged }
    // modal: element with [data-sync-confirm-body] and [data-sync-confirm-go].
    function mount(opts) {
        var root = opts.root, modal = opts.modal, f = opts.fetch || window.fetch.bind(window);
        var base = "/api/decks/" + encodeURIComponent(opts.registryId) + "/sync/";
        var vm = { state: "none", providers: [] }, phases = {}, pending = null, errorText = "";
        function nowv() { return opts.now ? opts.now() : Date.now(); }

        function render() {
            root.innerHTML = panelHtml(vm, phases, nowv()) +
                (errorText ? '<div class="sync-error" role="alert" data-sync-error>' + esc(errorText) + "</div>" : "");
        }
        async function call(method, path, body) {
            var res = await f(base + path, method === "GET" ? { method: "GET" } : {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
            var data = null;
            try { data = await res.json(); } catch (e) { data = null; }
            return { ok: res.ok && data && data.success !== false, status: res.status, data: data };
        }
        async function refresh() {
            try {
                var r = await call("GET", "status");
                if (r.ok) vm = viewModel(r.data);
                else errorText = "Could not load sync status.";
            } catch (e) { errorText = "Could not load sync status."; }
            render();
        }
        function setPhase(key, phase) {
            if (phase) phases[key] = phase; else delete phases[key];
            render();
        }
        function closeModal() {
            pending = null;
            if (opts.closeModal) opts.closeModal(modal);
        }
        // Step 1: click -> read-only dry run -> confirmation dialog.
        async function request(op, provider) {
            var key = op + ":" + provider, name = label(provider);
            errorText = "";
            setPhase(key, "busy");
            var r;
            try { r = await call("POST", op === "update" ? "pull" : "push", { provider: provider, dry_run: true }); }
            catch (e) { r = { ok: false, status: 0, data: { error: String(e) } }; }
            if (!r.ok) {
                errorText = errorMessage(op, name, r.data, r.status);
                setPhase(key, "failed");
                flash(key);
                return;
            }
            setPhase(key, null);
            pending = { op: op, provider: provider };
            modal.querySelector("[data-sync-confirm-body]").innerHTML = confirmHtml(op, name, opts.deckName, r.data.changes);
            var go = modal.querySelector("[data-sync-confirm-go]");
            go.textContent = confirmLabel(op, name);
            go.className = "hud-btn hud-btn--sm sync-btn sync-btn--" + op;
            if (opts.openModal) opts.openModal(modal);
        }
        // Step 2: explicit confirmation -> the real write.
        async function confirm() {
            if (!pending) return;
            var op = pending.op, provider = pending.provider, key = op + ":" + provider, name = label(provider);
            closeModal();
            errorText = "";
            setPhase(key, "busy");
            var r;
            try { r = await call("POST", op === "update" ? "pull" : "push", { provider: provider, dry_run: false }); }
            catch (e) { r = { ok: false, status: 0, data: { error: String(e) } }; }
            if (r.ok) {
                setPhase(key, "done");
                if (opts.onToast) opts.onToast(op === "update" ? "Updated from " + name : "Pushed to " + name, "ok");
                await refresh();
                if (op === "update" && opts.onChanged) opts.onChanged(r.data);
            } else {
                errorText = errorMessage(op, name, r.data, r.status);
                setPhase(key, "failed");
                if (opts.onToast) opts.onToast(errorText, "error");
            }
            flash(key);
        }
        function flash(key) {
            var ms = opts.flashMs == null ? 2000 : opts.flashMs;
            var t = setTimeout(function () { if (phases[key] === "done" || phases[key] === "failed") setPhase(key, null); }, ms);
            if (t && t.unref) t.unref();
        }

        root.addEventListener("click", function (ev) {
            var b = ev.target.closest && ev.target.closest("[data-sync-op]");
            if (!b || b.disabled) return;
            request(b.getAttribute("data-sync-op"), b.getAttribute("data-sync-provider"));
        });
        var go = modal.querySelector("[data-sync-confirm-go]");
        go.addEventListener("click", confirm);
        modal.addEventListener("click", function (ev) {
            if (ev.target === modal || (ev.target.closest && ev.target.closest("[data-hud-close]"))) pending = null;
        });
        render();
        return { refresh: refresh, request: request, confirm: confirm, state: function () { return { vm: vm, phases: phases, pending: pending, errorText: errorText }; } };
    }

    window.SyncControls = {
        ago: ago, viewModel: viewModel, buttonHtml: buttonHtml, panelHtml: panelHtml,
        confirmHtml: confirmHtml, confirmLabel: confirmLabel, diffHtml: diffHtml, errorMessage: errorMessage, mount: mount
    };
})();
