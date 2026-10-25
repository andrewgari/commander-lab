/* ==========================================================================
   Brew flow (BREW-CODE): /brew console (S1) + /brew/{id} session page (S2/S3).
   HUD-kit native: HUD.* builders, TcgCart for the buy list. Donor decks are
   read-only; generation never moves cards.
   ========================================================================== */
(function () {
    "use strict";

    var esc = (window.HUD && HUD.esc) || function (s) {
        return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
        });
    };

    function $(id) { return document.getElementById(id); }
    function fetchJSON(url, opts) {
        return fetch(url, opts).then(function (res) {
            return res.json().then(function (body) {
                if (!res.ok) {
                    var err = new Error((body && body.error) || ("Request failed (" + res.status + ")"));
                    err.status = res.status;
                    throw err;
                }
                return body;
            });
        });
    }
    function postJSON(url, body) {
        return fetchJSON(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body || {}),
        });
    }
    function readJSONScript(id) {
        var el = $(id);
        if (!el) return null;
        try { return JSON.parse(el.textContent); } catch (e) { return null; }
    }
    function relTime(iso) {
        var t = Date.parse(iso || "");
        if (!isFinite(t)) return "";
        var s = Math.max(0, (Date.now() - t) / 1000);
        if (s < 60) return "just now";
        if (s < 3600) return Math.floor(s / 60) + "m ago";
        if (s < 86400) return Math.floor(s / 3600) + "h ago";
        return Math.floor(s / 86400) + "d ago";
    }
    function money(v) { return "$" + Number(v || 0).toFixed(2); }

    /* ========================================================================
       S1 — /brew console
       ======================================================================== */
    function initConsole() {
        var donors = readJSONScript("brew-donor-data") || [];
        var resume = readJSONScript("brew-resume-data") || {};
        var donorById = {};
        donors.forEach(function (d) { donorById[d.registry_id] = d; });

        var state = {
            commanders: [],   // [{name, uid, image, color_identity, type_line, keywords}]
            donors: [],       // [registry_id]
            themes: [],
        };

        /* ---- commander picker ---- */
        var cmdInput = $("brewCmdSearch"), cmdResults = $("brewCmdResults"),
            cmdError = $("brewCmdError"), cmdSelected = $("brewCmdSelected"),
            cmdFrames = $("brewCmdFrames"), cmdNames = $("brewCmdNames"),
            cmdPips = $("brewCmdPips"), cmdPartner = $("brewCmdPartner"),
            cmdRemove = $("brewCmdRemove");
        var debounce = null, activeIdx = -1, currentResults = [], searchSequence = 0;

        function closeResults() {
            searchSequence++;
            cmdResults.hidden = true;
            cmdInput.setAttribute("aria-expanded", "false");
            cmdInput.removeAttribute("aria-activedescendant");
            activeIdx = -1;
        }
        function renderMessage(message, className) {
            currentResults = [];
            activeIdx = -1;
            cmdResults.innerHTML = "";
            var item = document.createElement("li");
            item.className = className || "brew-cmd-state";
            item.setAttribute("aria-disabled", "true");
            item.textContent = message;
            cmdResults.appendChild(item);
            cmdResults.hidden = false;
            cmdInput.setAttribute("aria-expanded", "true");
            cmdInput.removeAttribute("aria-activedescendant");
        }
        function showError(msg) {
            renderMessage(msg, "brew-cmd-state brew-cmd-state--error");
        }
        function renderResults(list) {
            currentResults = list;
            cmdResults.innerHTML = "";
            if (!list.length) {
                renderMessage("No commanders match — check spelling", "brew-cmd-state brew-cmd-state--empty");
                return;
            }
            list.forEach(function (c, i) {
                var li = document.createElement("li");
                li.id = "brewCmdOption" + i;
                li.setAttribute("role", "option");
                li.setAttribute("aria-selected", i === activeIdx ? "true" : "false");
                var img = c.image ? '<img src="' + esc(c.image) + '" alt="" loading="lazy">' : '<span style="width:40px;flex:none"></span>';
                li.innerHTML = img + '<span><span class="brew-cmd-name">' + esc(c.name) + '</span><br>' +
                    '<span class="brew-cmd-type">' + esc(c.type_line || "") + '</span></span>';
                li.addEventListener("mousedown", function (ev) { ev.preventDefault(); pickCommander(c); });
                cmdResults.appendChild(li);
            });
            cmdResults.hidden = false;
            cmdInput.setAttribute("aria-expanded", "true");
            if (activeIdx >= 0) {
                cmdInput.setAttribute("aria-activedescendant", "brewCmdOption" + activeIdx);
                var active = $("brewCmdOption" + activeIdx);
                if (active && active.scrollIntoView) active.scrollIntoView({ block: "nearest" });
            } else cmdInput.removeAttribute("aria-activedescendant");
        }
        function runSearch() {
            var q = cmdInput.value.trim();
            var sequence = ++searchSequence;
            if (q.length < 2) { closeResults(); return; }
            renderMessage("Searching commanders…", "brew-cmd-state brew-cmd-state--loading");
            fetchJSON("/api/brew/commanders?q=" + encodeURIComponent(q))
                .then(function (body) {
                    if (sequence === searchSequence && cmdInput.value.trim() === q) renderResults(body.results || []);
                })
                .catch(function () {
                    if (sequence === searchSequence && cmdInput.value.trim() === q) showError("Commander search failed. Try again.");
                });
        }
        function pickCommander(c) {
            if (state.commanders.length >= 2) return;
            state.commanders.push(c);
            cmdInput.value = "";
            closeResults();
            renderCommander();
            if (!document.activeElement || document.activeElement !== cmdInput) cmdInput.focus();
        }
        function hasPartnerKeyword(c) {
            return (c.keywords || []).some(function (k) {
                return /partner|background|friends forever/i.test(k);
            });
        }
        function renderCommander() {
            var has = state.commanders.length > 0;
            cmdSelected.hidden = !has;
            cmdInput.style.display = has && state.commanders.length >= 2 ? "none" : "";
            cmdFrames.innerHTML = state.commanders.map(function (c) {
                var src = "";
                if (c.uid && window.HUD && HUD.scryfall) src = HUD.scryfall(c.uid, "normal").src;
                else if (c.image) src = c.image;
                return '<span class="brew-cmd-frame">' + (src ? '<img src="' + esc(src) + '" alt="' + esc(c.name) + '">' : "") + "</span>";
            }).join("");
            cmdNames.innerHTML = state.commanders.map(function (c) { return esc(c.name); }).join(" + ");
            var colors = [];
            state.commanders.forEach(function (c) {
                (c.color_identity || []).forEach(function (x) { if (colors.indexOf(x) === -1) colors.push(x); });
            });
            cmdPips.innerHTML = window.HUD ? HUD.pips(colors) : colors.join("");
            cmdPartner.hidden = !(state.commanders.length === 1 && hasPartnerKeyword(state.commanders[0]));
            // Suggest a deck name
            var nameInput = $("brewName");
            if (has && (!nameInput.value || / brew$/.test(nameInput.value) || !nameInput.dataset.touched)) {
                nameInput.value = state.commanders[0].name.split(",")[0] + " brew";
            }
            syncRail();
        }
        cmdInput.addEventListener("input", function () {
            clearTimeout(debounce);
            searchSequence++;
            if (cmdInput.value.trim().length >= 2) {
                renderMessage("Searching commanders…", "brew-cmd-state brew-cmd-state--loading");
                debounce = setTimeout(runSearch, 250);
            } else closeResults();
        });
        cmdInput.addEventListener("focus", function () {
            // Re-open the suggestion list when returning to a field that
            // still has text (e.g. after picking, then editing the query).
            if (cmdInput.value.trim().length >= 2) runSearch();
        });
        document.addEventListener("mousedown", function (ev) {
            if (!cmdInput.contains(ev.target) && !cmdResults.contains(ev.target)) closeResults();
        });
        document.addEventListener("touchstart", function (ev) {
            if (!cmdInput.contains(ev.target) && !cmdResults.contains(ev.target)) closeResults();
        }, { passive: true });
        cmdInput.addEventListener("keydown", function (ev) {
            if (ev.key === "Escape") {
                if (!cmdResults.hidden) {
                    ev.preventDefault();
                    closeResults();
                }
                return;
            }
            if (cmdResults.hidden || !currentResults.length) return;
            if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
                ev.preventDefault();
                var max = currentResults.length - 1;
                activeIdx = ev.key === "ArrowDown" ? Math.min(max, activeIdx + 1) : Math.max(0, activeIdx - 1);
                renderResults(currentResults);
            } else if (ev.key === "Enter") {
                if (activeIdx < 0 || !currentResults[activeIdx]) return;
                ev.preventDefault();
                pickCommander(currentResults[activeIdx]);
            }
        });
        cmdRemove.addEventListener("click", function () {
            state.commanders = [];
            cmdInput.style.display = "";
            renderCommander();
            cmdInput.focus();
        });
        cmdPartner.addEventListener("click", function () {
            cmdInput.style.display = "";
            cmdInput.focus();
        });

        /* ---- donors ---- */
        var donorList = $("brewDonorList"), donorChips = $("brewDonorChips"),
            donorFilter = $("brewDonorFilter"), donorEmpty = $("brewDonorEmpty"),
            donorNote = $("brewDonorNote");

        function renderDonorChips() {
            donorChips.innerHTML = state.donors.map(function (id) {
                var d = donorById[id];
                if (!d) return "";
                return '<span class="hud-chip hud-chip--data">' + esc(d.name) +
                    '<button class="hud-chip-remove" type="button" data-id="' + esc(id) + '" aria-label="Remove ' + esc(d.name) + '">×</button></span>';
            }).join("");
            donorChips.querySelectorAll(".hud-chip-remove").forEach(function (btn) {
                btn.addEventListener("click", function () {
                    state.donors = state.donors.filter(function (x) { return x !== btn.dataset.id; });
                    renderDonors();
                });
            });
            donorEmpty.hidden = state.donors.length > 0;
            donorNote.hidden = state.donors.length <= 8;
        }
        var visibleDonors = [], donorActiveIdx = -1;
        function toggleDonor(id, on) {
            if (on && state.donors.indexOf(id) === -1) state.donors.push(id);
            if (!on) state.donors = state.donors.filter(function (x) { return x !== id; });
            renderDonorChips();
            renderDonorList();
            syncRail();
        }
        function renderDonorList() {
            if (!donorList) return;
            var q = (donorFilter ? donorFilter.value : "").trim().toLowerCase();
            visibleDonors = donors.filter(function (d) { return !q || d.name.toLowerCase().indexOf(q) !== -1; });
            if (!visibleDonors.length) {
                donorActiveIdx = -1;
                // Free text that matches no deck must say so, not fail silently.
                donorList.innerHTML = '<li class="brew-donor-none" aria-disabled="true">No decks match "' +
                    esc(q.length > 40 ? q.slice(0, 37) + "…" : q) + '" — pick a name from the list.</li>';
                return;
            }
            if (donorActiveIdx >= visibleDonors.length) donorActiveIdx = visibleDonors.length - 1;
            donorList.innerHTML = visibleDonors
                .map(function (d, i) {
                    var checked = state.donors.indexOf(d.registry_id) !== -1;
                    return '<li' + (i === donorActiveIdx ? ' class="kb-focus"' : "") +
                        '><label style="display:flex;align-items:center;gap:var(--sp-2);width:100%;cursor:pointer">' +
                        '<input type="checkbox" data-id="' + esc(d.registry_id) + '"' + (checked ? " checked" : "") + '>' +
                        '<span class="brew-donor-name">' + esc(d.name) + "</span>" +
                        (window.HUD ? HUD.statusChip(d.status) : esc(d.status)) +
                        '<span class="brew-donor-meta">' + (window.HUD ? HUD.pips(d.color || "", "sm") : "") + " " +
                        d.card_count + " cards · " + esc((d.commanders || []).join(" + ")) + "</span>" +
                        "</label></li>";
                }).join("");
            donorList.querySelectorAll("input[type=checkbox]").forEach(function (box) {
                box.addEventListener("change", function () {
                    var id = box.dataset.id;
                    if (box.checked && state.donors.indexOf(id) === -1) state.donors.push(id);
                    if (!box.checked) state.donors = state.donors.filter(function (x) { return x !== id; });
                    renderDonorChips();
                    syncRail();
                });
            });
        }
        function renderDonors() { renderDonorChips(); renderDonorList(); }
        if (donorFilter) {
            donorFilter.addEventListener("input", function () {
                donorActiveIdx = -1;
                renderDonorList();
            });
            donorFilter.addEventListener("keydown", function (ev) {
                if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
                    if (!visibleDonors.length) return;
                    ev.preventDefault();
                    var max = visibleDonors.length - 1;
                    donorActiveIdx = ev.key === "ArrowDown" ? Math.min(max, donorActiveIdx + 1) : Math.max(0, donorActiveIdx - 1);
                    renderDonorList();
                    var focused = donorList.querySelectorAll("li")[donorActiveIdx];
                    if (focused && focused.scrollIntoView) focused.scrollIntoView({ block: "nearest" });
                } else if (ev.key === "Enter") {
                    ev.preventDefault();
                    var d = visibleDonors[donorActiveIdx] || visibleDonors[0];
                    if (d) toggleDonor(d.registry_id, state.donors.indexOf(d.registry_id) === -1);
                } else if (ev.key === "Escape") {
                    donorFilter.value = "";
                    donorActiveIdx = -1;
                    renderDonorList();
                }
            });
        }

        /* ---- themes ---- */
        var themeInput = $("brewThemeInput"), themePills = $("brewThemePills");
        function renderThemes() {
            themePills.innerHTML = state.themes.map(function (t, i) {
                return '<span class="hud-chip hud-chip--ghost">' + esc(t) +
                    '<button class="hud-chip-remove" type="button" data-i="' + i + '" aria-label="Remove ' + esc(t) + '">×</button></span>';
            }).join("");
            themePills.querySelectorAll(".hud-chip-remove").forEach(function (btn) {
                btn.addEventListener("click", function () {
                    state.themes.splice(Number(btn.dataset.i), 1);
                    renderThemes();
                });
            });
        }
        themeInput.addEventListener("keydown", function (ev) {
            if (ev.key !== "Enter") return;
            ev.preventDefault();
            var v = themeInput.value.trim();
            if (v && state.themes.indexOf(v) === -1) { state.themes.push(v); renderThemes(); }
            themeInput.value = "";
        });
        $("brewName").addEventListener("input", function () { this.dataset.touched = "1"; });

        /* ---- power description ---- */
        var powerDescs = {
            "1": "Casual — janky, fun-first pile.",
            "2": "Low — precon-ish, light staples.",
            "3": "Mid — tuned casual, some staples.",
            "4": "High — optimized, fast mana, efficient engines.",
            "5": "cEDH-adjacent — the strongest cards you own.",
        };
        document.querySelectorAll('input[name="power"]').forEach(function (radio) {
            radio.addEventListener("change", function () {
                if (radio.checked) $("brewPowerDesc").textContent = powerDescs[radio.value] || "";
            });
        });

        /* ---- rail / validation ---- */
        function buildMode() {
            var el = document.querySelector('input[name="build_mode"]:checked');
            return el ? el.value : "owned_first";
        }
        function donorPoolSize() {
            var names = {};
            state.donors.forEach(function (id) {
                var d = donorById[id];
                if (d) names[id] = d.unique_cards || 0;
            });
            return Object.keys(names).reduce(function (n, k) { return n + names[k]; }, 0);
        }
        function syncRail() {
            var first = state.commanders[0];
            $("brewKvCmd").textContent = state.commanders.map(function (c) { return c.name; }).join(" + ") || "—";
            var colors = [];
            state.commanders.forEach(function (c) {
                (c.color_identity || []).forEach(function (x) { if (colors.indexOf(x) === -1) colors.push(x); });
            });
            $("brewKvColors").innerHTML = window.HUD ? HUD.pips(colors) : "—";
            $("brewKvDonors").textContent = state.donors.length + (state.donors.length === 1 ? " deck" : " decks");
            $("brewKvMode").textContent = buildMode() === "owned_first" ? "Owned-first" : "Best build";
            $("brewKvPool").textContent = donorPoolSize() + " unique cards";
            var art = $("brewRailArt");
            if (first) {
                var src = "";
                if (first.uid && window.HUD && HUD.scryfall) src = HUD.scryfall(first.uid, "art_crop").src;
                else if (first.image) src = first.image;
                art.innerHTML = src ? '<img src="' + esc(src) + '" alt="' + esc(first.name) + '">' : '<span class="brew-art-empty">' + esc(first.name) + "</span>";
            } else {
                art.innerHTML = '<span class="brew-art-empty">Pick a commander</span>';
            }
            var ok = state.commanders.length > 0;
            var reason = ok ? "" : "Pick a commander to brew";
            [$("brewGenerate"), $("brewGenerateMobile")].forEach(function (b) {
                if (!b) return;
                b.disabled = !ok;
                b.setAttribute("aria-disabled", String(!ok));
                if (!ok) b.setAttribute("title", reason);
                else b.removeAttribute("title");
            });
            $("brewGenerateHint").textContent = reason;
            $("brewMobileName").textContent = first ? first.name : "No commander";
            $("brewMobilePool").textContent = state.donors.length + " donors";
        }
        document.querySelectorAll('input[name="build_mode"]').forEach(function (r) {
            r.addEventListener("change", syncRail);
        });

        /* ---- generate ---- */
        function generate() {
            var errBox = $("brewError");
            errBox.hidden = true;
            var payload = {
                commanders: state.commanders.map(function (c) { return c.name; }),
                donor_deck_ids: state.donors.slice(),
                build_mode: buildMode(),
                name: $("brewName").value.trim(),
                power_level: (document.querySelector('input[name="power"]:checked') || {}).value || "3",
                idea: $("brewIdea").value.trim(),
                themes: state.themes.slice(),
                notes: $("brewNotes").value.trim(),
            };
            [$("brewGenerate"), $("brewGenerateMobile")].forEach(function (b) { if (b) b.disabled = true; });
            postJSON("/api/brew/start", payload)
                .then(function (body) {
                    return postJSON("/api/brew/" + encodeURIComponent(body.session_id) + "/build", {})
                        .then(function () { return body; });
                })
                .then(function (body) {
                    window.location.href = "/brew/" + encodeURIComponent(body.session_id);
                })
                .catch(function (err) {
                    errBox.textContent = err.message || "Could not start the brew.";
                    errBox.hidden = false;
                    [$("brewGenerate"), $("brewGenerateMobile")].forEach(function (b) { if (b) b.disabled = false; });
                });
        }
        $("brewGenerate").addEventListener("click", generate);
        $("brewGenerateMobile").addEventListener("click", generate);

        /* ---- recent brews ---- */
        fetchJSON("/api/brew/sessions?limit=5").then(function (body) {
            var list = (body.sessions || []).slice(0, 5);
            if (!list.length) return;
            $("brewRecent").hidden = false;
            $("brewRecentList").innerHTML = list.map(function (s) {
                var name = (s.preferences && s.preferences[0] && s.preferences[0].name) || s.commander || s.session_id;
                return '<li><a href="/brew/' + encodeURIComponent(s.session_id) + '">' + esc(name) + "</a>" +
                    (window.HUD ? HUD.statusChip(s.status) : esc(s.status)) +
                    '<span class="hud-freshness" style="margin-left:auto">' + esc(relTime(s.created_at)) + "</span></li>";
            }).join("");
        }).catch(function () { /* tray stays hidden */ });

        /* ---- resume (re-brew) ---- */
        if (resume && resume.session_id) {
            state.donors = (resume.donor_deck_ids || []).filter(function (id) { return donorById[id]; });
            state.themes = (resume.themes || []).slice();
            var pref = (resume.preferences || [])[0] || {};
            if (pref.name) { $("brewName").value = pref.name; $("brewName").dataset.touched = "1"; }
            if (resume.deck_idea) $("brewIdea").value = resume.deck_idea;
            if (resume.notes) $("brewNotes").value = resume.notes;
            var mode = resume.build_mode || "owned_first";
            var modeEl = document.querySelector('input[name="build_mode"][value="' + mode + '"]');
            if (modeEl) modeEl.checked = true;
            var p = String(resume.power_level || "3");
            var pEl = document.querySelector('input[name="power"][value="' + p + '"]');
            if (pEl) { pEl.checked = true; $("brewPowerDesc").textContent = powerDescs[p] || ""; }
            // Commander names: rehydrate as name-only entries; art loads lazily
            // from the search endpoint on the next pick.
            state.commanders = (resume.commanders || []).map(function (n) { return { name: n, color_identity: [] }; });
            renderThemes();
            renderDonors();
            renderCommander();
        } else {
            renderDonors();
            renderCommander();
        }
        syncRail();
    }

    /* ========================================================================
       S2/S3 — /brew/{session_id}
       ======================================================================== */
    function initSession() {
        var root = $("brewRoot");
        var sessionId = root.dataset.sessionId;
        var pollTimer = null, pollStart = Date.now();

        function show(el) { el.hidden = false; }
        function hide(el) { el.hidden = true; }
        function failSession() {
            hide($("brewBuilding")); hide($("brewDeck"));
            show($("brewNotFound"));
        }

        function provenanceChip(source, deckName) {
            if (source === "buy") return window.HUD ? HUD.chip("Buy", "neutral") : "Buy";
            if (source === "free_inventory") return window.HUD ? HUD.chip("Inventory", "data") : "Inventory";
            var label = "From " + (deckName || "donor");
            var short = label.length > 22 ? label.slice(0, 19) + "…" : label;
            return '<span class="hud-chip hud-chip--signal" title="' + esc(label) + '">' + esc(short) + "</span>";
        }

        function renderBuilding(session) {
            hide($("brewDeck")); hide($("brewNotFound"));
            show($("brewBuilding"));
            var failed = session.status === "failed";
            $("brewFailed").hidden = !failed;
            $("brewBuildName").textContent = ((session.preferences || [])[0] || {}).name || session.commander || "Brew";
            if (failed) {
                $("brewFailedMsg").textContent = session.error || "The brew failed.";
                $("brewBackBtn").href = "/brew?resume=" + encodeURIComponent(sessionId);
                if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
                return;
            }
            var note = session.progress || "Brewing…";
            $("brewProgressNote").textContent = note;
            if (Date.now() - pollStart > 5 * 60 * 1000) show($("brewLongNote"));
            if (!pollTimer) {
                pollTimer = setInterval(function () {
                    fetchJSON("/api/brew/" + encodeURIComponent(sessionId)).then(function (b) {
                        route(b.session);
                    }).catch(function () { /* keep polling */ });
                }, 2000);
            }
        }

        function renderDeck(session) {
            if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
            hide($("brewBuilding")); hide($("brewNotFound"));
            show($("brewDeck"));
            var draft = (session.drafts || [])[0] || {};
            var name = draft.name || session.commander || "Brew";
            document.title = name + " | Commander Lab";
            var head = root.querySelector(".hud-page-head h1");
            if (head) head.textContent = name;
            var eyebrow = root.querySelector(".hud-page-head .hud-label");
            if (eyebrow) eyebrow.textContent = "VIRTUAL BREW · " + (session.commander || "");
            $("brewDeckPips").innerHTML = window.HUD ? HUD.pips(draft.color || "") : "";
            var pl = String(session.power_level || "");
            $("brewDeckPower").innerHTML = pl && window.HUD ? HUD.chip("P" + pl, "neutral") : "";
            $("brewFreshness").textContent = "generated " + relTime(session.updated_at);
            $("brewRebrew").href = "/brew?resume=" + encodeURIComponent(sessionId);
            $("brewEditRebrew").href = "/brew?resume=" + encodeURIComponent(sessionId);
            // Commander strip
            $("brewCmdStrip").innerHTML = (draft.commanders || []).map(function (c) {
                return '<span style="display:inline-flex;align-items:center;gap:var(--sp-2)">' +
                    '<span style="font-family:var(--font-card)">' + esc(c) + "</span></span>";
            }).join(" ");

            fetchJSON("/api/brew/" + encodeURIComponent(sessionId) + "/availability").then(function (rep) {
                renderReport(session, draft, rep);
            }).catch(function () {
                $("brewTableWrap").innerHTML = '<div class="hud-empty">Availability report unavailable.</div>';
            });
        }

        function renderReport(session, draft, rep) {
            var ownedDonor = rep.owned.filter(function (o) { return o.source !== "free_inventory"; });
            var ownedInv = rep.owned.filter(function (o) { return o.source === "free_inventory"; });
            var buy = rep.buy_list || [];
            var counts = [ownedDonor.length, ownedInv.length, buy.length];
            var stats = $("brewStats").querySelectorAll(".hud-stat-value");
            if (stats[0]) stats[0].textContent = counts[0];
            if (stats[1]) stats[1].textContent = counts[1];
            if (stats[2]) stats[2].textContent = counts[2];
            var costKnown = rep.is_complete;
            if (stats[3]) stats[3].textContent = (costKnown ? "" : "≥ ") + money(rep.total_buy_cost);
            var foots = $("brewStats").querySelectorAll(".hud-stat-foot");
            if (foots[3]) foots[3].textContent = costKnown ? "" : (rep.unpriced_cards || []).length + " cards unpriced";
            if (rep.owned.some(function (o) { return o.stale; })) show($("brewStaleNote"));

            // Provenance bar
            var total = Math.max(1, counts[0] + counts[1] + counts[2]);
            var bar = $("brewProvBar");
            var segs = [
                { n: counts[0], cls: "hud-prov-bar-donor", label: "From donors" },
                { n: counts[1], cls: "hud-prov-bar-inv", label: "Inventory" },
                { n: counts[2], cls: "hud-prov-bar-buy", label: "Buy" },
            ];
            bar.innerHTML = segs.map(function (s) {
                return '<span class="hud-prov-bar-seg ' + s.cls + '" style="width:' + (s.n / total * 100).toFixed(1) + '%"></span>';
            }).join("") +
                '<span class="hud-prov-bar-legend">' + segs.map(function (s) {
                    return '<span class="hud-prov-bar-key ' + s.cls + '"></span>' + esc(s.label) + " " + s.n;
                }).join(" · ") + "</span>";

            // Deck table + tabs
            var donorNames = {};
            (rep.donor_projections || []).forEach(function (d) { donorNames[d.deck_id] = d.deck_name; });
            var srcByName = {};
            rep.owned.forEach(function (o) { srcByName[o.name.toLowerCase()] = o; });
            var buyByName = {};
            buy.forEach(function (b) { buyByName[b.name.toLowerCase()] = b; });
            var cmdNames = (draft.commanders || []).map(function (c) { return c.toLowerCase(); });
            var rows = (draft.cards || []).map(function (c) {
                var k = (c.name || "").toLowerCase();
                var o = srcByName[k], b = buyByName[k];
                var group = o ? (o.source === "free_inventory" ? "inventory" : "donor") : "buy";
                return {
                    name: c.name, qty: c.quantity || 1, group: group,
                    chip: o ? provenanceChip(o.source, o.source_deck_name || donorNames[o.source]) : provenanceChip("buy"),
                    price: b ? (b.price_known ? money(b.unit_price) : "—") : "",
                    isCommander: cmdNames.indexOf(k) !== -1,
                };
            });

            var groups = [
                ["all", "All · " + rows.length],
                ["donor", "From donors · " + rows.filter(function (r) { return r.group === "donor"; }).length],
                ["inventory", "From inventory · " + rows.filter(function (r) { return r.group === "inventory"; }).length],
                ["buy", "To buy · " + rows.filter(function (r) { return r.group === "buy"; }).length],
            ];
            var tabs = $("brewTabs");
            tabs.innerHTML = groups.map(function (g, i) {
                return '<button type="button" class="hud-tab' + (i === 0 ? " active" : "") + '" data-group="' + g[0] + '">' + esc(g[1]) + "</button>";
            }).join("");

            function renderTable(group) {
                var shown = rows.filter(function (r) { return group === "all" || r.group === group; });
                var cmdRow = rows.filter(function (r) { return r.isCommander && (group === "all" || r.group === group); });
                var html = '<table class="hud-table" data-hud-sortable><thead><tr>' +
                    '<th data-sort="num"><button type="button" class="hud-sort">Qty</button></th>' +
                    '<th data-sort="text"><button type="button" class="hud-sort">Card</button></th>' +
                    '<th data-sort="text"><button type="button" class="hud-sort">Source</button></th>' +
                    '<th data-sort="num"><button type="button" class="hud-sort">Price</button></th>' +
                    "</tr></thead><tbody>" +
                    shown.map(function (r) {
                        return "<tr" + (r.isCommander ? ' class="is-commander"' : "") + "><td>" + r.qty + "</td>" +
                            '<td><span class="hud-card-name">' + esc(r.name) + "</span></td>" +
                            "<td>" + r.chip + "</td><td>" + esc(r.price) + "</td></tr>";
                    }).join("") +
                    "</tbody></table>";
                if (!shown.length) html = '<div class="hud-empty">No cards in this group.</div>';
                $("brewTableWrap").innerHTML = html;
                if (window.HUD) HUD.initSortable($("brewTableWrap"));
            }
            tabs.querySelectorAll(".hud-tab").forEach(function (btn) {
                btn.addEventListener("click", function () {
                    tabs.querySelectorAll(".hud-tab").forEach(function (b) { b.classList.remove("active"); });
                    btn.classList.add("active");
                    renderTable(btn.dataset.group);
                });
            });
            renderTable("all");

            // Buy list panel
            var meta = buy.length + " cards · " + (costKnown ? "" : "≥ ") + money(rep.total_buy_cost);
            $("brewBuyMeta").textContent = buy.length ? meta : "";
            var rowsEl = $("brewBuyRows");
            if (buy.length) {
                var MAX = 8;
                rowsEl.innerHTML = buy.map(function (b, i) {
                    return '<li' + (i >= MAX ? ' data-extra hidden' : "") + '><span class="qty">' + (b.quantity || 1) + "</span>" +
                        '<span class="hud-card-name">' + esc(b.name) + "</span>" +
                        '<span class="price' + (b.price_known ? "" : " is-unknown") + '">' +
                        (b.price_known ? money(b.unit_price) : "unpriced") + "</span></li>";
                }).join("");
                if (buy.length > MAX) {
                    var more = document.createElement("button");
                    more.type = "button";
                    more.className = "hud-btn hud-btn--ghost hud-btn--sm";
                    more.textContent = "Show all " + buy.length;
                    more.addEventListener("click", function () {
                        rowsEl.querySelectorAll("[data-extra]").forEach(function (li) { li.hidden = false; });
                        more.remove();
                    });
                    rowsEl.parentNode.insertBefore(more, rowsEl.nextSibling);
                }
                hide($("brewBuyEmpty"));
            } else {
                rowsEl.innerHTML = "";
                var empty = $("brewBuyEmpty");
                empty.className = "hud-empty";
                empty.textContent = "Nothing to buy — you own every card";
                show(empty);
            }
            if (window.TcgCart) {
                TcgCart.mount($("brewBuyCart"), buy.map(function (b) {
                    return { name: b.name, qty: b.quantity || 1 };
                }));
            }

            // Donor impact
            var impact = $("brewImpactRows");
            var projections = (rep.donor_projections || []).filter(function (d) { return d.claimed > 0; });
            impact.innerHTML = projections.length ? projections.map(function (d) {
                return "<li>" + esc(d.deck_name) + ' <span class="mono">' + d.claimed + " cards claimed · " +
                    d.size_before + " → " + d.size_after + "</span></li>";
            }).join("") : "<li>No donors named</li>";

            // Brew inputs
            var mode = session.build_mode === "best_build" ? "Best build" : "Owned-first";
            var themes = (session.themes || []).map(function (t) { return window.HUD ? HUD.chip(t, "ghost") : esc(t); }).join(" ");
            $("brewInputsKv").innerHTML =
                "<dt>Build mode</dt><dd>" + esc(mode) + "</dd>" +
                "<dt>Power</dt><dd>" + esc(pl ? "P" + pl : "—") + "</dd>" +
                "<dt>Idea</dt><dd>" + esc(session.deck_idea || "—") + "</dd>" +
                "<dt>Themes</dt><dd>" + (themes || "—") + "</dd>" +
                "<dt>Notes</dt><dd>" + esc((session.notes || "—").slice(0, 200)) + "</dd>";

            // Make physical modal (sketch only)
            $("brewPhysicalBody").innerHTML =
                projections.map(function (d) {
                    return "<p>Take " + d.claimed + " cards from " + esc(d.deck_name) +
                        ' <span class="mono">(deck goes ' + d.size_before + " → " + d.size_after + ")</span></p>";
                }).join("") +
                "<p>Take " + ownedInv.length + " cards from free inventory</p>" +
                "<p>Buy list (" + buy.length + " cards) stays unowned until purchased</p>";
        }

        function route(session) {
            if (!session) return failSession();
            if (session.status === "building" || session.status === "pending_preferences" || session.status === "failed") {
                renderBuilding(session);
            } else {
                renderDeck(session);
            }
        }

        $("brewCancelBtn").addEventListener("click", function () {
            postJSON("/api/brew/" + encodeURIComponent(sessionId) + "/abandon", {})
                .then(function () { window.location.href = "/brew"; })
                .catch(function () {});
        });
        $("brewRefreshBtn").addEventListener("click", function () {
            fetchJSON("/api/brew/" + encodeURIComponent(sessionId)).then(function (b) { route(b.session); });
        });
        $("brewRetryBtn").addEventListener("click", function () {
            $("brewFailed").hidden = true;
            postJSON("/api/brew/" + encodeURIComponent(sessionId) + "/build", {}).catch(function () {});
            pollStart = Date.now();
            fetchJSON("/api/brew/" + encodeURIComponent(sessionId)).then(function (b) { route(b.session); });
        });
        $("brewAbandon").addEventListener("click", function () {
            postJSON("/api/brew/" + encodeURIComponent(sessionId) + "/abandon", {})
                .then(function () { window.location.href = "/brew"; })
                .catch(function () {});
        });
        $("brewRebrew").addEventListener("click", function (ev) { ev.stopPropagation(); });
        $("brewExport").addEventListener("click", function () {
            fetchJSON("/api/brew/" + encodeURIComponent(sessionId) + "/drafts").then(function (b) {
                var draft = (b.drafts || [])[0] || {};
                var lines = (draft.commanders || []).map(function (c) { return "1 " + c; });
                (draft.cards || []).forEach(function (c) { lines.push((c.quantity || 1) + " " + c.name); });
                var blob = new Blob([lines.join("\n")], { type: "text/plain" });
                var a = document.createElement("a");
                a.href = URL.createObjectURL(blob);
                a.download = (draft.name || "brew") + ".txt";
                a.click();
                URL.revokeObjectURL(a.href);
            });
        });
        var modal = $("brewPhysicalModal");
        $("brewMakePhysical").addEventListener("click", function () {
            if (window.HUD && HUD.openModal) HUD.openModal(modal);
            else { modal.classList.add("is-open"); modal.setAttribute("aria-hidden", "false"); }
        });
        $("brewPhysicalCancel").addEventListener("click", function () {
            if (window.HUD && HUD.closeModal) HUD.closeModal(modal);
            else { modal.classList.remove("is-open"); modal.setAttribute("aria-hidden", "true"); }
        });

        fetchJSON("/api/brew/" + encodeURIComponent(sessionId)).then(function (b) {
            route(b.session);
        }).catch(function (err) {
            if (err.status === 404) failSession();
            else failSession();
        });
    }

    if ($("brewGenerate")) initConsole();
    else if ($("brewRoot")) initSession();
})();
