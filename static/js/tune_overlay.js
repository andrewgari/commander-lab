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
    var ac = { items: [], active: -1, input: null, timer: null, box: null };

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
    function chipHtml(c, col) {
        var pinned = slotOf(c) === "commander";
        return '<div class="tnov-chip' + (excludedCard(c) ? " is-excluded" : "") + '"' +
            (pinned ? "" : ' draggable="true"') +
            ' data-card="' + esc(c.name) + '" data-col="' + esc(col.id) + '">' +
            '<span class="hud-card-name">' + window.HUD.cardName(c.name) + "</span>" +
            ((c.qty || 1) > 1 ? '<span class="tnov-qty">×' + (c.qty || 1) + "</span>" : "") +
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
    function addCard(name, colId) {
        name = String(name || "").trim();
        if (!name || !S) return;
        var col = parseCol(colId);
        var existing = S.cards.filter(function (c) { return c.name.toLowerCase() === name.toLowerCase(); })[0];
        if (existing) existing.qty = (existing.qty || 1) + 1;
        else S.cards.push({ name: name, qty: 1, slot: col.kind === "slot" ? col.key : "mainboard", source: "manual" });
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

    // ---- add-card autocomplete (over /api/decks/{id}/search) -----------------
    function acClose() {
        if (ac.box) ac.box.hidden = true;
        ac.items = []; ac.active = -1; ac.input = null;
    }
    function acPick(item) {
        var input = ac.input;
        acClose();
        if (!input) return;
        input.value = "";
        addCard(item.name, input.getAttribute("data-add-col"));
        input.focus();
    }
    function acRender() {
        var box = ac.box;
        box.innerHTML = ac.items.map(function (it, i) {
            return '<div class="tnov-ac-option' + (i === ac.active ? " is-active" : "") + '" role="option" data-ac="' + i + '">' +
                '<span class="hud-card-name">' + esc(it.name) + "</span>" +
                '<span class="tnov-ac-type">' + esc(it.type || "") + "</span></div>";
        }).join("");
        box.hidden = !ac.items.length;
    }
    function acSchedule(input) {
        ac.input = input;
        if (ac.timer) clearTimeout(ac.timer);
        ac.timer = setTimeout(function () {
            var q = input.value.trim();
            if (q.length < 2) { acClose(); return; }
            fetch(API + "/search?q=" + encodeURIComponent(q)).then(function (r) { return r.json(); }).then(function (j) {
                if (ac.input !== input || input.value.trim() !== q) return;
                ac.items = (j.results || []).slice(0, 8);
                ac.active = -1;
                var host = input.closest(".tnov-add");
                if (ac.box.parentNode !== host) host.appendChild(ac.box);
                acRender();
            }).catch(acClose);
        }, 200);
    }
    function acKey(e) {
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            if (!ac.items.length) return false;
            var d = e.key === "ArrowDown" ? 1 : -1;
            ac.active = (ac.active + d + ac.items.length) % ac.items.length;
            acRender(); e.preventDefault(); return true;
        }
        if (e.key === "Escape") { acClose(); return true; }
        if (e.key === "Enter") {
            e.preventDefault();
            var input = e.target;
            if (ac.active >= 0 && ac.items[ac.active]) { acPick(ac.items[ac.active]); return true; }
            acClose();
            if (input.value.trim()) addCard(input.value, input.getAttribute("data-add-col"));
            input.value = "";
            return true;
        }
        return false;
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
            if (!col || !dragPayload) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = "move";
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
            if (!col || !dragPayload) return;
            e.preventDefault();
            col.classList.remove("is-drop");
            var to = col.getAttribute("data-col");
            if (to !== dragPayload.col) moveCard(dragPayload.name, dragPayload.col, to);
            dragPayload = null;
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
    }

    function start() {
        if (!$("tuneOverlayView")) return;
        ac.box = document.createElement("div");
        ac.box.className = "tnov-ac";
        ac.box.setAttribute("role", "listbox");
        ac.box.hidden = true;
        ac.box.addEventListener("mousedown", function (e) {
            var opt = e.target.closest("[data-ac]");
            if (opt) { e.preventDefault(); acPick(ac.items[Number(opt.getAttribute("data-ac"))]); }
        });

        var cols = $("tuneOvColumns");
        cols.addEventListener("click", function (e) {
            var rm = e.target.closest("[data-remove]");
            if (rm) { removeCard(rm.getAttribute("data-remove")); return; }
            var fl = e.target.closest("[data-flag]");
            if (fl) toggleFlag(fl.getAttribute("data-flag"));
        });
        cols.addEventListener("input", function (e) {
            if (e.target.classList.contains("tnov-add-input")) acSchedule(e.target);
        });
        cols.addEventListener("keydown", function (e) {
            if (e.target.classList && e.target.classList.contains("tnov-add-input")) acKey(e);
        });
        cols.addEventListener("focusout", function (e) {
            if (e.target.classList && e.target.classList.contains("tnov-add-input")) {
                setTimeout(function () { if (document.activeElement !== ac.input) acClose(); }, 120);
            }
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
