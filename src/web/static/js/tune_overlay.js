/*!
 * tune_overlay.js — Deck Tuner overlay shell (DT-1, docs/architecture/deck-tuner-overlay-spec.md §2).
 *
 * A deckbuilding view layered over the decklist on /deck/{name}/tune, toggled
 * by the Tuner/Decklist segmented control in the header (deep-link: ?view=overlay).
 *
 *   - Columns: one per card tag (deck_card_tags, reused live — NOT a forked
 *     store) plus structural slot columns (Commander, Maybeboard, Considering)
 *     and an Uncategorized pool.
 *   - Drag a chip between tag columns = POST + DELETE /card-tags (spec §2).
 *   - Drag onto a slot column changes board membership (draft-only).
 *   - [+ Add card] per column: card_meta autocomplete, card lands in the
 *     paper draft and gets the column's tag. [x] removes it from the draft.
 *   - ⚑ on a tag column header toggles "not in decklist" (tuneup:tag_flags);
 *     cards whose every tag is excluded drop out of the deck count (§3.2).
 *   - Draft saves are paper-only (POST /tuneup/draft): no deck or instance
 *     writes. The apply path is DT-2; the Save deck button stays disabled.
 *
 * No dependencies: native HTML5 drag & drop + window.HUD string builders.
 */
(function () {
    "use strict";

    var cfg = null, API = "";
    var S = null;                 // last server state (GET /tuneup/draft)
    var loaded = false;
    var extraCols = [];           // client-added empty tag columns (the server only lists tags in use)
    var dirty = false, saving = false;
    var saveTimer = null;
    var dragPayload = null;
    // DT-3 printing picker state (spec §3.1/§3.3): one shared dropdown.
    var pp = { box: null, card: null };

    // Scoped picker styles (hud.css is DT-1's file; DT-7 set the precedent
    // of injecting its own scoped CSS instead).
    var PP_CSS =
        ".tnov-col-body{position:relative}" +
        ".tnov-print{cursor:pointer;border:var(--hairline) solid var(--line);background:transparent;" +
        "color:var(--text-3);font-family:var(--font-mono);font-size:var(--fs-micro);" +
        "padding:0 var(--sp-1);border-radius:var(--r-sm);margin-left:var(--sp-1)}" +
        ".tnov-print:hover{color:var(--text-1);border-color:var(--text-3)}" +
        ".tnov-need{margin-left:var(--sp-1);color:var(--warn,#e0a458);font-family:var(--font-mono);" +
        "font-size:var(--fs-micro);text-transform:uppercase;letter-spacing:.04em}" +
        ".tnov-pp{position:absolute;z-index:60;min-width:260px;max-width:340px;max-height:320px;overflow:auto;" +
        "background:var(--bg-2);border:var(--hairline) solid var(--line);border-radius:var(--r-md);" +
        "box-shadow:0 8px 24px rgba(0,0,0,.45)}" +
        ".tnov-pp-opt{display:flex;gap:var(--sp-2);align-items:center;padding:var(--sp-2);" +
        "cursor:pointer;border-bottom:var(--hairline) solid var(--line)}" +
        ".tnov-pp-opt:hover{background:var(--bg-3)}" +
        ".tnov-pp-opt img{width:34px;height:auto;border-radius:2px;flex:none}" +
        ".tnov-pp-main{flex:1;min-width:0}" +
        ".tnov-pp-set{color:var(--text-1);font-size:var(--fs-xs);white-space:nowrap;overflow:hidden;" +
        "text-overflow:ellipsis}" +
        ".tnov-pp-sub{color:var(--text-3);font-family:var(--font-mono);font-size:var(--fs-micro)}" +
        ".tnov-pp-own{flex:none;font-family:var(--font-mono);font-size:var(--fs-micro);" +
        "text-transform:uppercase;letter-spacing:.04em}" +
        ".tnov-pp-own.is-owned{color:var(--ok,#7fc98f)}" +
        ".tnov-pp-own.is-buy{color:var(--warn,#e0a458)}" +
        ".tnov-pp-status{padding:var(--sp-3);color:var(--text-3);font-size:var(--fs-xs)}";
    function injectPpCss() {
        if (document.getElementById("tnov-pp-css")) return;
        var el = document.createElement("style");
        el.id = "tnov-pp-css";
        el.textContent = PP_CSS;
        document.head.appendChild(el);
    }

    function $(id) { return document.getElementById(id); }
    function esc(s) { return window.HUD.esc(s); }

    function send(method, path, body) {
        return fetch(API + path, { method: method, headers: { "Content-Type": "application/json" },
                                   body: body === undefined ? undefined : JSON.stringify(body) })
            .then(function (r) { return r.json().then(function (j) {
                if (!r.ok || j.success === false) throw new Error(j.error || ("HTTP " + r.status));
                return j; }); });
    }

    // ---- state helpers ------------------------------------------------------
    function parseCol(id) {
        if (id === "uncategorized") return { kind: "pool", key: "" };
        var i = id.indexOf(":");
        return { kind: id.slice(0, i), key: id.slice(i + 1) };
    }
    function cardByName(name) {
        return S.cards.filter(function (c) { return c.name === name; })[0] || null;
    }
    function tagsOf(name) {
        return (S.card_tags[name] || []).map(function (e) { return e.tag; });
    }
    function pushTag(name, tag, source) {
        var list = S.card_tags[name] = S.card_tags[name] || [];
        if (!list.some(function (e) { return e.tag === tag; })) list.push({ tag: tag, source: source || "user" });
    }
    function pullTag(name, tag) {
        var list = (S.card_tags[name] || []).filter(function (e) { return e.tag !== tag; });
        if (list.length) S.card_tags[name] = list; else delete S.card_tags[name];
    }
    function flagOff(tag) {
        var f = S.tag_flags[tag];
        return !!(f && f.in_decklist === false);
    }
    function slotOf(c) { return c.slot || "mainboard"; }
    function excludedCard(c) {
        // Spec §3.2: dropped only when EVERY tag it carries is flagged off.
        var tags = tagsOf(c.name);
        if (slotOf(c) === "commander") tags = tags.concat(["Commander"]);
        return tags.length > 0 && tags.every(flagOff);
    }
    function counts() {
        var total = 0, dropped = 0;
        S.cards.forEach(function (c) {
            if (slotOf(c) === "maybeboard" || slotOf(c) === "considering") return;
            if (excludedCard(c)) dropped += 1; else total += (c.qty || 1);
        });
        return { total: total, dropped: dropped };
    }
    function allColumns() {
        var cols = (S.columns || []).slice();
        var unc = cols.filter(function (c) { return c.id === "uncategorized"; })[0];
        cols = cols.filter(function (c) { return c.id !== "uncategorized"; });
        extraCols.forEach(function (x) {
            if (!cols.some(function (c) { return c.id === x.id; })) cols.push(x);
        });
        if (unc) cols.push(unc);
        return cols;
    }
    function colMembers(col) {
        var p = parseCol(col.id);
        if (p.kind === "slot") return S.cards.filter(function (c) { return slotOf(c) === p.key; });
        if (p.kind === "tag") {
            return S.cards.filter(function (c) {
                return slotOf(c) === "mainboard" && tagsOf(c.name).indexOf(p.key) !== -1;
            });
        }
        return S.cards.filter(function (c) { return slotOf(c) === "mainboard" && tagsOf(c.name).length === 0; });
    }

    // ---- rendering ----------------------------------------------------------
    function printingLabel(p) {
        if (!p || !p.set) return "set…";
        return (p.set || "").toUpperCase() + " #" + (p.collector_number || "?") +
            (p.finish && p.finish !== "nonfoil" ? " " + p.finish : "");
    }
    function chipHtml(c, col) {
        var pinned = slotOf(c) === "commander";
        return '<div class="tnov-chip' + (excludedCard(c) ? " is-excluded" : "") + '"' +
            (pinned ? "" : ' draggable="true"') +
            ' data-card="' + esc(c.name) + '" data-col="' + esc(col.id) + '">' +
            '<span class="hud-card-name">' + window.HUD.cardName(c.name) + "</span>" +
            ((c.qty || 1) > 1 ? '<span class="tnov-qty">×' + (c.qty || 1) + "</span>" : "") +
            '<button type="button" class="tnov-print" data-print="' + esc(c.name) +
                '" title="Change printing — a different printing counts as a new card">' +
                esc(printingLabel(c.printing)) + "</button>" +
            (c.needs_purchase ? '<span class="tnov-need" title="This exact printing is not in your inventory">needs purchase</span>' : "") +
            (pinned ? "" : '<button type="button" class="tnov-remove" data-remove="' + esc(c.name) +
                '" title="Remove from the draft decklist" aria-label="Remove ' + esc(c.name) + '">×</button>') +
            "</div>";
    }
    function colHtml(col) {
        var p = parseCol(col.id);
        var members = colMembers(col);
        var count = members.reduce(function (s, c) { return s + (c.qty || 1); }, 0);
        var off = p.kind === "tag" && flagOff(p.key);
        var flag = p.kind === "tag"
            ? '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost tnov-flag' + (off ? " is-on" : "") +
              '" data-flag="' + esc(p.key) + '" aria-pressed="' + (off ? "true" : "false") +
              '" title="Not in decklist: exclude this category from deck counts and exports">⚑</button>'
            : "";
        var addBox = (p.kind === "slot" && p.key === "commander") ? ""
            : '<div class="tnov-add"><input type="text" class="tnov-add-input" data-add-col="' + esc(col.id) +
              '" placeholder="+ Add card" aria-label="Add a card to ' + esc(col.label) + '" autocomplete="off" spellcheck="false"></div>';
        return '<section class="hud-panel tnov-col' + (off ? " is-excluded" : "") + '" data-col="' + esc(col.id) + '">' +
            '<header class="hud-panel-head tnov-col-head"><h3 class="hud-panel-title">' + esc(col.label) + "</h3>" +
            '<span class="hud-panel-meta">' + count + "</span>" + flag + "</header>" +
            '<div class="hud-panel-body tnov-col-body">' +
            (members.map(function (c) { return chipHtml(c, col); }).join("") ||
                '<div class="hud-empty tnov-empty">Drop cards here</div>') +
            addBox + "</div></section>";
    }
    function renderState() {
        var stateEl = $("tuneOvState");
        if (saving) stateEl.textContent = "Saving draft…";
        else if (dirty) stateEl.textContent = "Unsaved changes";
        else if (S && S.draft) stateEl.textContent = "Draft saved";
        else stateEl.textContent = "No draft yet — edits start one";
        $("tuneOvDrift").hidden = !(S && S.drifted);
        $("tuneOvRebase").hidden = !(S && S.drifted);
    }
    function render() {
        if (!S) return;
        $("tuneOvColumns").innerHTML = allColumns().map(colHtml).join("");
        acAttachAll();
        var n = counts();
        $("tuneOvCount").textContent = n.total + " in decklist" + (n.dropped ? " · " + n.dropped + " excluded" : "");
        renderState();
    }
    function fail(e) {
        $("tuneOvError").textContent = e.message;
        load();  // resync from the authoritative server state
    }

    // ---- server sync --------------------------------------------------------
    function applyState(j) {
        S = j;
        S.cards.forEach(function (c) { if (!c.slot) c.slot = "mainboard"; });
    }
    function load() {
        return send("GET", "/tuneup/draft").then(function (j) {
            applyState(j); loaded = true; dirty = false;
            $("tuneOvError").textContent = "";
            render();
        }).catch(function (e) {
            $("tuneOvColumns").innerHTML = '<div class="hud-empty">Couldn’t load the decklist: ' + esc(e.message) + "</div>";
        });
    }
    function saveNow(rebase) {
        saving = true; renderState();
        return send("POST", "/tuneup/draft", { cards: S.cards, rebase: !!rebase }).then(function (j) {
            applyState(j); dirty = false; saving = false; loaded = true; render();
        }).catch(function (e) {
            saving = false; renderState();
            throw e;
        });
    }
    function queueSave() {
        dirty = true; renderState();
        if (saveTimer) clearTimeout(saveTimer);
        saveTimer = setTimeout(function () {
            saveTimer = null;
            saveNow(false).catch(fail);
        }, 350);
    }

    // ---- mutations ----------------------------------------------------------
    function moveCard(name, fromId, toId) {
        var from = parseCol(fromId), to = parseCol(toId);
        var c = cardByName(name);
        if (!c || slotOf(c) === "commander") return;
        if (to.kind === "slot" && to.key !== "commander") c.slot = to.key;
        else if (to.kind !== "slot" && (slotOf(c) === "maybeboard" || slotOf(c) === "considering")) c.slot = "mainboard";
        var adds = [], removals = [];
        if (from.kind === "tag" && !(to.kind === "tag" && to.key === from.key)) removals.push(from.key);
        if (to.kind === "tag" && tagsOf(name).indexOf(to.key) === -1) adds.push(to.key);
        adds.forEach(function (t) { pushTag(name, t, "user"); });
        removals.forEach(function (t) { pullTag(name, t); });
        render();
        var chain = Promise.resolve();
        adds.forEach(function (t) {
            chain = chain.then(function () { return send("POST", "/card-tags", { card_name: name, tag: t, source: "user" }); });
        });
        removals.forEach(function (t) {
            chain = chain.then(function () { return send("DELETE", "/card-tags", { card_name: name, tag: t }); });
        });
        chain.then(queueSave).catch(fail);
    }
    function addCard(name, colId, source) {
        name = String(name || "").trim();
        if (!name || !S) return;
        var col = parseCol(colId);
        var slot = col.kind === "slot" && col.key !== "commander" ? col.key : "mainboard";
        var existing = S.cards.filter(function (c) { return c.name.toLowerCase() === name.toLowerCase(); })[0];
        if (existing) existing.qty = (existing.qty || 1) + 1;
        else S.cards.push({ name: name, qty: 1, slot: slot, source: source || "manual" });
        var chain = Promise.resolve();
        if (col.kind === "tag" && tagsOf(name).indexOf(col.key) === -1) {
            pushTag(name, col.key, "user");
            chain = chain.then(function () { return send("POST", "/card-tags", { card_name: name, tag: col.key, source: "user" }); });
        }
        render();
        chain.then(queueSave).catch(fail);
    }
    function removeCard(name) {
        S.cards = S.cards.filter(function (c) { return c.name !== name; });
        render();
        queueSave();
    }
    function toggleFlag(tag) {
        var on = flagOff(tag);   // currently excluded -> set in_decklist back on
        S.tag_flags[tag] = { in_decklist: on };
        render();
        send("PUT", "/tuneup/tag-flags", { tag: tag, in_decklist: on }).then(function (j) {
            S.tag_flags = j.flags; render();
        }).catch(fail);
    }
    function addColumn(tag) {
        tag = String(tag || "").trim();
        if (!tag || !S) return;
        var id = "tag:" + tag;
        if (!allColumns().some(function (c) { return c.id === id; })) {
            extraCols.push({ id: id, label: tag, kind: "tag", tag: tag });
        }
        $("tuneOvNewTag").value = "";
        render();
    }

    // ---- add-card autocomplete: the shared CardAutocomplete component ----------
    // (card_autocomplete.js, GET /api/cards/autocomplete). The columns re-render
    // wholesale, so inputs are (re)attached after every render; handles for
    // inputs that left the DOM are destroyed.
    var acHandles = [];
    function acAttachAll() {
        acHandles = acHandles.filter(function (h) {
            if (document.body.contains(h.input)) return true;
            h.destroy();
            return false;
        });
        if (!window.CardAutocomplete) return;
        document.querySelectorAll("#tuneOvColumns .tnov-add-input").forEach(function (input) {
            if (acHandles.some(function (h) { return h.input === input; })) return;
            acHandles.push(CardAutocomplete.attach(input, {
                onSelect: function (card) {
                    input.value = "";
                    addCard(card.name, input.getAttribute("data-add-col"));
                    input.focus();
                },
            }));
        });
    }
    // Enter with no suggestion highlighted and no list open still adds the typed name.
    function acEnterFallback(e) {
        if (e.key !== "Enter" || e.defaultPrevented) return;
        var input = e.target;
        if (input.value.trim()) addCard(input.value, input.getAttribute("data-add-col"));
        input.value = "";
        e.preventDefault();
    }

    // ---- printing picker (DT-3, spec §3.1/§3.3) -------------------------------
    // A printing change is treated as a NEW card: the chosen exact
    // (set, collector_number, finish) goes on the draft card and the server
    // recomputes needs_purchase against the instance registry on save.
    function ppClose() {
        if (pp.box) pp.box.hidden = true;
        pp.card = null;
    }
    function ppPick(set, cn, finish, owned) {
        var c = cardByName(pp.card);
        ppClose();
        if (!c) return;
        c.printing = { set: set, collector_number: cn, finish: finish };
        c.needs_purchase = !owned;
        render();
        queueSave();   // POST /tuneup/draft recomputes needs_purchase server-side
    }
    function ppRender(name, printings) {
        var rows = [];
        printings.forEach(function (p) {
            (p.finishes || []).forEach(function (f) {
                rows.push(
                    '<div class="tnov-pp-opt" role="option" data-pp-set="' + esc(p.set) +
                    '" data-pp-cn="' + esc(p.collector_number) + '" data-pp-finish="' + esc(f.finish) +
                    '" data-pp-owned="' + (f.owned ? "1" : "") + '">' +
                    (p.image ? '<img src="' + esc(p.image) + '" alt="" loading="lazy">' : "") +
                    '<span class="tnov-pp-main"><span class="tnov-pp-set">' + esc(p.set_name || p.set) + "</span>" +
                    '<span class="tnov-pp-sub"> ' + esc((p.set || "").toUpperCase() + " #" + p.collector_number +
                        " · " + f.finish + (f.usd ? " · $" + f.usd : "")) + "</span></span>" +
                    '<span class="tnov-pp-own ' + (f.owned ? "is-owned" : "is-buy") + '">' +
                    (f.owned ? "owned" : "buy") + "</span></div>");
            });
        });
        pp.box.innerHTML = rows.join("") ||
            '<div class="tnov-pp-status">No printings found for ' + esc(name) + ".</div>";
        pp.box.hidden = false;
    }
    function ppOpen(chip, name) {
        var host = chip.closest(".tnov-col-body") || chip.parentNode;
        if (pp.box.parentNode !== host) host.appendChild(pp.box);
        if (pp.card === name && !pp.box.hidden) { ppClose(); return; }
        pp.card = name;
        pp.box.innerHTML = '<div class="tnov-pp-status">Loading printings…</div>';
        pp.box.hidden = false;
        fetch(API + "/tuneup/printings?name=" + encodeURIComponent(name))
            .then(function (r) { return r.json(); })
            .then(function (j) {
                if (pp.card !== name) return;
                if (j.error) pp.box.innerHTML = '<div class="tnov-pp-status">' + esc(j.error) + "</div>";
                else ppRender(name, j.printings || []);
            })
            .catch(function () {
                if (pp.card === name) pp.box.innerHTML = '<div class="tnov-pp-status">Printing lookup failed.</div>';
            });
    }

    // ---- drag & drop ----------------------------------------------------------
    function bindDnd(cols) {
        cols.addEventListener("dragstart", function (e) {
            var chip = e.target.closest(".tnov-chip");
            if (!chip) return;
            dragPayload = { name: chip.getAttribute("data-card"), col: chip.getAttribute("data-col") };
            e.dataTransfer.setData("text/plain", dragPayload.name);
            e.dataTransfer.effectAllowed = "move";
        });
        cols.addEventListener("dragover", function (e) {
            var col = e.target.closest(".tnov-col");
            if (!col) return;
            // Internal chip move, or an external card-name drag (DT-6 float panel).
            var external = !dragPayload && e.dataTransfer.types &&
                Array.prototype.indexOf.call(e.dataTransfer.types, "text/plain") !== -1;
            if (!dragPayload && !external) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = dragPayload ? "move" : "copy";
            Array.prototype.forEach.call(cols.querySelectorAll(".tnov-col.is-drop"), function (c) {
                if (c !== col) c.classList.remove("is-drop");
            });
            col.classList.add("is-drop");
        });
        cols.addEventListener("dragleave", function (e) {
            var col = e.target.closest(".tnov-col");
            if (col && !col.contains(e.relatedTarget)) col.classList.remove("is-drop");
        });
        cols.addEventListener("drop", function (e) {
            var col = e.target.closest(".tnov-col");
            if (!col) return;
            var to = col.getAttribute("data-col");
            if (dragPayload) {
                e.preventDefault();
                col.classList.remove("is-drop");
                if (to !== dragPayload.col) moveCard(dragPayload.name, dragPayload.col, to);
                dragPayload = null;
                return;
            }
            // DT-6: a recommendation dragged in from the float panel becomes a
            // draft card (tagged when dropped on a tag column) and leaves the panel.
            var name = e.dataTransfer.getData("text/plain");
            if (!name || to === "slot:commander") return;
            e.preventDefault();
            col.classList.remove("is-drop");
            addCard(name, to, "recommendation");
            if (window.TuneOverlay.adds) window.TuneOverlay.adds.consumed(name);
        });
        cols.addEventListener("dragend", function () {
            dragPayload = null;
            Array.prototype.forEach.call(cols.querySelectorAll(".tnov-col.is-drop"), function (c) { c.classList.remove("is-drop"); });
        });
    }

    // ---- view switching -------------------------------------------------------
    function setView(v, push) {
        var overlay = v === "overlay";
        $("tuneRecsView").hidden = overlay;
        $("tuneOverlayView").hidden = !overlay;
        (overlay ? $("view-overlay") : $("view-recs")).checked = true;
        if (push !== false) {
            var p = new URLSearchParams(location.search);
            if (overlay) p.set("view", "overlay"); else p.delete("view");
            history.replaceState(null, "", location.pathname + (p.toString() ? "?" + p.toString() : "") + location.hash);
        }
        if (overlay && !loaded) load();
        if (overlay && window.TuneOverlay.adds) window.TuneOverlay.adds.reload();
    }

    function start() {
        if (!$("tuneOverlayView")) return;
        injectPpCss();
        pp.box = document.createElement("div");
        pp.box.className = "tnov-pp";
        pp.box.setAttribute("role", "listbox");
        pp.box.hidden = true;
        pp.box.addEventListener("mousedown", function (e) {
            var opt = e.target.closest("[data-pp-set]");
            if (!opt) return;
            e.preventDefault();
            ppPick(opt.getAttribute("data-pp-set"), opt.getAttribute("data-pp-cn"),
                   opt.getAttribute("data-pp-finish"), opt.getAttribute("data-pp-owned") === "1");
        });
        document.addEventListener("keydown", function (e) { if (e.key === "Escape") ppClose(); });
        document.addEventListener("click", function (e) {
            if (!pp.box || pp.box.hidden) return;
            if (pp.box.contains(e.target) || e.target.closest("[data-print]")) return;
            ppClose();
        });
        var cols = $("tuneOvColumns");
        cols.addEventListener("click", function (e) {
            var rm = e.target.closest("[data-remove]");
            if (rm) { removeCard(rm.getAttribute("data-remove")); return; }
            var pr = e.target.closest("[data-print]");
            if (pr) { ppOpen(pr.closest(".tnov-chip") || pr, pr.getAttribute("data-print")); return; }
            var fl = e.target.closest("[data-flag]");
            if (fl) toggleFlag(fl.getAttribute("data-flag"));
        });
        cols.addEventListener("keydown", function (e) {
            if (e.target.classList && e.target.classList.contains("tnov-add-input")) acEnterFallback(e);
        });
        bindDnd(cols);

        $("tuneOvSaveDraft").addEventListener("click", function () {
            if (!S) return;
            if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
            saveNow(false).catch(fail);
        });
        $("tuneOvRebase").addEventListener("click", function () {
            if (!S) return;
            saveNow(true).catch(fail);
        });
        $("tuneOvAddCol").addEventListener("click", function () { addColumn($("tuneOvNewTag").value); });
        $("tuneOvNewTag").addEventListener("keydown", function (e) {
            if (e.key === "Enter") { e.preventDefault(); addColumn(e.target.value); }
        });
        document.querySelectorAll('input[name="tuneView"]').forEach(function (x) {
            x.addEventListener("change", function () { setView(x.value); });
        });

        if (new URLSearchParams(location.search).get("view") === "overlay") setView("overlay", false);
    }

    window.TuneOverlay = {
        init: function (c) {
            cfg = c;
            API = "/api/decks/" + encodeURIComponent(c.deckId);
            if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
            else start();
        },
        active: function () {
            var o = $("view-overlay");
            return !!(o && o.checked);
        },
        reload: function () { return loaded ? load() : Promise.resolve(); }
    };
})();

/* ---- DT-7: recommendation explanations + reconsider/alternative (merged from main) ---- */

/* Deck Tuner overlay — DT-7 module: recommendation explanations + the
 * "Reconsider" / "Recommend something else" buttons (overlay spec §3.3).
 *
 * Self-contained under window.TuneOverlay so sibling cards (DT-1 shell, DT-5
 * cuts, DT-6 adds) extend the same namespace instead of overwriting this file.
 *
 * Two mount modes:
 *   1. Classic Tune page (deck_tune.html): auto-boots when the page loaded this
 *      script with a data-deck-id attribute and a #swaps list exists. Rows
 *      (.hud-swap[data-cut]) rendered by the page's own inline script are
 *      decorated after every render via MutationObserver — nothing in the
 *      page's script is touched.
 *   2. DT-1 overlay shell: call TuneOverlay.explanations.boot({api, root,
 *      rowSelector, cutAttr}) directly.
 *
 * Styles are injected scoped (.tn7-*) on HUD tokens so hud.css stays untouched.
 */
(function () {
    "use strict";
    var NS = (window.TuneOverlay = window.TuneOverlay || {});
    if (NS.explanations) return;

    function esc(s) {
        if (window.HUD && window.HUD.esc) return window.HUD.esc(s);
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }

    var CSS =
        ".tn7-expl{grid-column:1/-1;display:flex;flex-direction:column;gap:var(--sp-2);" +
        "padding:var(--sp-2) 0 0;border-top:var(--hairline) solid var(--line)}" +
        ".tn7-reason{margin:0;color:var(--text-2);font-size:var(--fs-xs)}" +
        ".tn7-recon{margin:0;color:var(--text-1);font-size:var(--fs-xs)}" +
        ".tn7-recon .tn7-tag{color:var(--text-3);text-transform:uppercase;letter-spacing:.04em;" +
        "font-family:var(--font-mono);font-size:var(--fs-micro);margin-right:var(--sp-1)}" +
        ".tn7-prev{margin:0;color:var(--text-3);font-size:var(--fs-xs)}" +
        ".tn7-act{display:flex;gap:var(--sp-2);align-items:center;flex-wrap:wrap}" +
        ".tn7-msg{color:var(--text-3);font-size:var(--fs-xs)}";

    function injectCss() {
        if (document.getElementById("tn7-css")) return;
        var el = document.createElement("style");
        el.id = "tn7-css";
        el.textContent = CSS;
        document.head.appendChild(el);
    }

    var Ex = {};
    var S = {
        api: null, root: null,
        rowSelector: ".hud-swap[data-cut]", cutAttr: "data-cut",
        entries: {}, log: [], loading: null, observer: null,
    };

    function send(method, path, body) {
        return fetch(S.api + path, {
            method: method,
            headers: { "Content-Type": "application/json" },
            body: body === undefined ? undefined : JSON.stringify(body),
        }).then(function (r) {
            return r.json().then(function (j) {
                if (!r.ok || j.success === false) {
                    var e = new Error(j.error || "HTTP " + r.status);
                    e.status = r.status;
                    throw e;
                }
                return j;
            });
        });
    }

    function index(payload) {
        S.entries = {};
        S.log = (payload && payload.entry_log) || [];
        ((payload && payload.swaps) || []).forEach(function (s) {
            if (!s.cut || !s.cut.name) return;
            var add = (s.pick === "buy" ? s.buy : s.own) || s.own || s.buy;
            if (!add) return;
            S.entries[s.cut.name] = {
                entryId: s.entry_id || null, cut: s.cut.name, add: add.name,
                reason: s.reason || s.why || "", status: s.status || "open",
                reconsideration: s.reconsideration || "",
            };
        });
    }

    function fetchSaved() {
        return send("GET", "/tune/saved").then(function (j) {
            var rec = j.recommendation, p = rec && rec.payload;
            if (!p) { index(null); return; }
            var legacy = (p.swaps || []).some(function (s) { return !s.entry_id; });
            if (!legacy) { index(p); return; }
            // Saved before DT-7: /tune/why doubles as the entry sync (it writes
            // entry ids + explanations back into the active record), then refetch.
            var pr = rec.params || {};
            var opts = { mode: pr.mode || "flex", in_deck: pr.in_deck !== false, buy: pr.buy !== false,
                         goal: pr.goal || "balanced" };
            return send("POST", "/tune/why", opts).then(reload, reload);
            function reload() {
                return send("GET", "/tune/saved").then(function (j2) {
                    index(j2.recommendation && j2.recommendation.payload);
                });
            }
        });
    }

    function load() {
        if (S.loading) return S.loading;
        S.loading = fetchSaved().catch(function () { /* entries are best effort */ })
            .then(function () { S.loading = null; });
        return S.loading;
    }

    function previousFor(cut) {
        var prev = null;
        S.log.forEach(function (e) {
            if (e && e.cut === cut && e.status === "ignored") prev = e;
        });
        return prev;
    }

    function decorateRow(li) {
        var cut = li.getAttribute(S.cutAttr);
        if (!cut) return;
        var e = S.entries[cut];
        // Idempotency guard: decorate() runs from a MutationObserver watching
        // this same root, so rebuilding an unchanged row would mutate the DOM,
        // retrigger the observer and loop forever. A change signature makes
        // decorate() a no-op when nothing the block shows has changed.
        var prev = previousFor(cut);
        var sig = !e || e.status === "ignored" ? "off"
            : JSON.stringify([e.reason, e.reconsideration, prev && prev.card,
                              !!li.querySelector(".tn-why")]);
        if (li.querySelector(".tn7-expl") && li._tn7sig === sig) return;
        li._tn7sig = sig;
        var stale = li.querySelector(".tn7-expl");
        if (stale) stale.remove();
        if (!e || e.status === "ignored") return;
        var html = "";
        // The page's own .tn-why shows the same (LLM) explanation — don't double it.
        if (!li.querySelector(".tn-why") && e.reason) {
            html += '<p class="tn7-reason">' + esc(e.reason) + "</p>";
        }
        if (e.reconsideration) {
            html += '<p class="tn7-recon"><span class="tn7-tag">Reconsidered</span>' + esc(e.reconsideration) + "</p>";
        }
        if (prev && prev.card) {
            html += '<p class="tn7-prev">Previously suggested for this slot: ' + esc(prev.card) + " (ignored).</p>";
        }
        html += '<div class="tn7-act">' +
            '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost" data-tn7="reconsider"' +
            ' title="Challenge this recommendation; it answers and stands its ground">Reconsider</button>' +
            '<button type="button" class="hud-btn hud-btn--sm hud-btn--ghost" data-tn7="alternative"' +
            ' title="Ignore this card and recommend something else for the same slot">Recommend something else</button>' +
            '<span class="tn7-msg" aria-live="polite"></span></div>';
        var div = document.createElement("div");
        div.className = "tn7-expl";
        div.innerHTML = html;
        li.appendChild(div);
    }

    function decorate() {
        if (!S.root) return;
        var rows = S.root.querySelectorAll(S.rowSelector);
        for (var i = 0; i < rows.length; i++) decorateRow(rows[i]);
    }

    var refreshTimer = null;
    function scheduleRefresh() {
        if (refreshTimer) return;
        refreshTimer = setTimeout(function () {
            refreshTimer = null;
            load().then(decorate);
        }, 250);
    }

    function watch() {
        decorate();
        if (S.observer || !S.root || !window.MutationObserver) return;
        S.observer = new MutationObserver(function () {
            // The page re-renders rows on every action; decorate the new nodes.
            // Only re-fetch entries when a row shows up that we don't know yet.
            var unknown = false;
            var rows = S.root.querySelectorAll(S.rowSelector);
            for (var i = 0; i < rows.length; i++) {
                var cut = rows[i].getAttribute(S.cutAttr);
                if (cut && !S.entries[cut]) { unknown = true; break; }
            }
            if (unknown) scheduleRefresh();
            else decorate();
        });
        S.observer.observe(S.root, { childList: true, subtree: true });
    }

    function msg(li, text) {
        var el = li.querySelector(".tn7-msg");
        if (el) el.textContent = text || "";
    }

    function buttons(li, disabled) {
        var bs = li.querySelectorAll("[data-tn7]");
        for (var i = 0; i < bs.length; i++) bs[i].disabled = disabled;
    }

    function onReconsider(li) {
        var cut = li.getAttribute(S.cutAttr);
        buttons(li, true);
        msg(li, "Thinking it over…");
        send("POST", "/tune/reconsider", { cut: cut }).then(function (j) {
            if (S.entries[cut]) S.entries[cut].reconsideration = j.response;
            li._tn7sig = null;  // force rebuild: a cache-hit answer changes no
            decorateRow(li);    // signature, and the buttons must re-enable
        }).catch(function (e) {
            msg(li, e.message || "Reconsider failed.");
            buttons(li, false);
        });
    }

    function onAlternative(li) {
        var cut = li.getAttribute(S.cutAttr);
        buttons(li, true);
        msg(li, "Finding another card for this slot…");
        send("POST", "/tune/alternative", { cut: cut }).then(function (j) {
            if (!j.alternative) {
                msg(li, j.message || "No alternative right now.");
                buttons(li, false);
                return;
            }
            // The saved recommendation changed under the page's own snapshot, so
            // reload focused on this slot: the page re-renders the replacement row.
            var glue = location.search ? "&" : "?";
            location.href = location.pathname + location.search + glue + "focus=" + encodeURIComponent(cut);
        }).catch(function (e) {
            msg(li, e.message || "Could not find an alternative.");
            buttons(li, false);
        });
    }

    function onClick(ev) {
        var b = ev.target && ev.target.closest ? ev.target.closest("[data-tn7]") : null;
        if (!b) return;
        var li = b.closest(S.rowSelector.split(",")[0]);
        if (!li) return;
        ev.preventDefault();
        if (b.getAttribute("data-tn7") === "reconsider") onReconsider(li);
        else onAlternative(li);
    }

    Ex.boot = function (opts) {
        opts = opts || {};
        if (!opts.api) return;
        injectCss();
        S.api = opts.api;
        S.root = opts.root || document.body;
        if (opts.rowSelector) S.rowSelector = opts.rowSelector;
        if (opts.cutAttr) S.cutAttr = opts.cutAttr;
        S.root.addEventListener("click", onClick);
        load().then(watch);
    };

    Ex.reload = function () { return load().then(decorate); };
    Ex.entries = function () { return S.entries; };
    NS.explanations = Ex;

    // Classic Tune page auto-boot: the template includes this script with
    // data-deck-id; #swaps is the existing recommendation list.
    document.addEventListener("DOMContentLoaded", function () {
        var tag = document.querySelector("script[data-tune-overlay][data-deck-id]");
        var swaps = document.getElementById("swaps");
        if (!tag || !swaps) return;
        Ex.boot({ api: "/api/decks/" + encodeURIComponent(tag.getAttribute("data-deck-id")), root: swaps });
    });
})();

/* ---- DT-6: add-recommendations float panel (overlay spec §2/§3.3) ---- */

/* Float panel on the far right of the overlay: the picked add of each open
 * swap, inventory-filtered server-side (GET /tuneup/recommendations —
 * owned-and-free cards never appear; a missing exact printing or an unowned
 * card shows with a Purchase flag). Drag a card into any overlay column to
 * put it in the paper draft; the drop itself is handled by the DT-1 shell's
 * external-drag path above. Self-contained under window.TuneOverlay.adds like
 * the DT-7 module; styles injected scoped (.tn6-*) on HUD tokens.
 */
(function () {
    "use strict";
    var NS = (window.TuneOverlay = window.TuneOverlay || {});
    if (NS.adds) return;

    function esc(s) {
        if (window.HUD && window.HUD.esc) return window.HUD.esc(s);
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }
    function cardName(n) {
        return window.HUD && window.HUD.cardName ? window.HUD.cardName(n) : esc(n);
    }

    var CSS =
        ".tn6-panel{position:fixed;right:var(--sp-4);top:120px;width:248px;max-height:70vh;" +
        "display:flex;flex-direction:column;z-index:40;background:var(--bg-2);border:var(--stroke);" +
        "border-radius:var(--radius);box-shadow:var(--shadow,0 8px 24px rgba(0,0,0,.35))}" +
        ".tn6-panel[hidden]{display:none}" +
        ".tn6-head{display:flex;align-items:baseline;gap:var(--sp-2);padding:var(--sp-3) var(--sp-3) var(--sp-2);" +
        "border-bottom:var(--hairline) solid var(--line)}" +
        ".tn6-title{margin:0;font-size:var(--fs-sm);color:var(--text-1)}" +
        ".tn6-count{margin-left:auto;font-family:var(--font-mono);font-size:var(--fs-micro);color:var(--text-3)}" +
        ".tn6-body{overflow-y:auto;padding:var(--sp-2);display:flex;flex-direction:column;gap:var(--sp-2)}" +
        ".tn6-card{padding:var(--sp-2) var(--sp-3);border:var(--hairline) solid var(--line);" +
        "border-radius:var(--radius);cursor:grab;background:var(--bg-1)}" +
        ".tn6-card:active{cursor:grabbing}" +
        ".tn6-row{display:flex;align-items:center;gap:var(--sp-2)}" +
        ".tn6-buy{margin-left:auto;font-family:var(--font-mono);font-size:var(--fs-micro);" +
        "text-transform:uppercase;letter-spacing:.04em;color:var(--signal);border:var(--hairline) solid var(--signal);" +
        "border-radius:var(--radius);padding:0 var(--sp-1)}" +
        ".tn6-meta{margin:var(--sp-1) 0 0;font-size:var(--fs-xs);color:var(--text-3)}" +
        ".tn6-empty{padding:var(--sp-3);color:var(--text-3);font-size:var(--fs-xs)}";

    function injectCss() {
        if (document.getElementById("tn6-css")) return;
        var el = document.createElement("style");
        el.id = "tn6-css";
        el.textContent = CSS;
        document.head.appendChild(el);
    }

    var S = { api: null, adds: [], panel: null, loading: null, booted: false, failed: false };

    function entryHtml(a) {
        var buy = a.needs_purchase
            ? '<span class="tn6-buy" title="Not in your collection' +
              (a.printing && a.printing.set ? " in this printing" : "") + ' — purchase needed">Purchase</span>' : "";
        var meta = a.cut ? '<p class="tn6-meta">Suggested for ' + esc(a.cut) + "</p>" : "";
        return '<div class="tn6-card" draggable="true" data-card="' + esc(a.card) + '">' +
            '<div class="tn6-row"><span class="hud-card-name">' + cardName(a.card) + "</span>" + buy + "</div>" +
            meta + "</div>";
    }
    function render() {
        if (!S.panel) return;
        S.panel.hidden = false;
        var body = S.adds.length
            ? S.adds.map(entryHtml).join("")
            : '<div class="tn6-empty">' + (S.failed
                ? "Couldn’t load recommendations."
                : "Nothing to add: every recommended card is already in your collection and free to use, or no tune review has run yet.") + "</div>";
        S.panel.innerHTML = '<header class="tn6-head"><h3 class="tn6-title">Recommendations</h3>' +
            '<span class="tn6-count">' + S.adds.length + "</span></header>" +
            '<div class="tn6-body">' + body + "</div>";
    }
    function load() {
        if (!S.api) return Promise.resolve();
        if (S.loading) return S.loading;
        S.loading = fetch(S.api + "/tuneup/recommendations").then(function (r) {
            return r.json().then(function (j) {
                if (!r.ok || j.success === false) throw new Error(j.error || ("HTTP " + r.status));
                return j;
            });
        }).then(function (j) {
            S.adds = j.adds || [];
            S.failed = false;
            S.loading = null;
            render();
        }).catch(function () {
            S.failed = true;
            S.loading = null;
            render();
        });
        return S.loading;
    }

    var Adds = {
        boot: function (api) {
            if (S.booted) return;
            S.booted = true;
            injectCss();
            S.api = api;
            S.panel = document.getElementById("tuneRecPanel");
            if (!S.panel) return;
            // Drag out: a plain card name is all the DT-1 drop handler needs.
            S.panel.addEventListener("dragstart", function (e) {
                var chip = e.target.closest(".tn6-card");
                if (!chip) return;
                e.dataTransfer.setData("text/plain", chip.getAttribute("data-card"));
                e.dataTransfer.effectAllowed = "copy";
            });
            load();
        },
        reload: function () { return load(); },
        // A card dragged into the deck leaves the panel for this session.
        consumed: function (name) {
            S.adds = S.adds.filter(function (a) { return a.card !== name; });
            render();
        },
        entries: function () { return S.adds; },
    };
    NS.adds = Adds;

    document.addEventListener("DOMContentLoaded", function () {
        var tag = document.querySelector("script[data-tune-overlay][data-deck-id]");
        if (!tag || !document.getElementById("tuneRecPanel")) return;
        Adds.boot("/api/decks/" + encodeURIComponent(tag.getAttribute("data-deck-id")));
    });
})();
