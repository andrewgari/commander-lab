/* ==========================================================================
   CardAutocomplete — the one shared card-name suggestion component
   (spec: docs/architecture/shared-card-autocomplete.md).

   Vanilla JS, no framework. Behavior lifted from the working brew.js
   commander picker and generalized: debounced fetch with a sequence guard,
   combobox/listbox aria wiring, ArrowUp/Down/Enter/Escape, and
   loading/empty/error/degraded state rows (an error row is always
   distinguishable from "no matches"). commitOnEnter:"first" makes Enter with
   no arrow-highlight commit the top result (the residual Brew bug).

   Usage:
     CardAutocomplete.attach(inputEl, {
       endpoint: "/api/cards/autocomplete",  // null = no network (local source only)
       commandersOnly: false,                // adds commanders_only=true
       extraSource: async (q) => [...names], // optional local pool, merged first
       minChars: 2, debounceMs: 200, limit: 8,
       commitOnEnter: "first",
       onSelect: (card, input) => {},        // default: input.value = card.name
       listEl: existingUl,                   // optional; one is created otherwise
       loadingText / emptyText / errorText / degradedText,
     });
   Returns a handle with destroy().
   ========================================================================== */
(function () {
    "use strict";

    var esc = (window.HUD && HUD.esc) || function (s) {
        return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
        });
    };

    var uidCounter = 0;

    function normalizeItem(item) {
        // extraSource may return bare names or {name, badge, ...} objects.
        if (typeof item === "string") return { name: item };
        return item || { name: "" };
    }

    function attach(inputEl, opts) {
        opts = opts || {};
        var endpoint = opts.endpoint === undefined ? "/api/cards/autocomplete" : opts.endpoint;
        var commandersOnly = !!opts.commandersOnly;
        var extraSource = opts.extraSource || null;
        var minChars = opts.minChars == null ? 2 : opts.minChars;
        var debounceMs = opts.debounceMs == null ? 200 : opts.debounceMs;
        var limit = opts.limit || 8;
        var commitOnEnter = opts.commitOnEnter || "first";
        var onSelect = opts.onSelect || null;
        var loadingText = opts.loadingText || "Searching…";
        var emptyText = opts.emptyText || "No matches";
        var errorText = opts.errorText || "Search failed — check your connection and try again.";
        var degradedText = opts.degradedText || "Card search is offline — showing local matches.";

        /* ---- listbox element (caller-supplied or created) ---- */
        var ownsList = !opts.listEl;
        var listEl = opts.listEl || document.createElement("ul");
        listEl.classList.add("hud-autocomplete");
        listEl.setAttribute("role", "listbox");
        listEl.hidden = true;
        if (!listEl.id) listEl.id = (inputEl.id || "hud-ac") + "-list-" + (++uidCounter);
        if (ownsList) inputEl.insertAdjacentElement("afterend", listEl);
        // The dropdown is absolutely positioned against its parent.
        var parent = listEl.parentElement;
        if (parent && window.getComputedStyle && getComputedStyle(parent).position === "static") {
            parent.style.position = "relative";
        }

        inputEl.setAttribute("role", "combobox");
        inputEl.setAttribute("aria-autocomplete", "list");
        inputEl.setAttribute("aria-controls", listEl.id);
        inputEl.setAttribute("aria-expanded", "false");
        if (!inputEl.getAttribute("autocomplete")) inputEl.setAttribute("autocomplete", "off");

        var debounce = null, activeIdx = -1, currentResults = [], searchSequence = 0, destroyed = false;
        var optId = listEl.id + "-opt-";

        function closeList() {
            clearTimeout(debounce);
            searchSequence++;
            listEl.hidden = true;
            inputEl.setAttribute("aria-expanded", "false");
            inputEl.removeAttribute("aria-activedescendant");
            activeIdx = -1;
        }

        function renderMessage(message, modifier) {
            currentResults = [];
            activeIdx = -1;
            listEl.innerHTML = "";
            var item = document.createElement("li");
            item.className = "hud-autocomplete-state" + (modifier ? " hud-autocomplete-state--" + modifier : "");
            item.setAttribute("aria-disabled", "true");
            item.textContent = message;
            listEl.appendChild(item);
            listEl.hidden = false;
            inputEl.setAttribute("aria-expanded", "true");
            inputEl.removeAttribute("aria-activedescendant");
        }

        function renderResults(list, note) {
            currentResults = list;
            listEl.innerHTML = "";
            if (note) {
                var noteEl = document.createElement("li");
                noteEl.className = "hud-autocomplete-state hud-autocomplete-state--" + note.kind;
                noteEl.setAttribute("aria-disabled", "true");
                noteEl.textContent = note.text;
                listEl.appendChild(noteEl);
            }
            if (!list.length) {
                if (!note) renderMessage(emptyText, "empty");
                else { listEl.hidden = false; inputEl.setAttribute("aria-expanded", "true"); }
                return;
            }
            list.forEach(function (c, i) {
                var li = document.createElement("li");
                li.id = optId + i;
                li.setAttribute("role", "option");
                li.setAttribute("aria-selected", i === activeIdx ? "true" : "false");
                var img = c.image
                    ? '<img src="' + esc(c.image) + '" alt="" loading="lazy">'
                    : '<span class="hud-autocomplete-thumb"></span>';
                li.innerHTML = img +
                    '<span class="hud-autocomplete-text"><span class="hud-autocomplete-name">' + esc(c.name) + "</span>" +
                    (c.type_line ? '<br><span class="hud-autocomplete-type">' + esc(c.type_line) + "</span>" : "") +
                    "</span>" +
                    (c.badge ? '<span class="hud-chip hud-chip--ok hud-autocomplete-badge">' + esc(c.badge) + "</span>" : "");
                li.addEventListener("mousedown", function (ev) { ev.preventDefault(); commit(c); });
                listEl.appendChild(li);
            });
            listEl.hidden = false;
            inputEl.setAttribute("aria-expanded", "true");
            if (activeIdx >= 0 && activeIdx < list.length) {
                inputEl.setAttribute("aria-activedescendant", optId + activeIdx);
                var active = document.getElementById(optId + activeIdx);
                if (active && active.scrollIntoView) active.scrollIntoView({ block: "nearest" });
            } else {
                inputEl.removeAttribute("aria-activedescendant");
            }
        }

        function fetchAll(q) {
            var extraP = extraSource
                ? Promise.resolve().then(function () { return extraSource(q); }).then(
                    function (items) { return (items || []).map(normalizeItem); },
                    function () { return []; })
                : Promise.resolve([]);
            if (!endpoint) {
                return extraP.then(function (extras) {
                    return { items: extras.slice(0, limit), note: null };
                });
            }
            var url = endpoint + (endpoint.indexOf("?") === -1 ? "?" : "&") +
                "q=" + encodeURIComponent(q) + "&limit=" + limit +
                (commandersOnly ? "&commanders_only=true" : "");
            var netP = fetch(url).then(function (res) {
                if (!res.ok) throw new Error("HTTP " + res.status);
                return res.json();
            }).then(
                function (body) { return { body: body }; },
                function () { return { failed: true }; });
            return Promise.all([extraP, netP]).then(function (parts) {
                var extras = parts[0], net = parts[1];
                if (net.failed) {
                    // Network down: local extras still surface, flagged as an
                    // error (never silently indistinguishable from no-match).
                    if (extras.length) {
                        return { items: extras.slice(0, limit), note: { text: errorText, kind: "error" } };
                    }
                    throw new Error("search failed");
                }
                var body = net.body || {};
                var seen = {};
                var items = [];
                extras.forEach(function (c) {
                    seen[(c.name || "").toLowerCase()] = true;
                    items.push(c);
                });
                (body.results || []).forEach(function (c) {
                    if (!seen[(c.name || "").toLowerCase()]) items.push(c);
                });
                var note = body.degraded ? { text: degradedText, kind: "degraded" } : null;
                return { items: items.slice(0, limit), note: note };
            });
        }

        function runSearch() {
            var q = inputEl.value.trim();
            var sequence = ++searchSequence;
            if (q.length < minChars) { closeList(); return; }
            if (endpoint) renderMessage(loadingText, "loading");
            fetchAll(q).then(function (out) {
                if (sequence === searchSequence && inputEl.value.trim() === q) {
                    activeIdx = -1;
                    renderResults(out.items, out.note);
                }
            }).catch(function () {
                if (sequence === searchSequence && inputEl.value.trim() === q) {
                    renderMessage(errorText, "error");
                }
            });
        }

        function commit(card) {
            if (!card) return;
            if (onSelect) onSelect(card, inputEl);
            else inputEl.value = card.name;
            closeList();
        }

        /* ---- wiring ---- */
        function onInput() {
            clearTimeout(debounce);
            searchSequence++;
            if (inputEl.value.trim().length >= minChars) {
                debounce = setTimeout(runSearch, debounceMs);
            } else {
                closeList();
            }
        }
        function onFocus() {
            // Re-open the suggestion list when returning to a field that
            // still has text (e.g. after picking, then editing the query).
            if (inputEl.value.trim().length >= minChars) runSearch();
        }
        function onKeydown(ev) {
            if (ev.key === "Escape") {
                // Esc also cancels a pending debounce / in-flight search, so a
                // keystroke-then-Esc does not re-open the list afterwards.
                var wasOpen = !listEl.hidden;
                closeList();
                if (wasOpen) ev.preventDefault();
                return;
            }
            if (listEl.hidden || !currentResults.length) return;
            if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
                ev.preventDefault();
                var max = currentResults.length - 1;
                activeIdx = ev.key === "ArrowDown" ? Math.min(max, activeIdx + 1) : Math.max(0, activeIdx - 1);
                renderResults(currentResults, null);
            } else if (ev.key === "Enter") {
                ev.preventDefault();
                var pick = activeIdx >= 0
                    ? currentResults[activeIdx]
                    : (commitOnEnter === "first" ? currentResults[0] : null);
                if (pick) commit(pick);
            }
        }
        function onDocDown(ev) {
            if (!inputEl.contains(ev.target) && !listEl.contains(ev.target)) closeList();
        }

        inputEl.addEventListener("input", onInput);
        inputEl.addEventListener("focus", onFocus);
        inputEl.addEventListener("keydown", onKeydown);
        document.addEventListener("mousedown", onDocDown);
        document.addEventListener("touchstart", onDocDown, { passive: true });

        return {
            input: inputEl,
            list: listEl,
            close: closeList,
            destroy: function () {
                if (destroyed) return;
                destroyed = true;
                closeList();
                inputEl.removeEventListener("input", onInput);
                inputEl.removeEventListener("focus", onFocus);
                inputEl.removeEventListener("keydown", onKeydown);
                document.removeEventListener("mousedown", onDocDown);
                document.removeEventListener("touchstart", onDocDown);
                if (ownsList && listEl.parentNode) listEl.parentNode.removeChild(listEl);
            },
        };
    }

    window.CardAutocomplete = { attach: attach };
})();
