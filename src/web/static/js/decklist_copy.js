/* Copy decklist control (deck pages, partials/deck_head.html).
 *
 * One click fetches the decklist as paste/import text from
 * GET /api/deck/{name}/decklist (rendered server-side by
 * decklist_export.export_decklist_text — no format logic lives here) and
 * copies it via the Clipboard API. The last chosen format persists in
 * localStorage and is preselected on the next visit. Success shows a
 * transient "Copied!" beside the button; any failure (HTTP, clipboard
 * denied, insecure context) shows the error and reveals a select-all
 * textarea with the text for manual copying.
 */
(function () {
    "use strict";

    var STORE_KEY = "commanderLab.decklistCopyFormat";
    var FORMATS = ["plain", "archidekt", "moxfield", "forge", "tcgplayer"];
    var MSG_OK_MS = 2500;

    function $(id) { return document.getElementById(id); }

    function init() {
        var root = $("copyDeck");
        if (!root) return;  // not a deck page (or deck not found)
        var deckName = root.getAttribute("data-deck-name") || "";
        var btn = $("copyDeckBtn");
        var optBtn = $("copyDeckOptBtn");
        var panel = $("copyDeckPanel");
        var select = $("copyDeckFormat");
        var sideboard = $("copyDeckSideboard");
        var maybeboard = $("copyDeckMaybeboard");
        var printing = $("copyDeckPrinting");
        var msg = $("copyDeckMsg");
        var fallback = $("copyDeckFallback");
        var fallbackNote = $("copyDeckFallbackNote");
        var fallbackText = $("copyDeckText");
        var msgTimer = null;

        // Persisted format (default Plain). Toggles always start off.
        try {
            var saved = localStorage.getItem(STORE_KEY);
            if (saved && FORMATS.indexOf(saved) !== -1) select.value = saved;
        } catch (e) { /* private mode: keep the default */ }
        select.addEventListener("change", function () {
            try { localStorage.setItem(STORE_KEY, select.value); } catch (e) { /* ignore */ }
        });

        optBtn.addEventListener("click", function () {
            var open = panel.hidden;
            panel.hidden = !open;
            optBtn.setAttribute("aria-expanded", open ? "true" : "false");
        });

        function showMsg(text, isError) {
            msg.textContent = text;
            msg.classList.toggle("is-error", !!isError);
            if (msgTimer) { clearTimeout(msgTimer); msgTimer = null; }
            if (!isError) {
                msgTimer = setTimeout(function () { msg.textContent = ""; }, MSG_OK_MS);
            }
        }

        function fail(reason, text) {
            showMsg(reason + " — copy manually below.", true);
            if (text != null) {
                fallbackNote.textContent = reason + " — select all and copy manually:";
                fallback.hidden = false;
                panel.hidden = false;
                optBtn.setAttribute("aria-expanded", "true");
                fallbackText.value = text;
                fallbackText.focus();
                fallbackText.select();
            }
        }

        function copyText(text) {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                return navigator.clipboard.writeText(text).then(
                    function () {
                        fallback.hidden = true;
                        showMsg("Copied!");
                    },
                    function () { fail("Clipboard copy failed", text); }
                );
            }
            fail("Clipboard API unavailable in this browser/context", text);
            return Promise.resolve();
        }

        btn.addEventListener("click", function () {
            var params = new URLSearchParams({
                format: select.value,
                include_sideboard: sideboard.checked ? "true" : "false",
                include_maybeboard: maybeboard.checked ? "true" : "false",
                include_printing: printing.checked ? "true" : "false"
            });
            btn.disabled = true;
            fetch("/api/deck/" + encodeURIComponent(deckName) + "/decklist?" + params.toString())
                .then(function (res) {
                    return res.json().then(function (body) {
                        if (!res.ok) throw new Error((body && body.error) || ("Export failed (HTTP " + res.status + ")"));
                        return body.text || "";
                    });
                })
                .then(copyText)
                .catch(function (err) { fail(err && err.message ? err.message : "Export failed", null); })
                .finally(function () { btn.disabled = false; });
        });

        fallbackText.addEventListener("click", function () { fallbackText.select(); });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
