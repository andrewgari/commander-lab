"""Browser e2e for the drafts commit flow (page-specs §2.9) against a mock API.

  cd src/web && npm run build
  python src/web/tests/drafts_commit_flow.py            # starts the mock API and `node build` itself
Set CHROMIUM=/path/to/chromium to use a system browser. Needs: pip install playwright.
No Postgres/Redis: the SvelteKit server talks to an in-process mock of the /api/v2 endpoints it uses.
"""
import json, os, re, socket, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OID = {"Sol Ring": "11111111-1111-4111-8111-111111111111", "Arcane Signet": "22222222-2222-4222-8222-222222222222",
       "Atraxa, Praetors' Voice": "33333333-3333-4333-8333-333333333333", "Fire // Ice": "44444444-4444-4444-8444-444444444444"}
INST = "99999999-9999-4999-8999-999999999999"
state = {"mode": "ok", "commit": "ok", "cards": [], "moves": [], "status": "open", "posts": [], "commit_calls": 0, "search_delay": 0}

def card_hit(name):
    return {"oracle_id": OID[name], "name": name, "mana_cost": "{1}", "cmc": 1, "type_line": "Artifact", "color_identity": [],
            "rarity": "uncommon", "oracle_text": "", "image": name, "owned_qty": 4, "free_qty": 1, "can_be_commander": False, "pairing": None}

def draft_state():
    return {"draft": {"id": "draft-abc123", "kind": "combine", "status": state["status"], "name": "Gruul rebuild",
                      "source_decks": [{"id": "archidekt:123", "name": "Sliver Swarm"}, {"id": "archidekt:456", "name": "Elves"}],
                      "target_name": "Atraxa Superfriends", "target_commanders": [], "result_deck_id": "draft:draft-abc123" if state["status"] == "committed" else None,
                      "notes": "", "created_at": None, "updated_at": None, "closed_at": None},
            "cards": state["cards"], "moves": state["moves"]}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == "/api/v2/drafts/draft-abc123": return self._send(200, draft_state())
        if u.path == "/api/v2/drafts/draft-abc123/suggestions":
            return self._send(200, {"adds": [{"oracle_id": OID["Sol Ring"], "name": "Sol Ring", "tier": "own", "qty_available": 1, "score": None, "score_unavailable": True,
                                               "donor": {"deck_id": "archidekt:123", "replacement_oracle_id": "x", "replacement_name": "Mind Stone"}, "reasons": ["staple"]}],
                                   "hidden_donor_pulls": 0, "hidden_reason": None, "cuts": [], "inputs_unavailable": {}, "note": "n"})
        if u.path == "/api/v2/decks": return self._send(200, {"decks": [{"id": "archidekt:123", "name": "Sliver Swarm", "status": "have", "source": "archidekt", "archidekt_id": "123", "color_identity": [], "commanders": [], "card_qty": 100, "have_buy": None}]})
        if u.path == "/api/v2/cards/search":
            if state["search_delay"]: time.sleep(state["search_delay"])
            if state["mode"] == "fail": return self._send(502, {"detail": "boom"})
            name = (q.get("name") or [""])[0].lower()
            assert q.get("owned") == ["owned"], q
            hits = [card_hit(n) for n in OID if name and name in n.lower()]
            if state["mode"] == "empty": hits = []
            return self._send(200, {"results": hits, "page": 1, "has_more": False, "total": len(hits), "query": "", "source": "local" if state["mode"] == "degraded" else "scryfall", "degraded": state["mode"] == "degraded"})
        if u.path.startswith("/api/v2/decks/draft"): return self._send(200, {"deck": {"id": "draft:draft-abc123", "name": "Atraxa Superfriends", "status": "virtual", "color_identity": [], "commanders": [], "archidekt_id": None}, "boards": {}, "have_buy": {"totals": {"have": 0, "need": 0, "have_pct": None, "pending": 0, "buy": 0, "complete": False}, "lines": []}, "snapshot": None})
        self._send(404, {"detail": "nope"})
    def do_POST(self):
        u = urlparse(self.path); n = int(self.headers.get("content-length") or 0); body = json.loads(self.rfile.read(n) or b"{}")
        state["posts"].append((u.path, body))
        if u.path.endswith("/cards"):
            if body["board"] not in ("commander", "main", "sideboard", "maybe", "considering"):
                return self._send(400, {"detail": {"reason": "board must be one of [...]"}})
            name = next(k for k, v in OID.items() if v == body["oracle_id"])
            state["cards"] = [c for c in state["cards"] if not (c["oracle_id"] == body["oracle_id"] and c["board"] == body["board"])]
            state["cards"].append({"oracle_id": body["oracle_id"], "name": name, "qty": body["qty"], "board": body["board"], "category": None, "type_line": "Artifact", "cmc": 1, "color_identity": [], "image_uri": ""})
            return self._send(200, draft_state())
        if u.path.endswith("/commit"):
            state["commit_calls"] += 1
            if state["commit"] == "409": return self._send(409, {"detail": {"reason": "draft is committed, not open"}})
            state["status"] = "committed"; return self._send(200, {"draft_id": "draft-abc123", "deck_id": "draft:draft-abc123", "cards_written": 1, "moves_applied": 1, "events_written": 1})
        if u.path.endswith("/abandon"): return self._send(200, {"draft_id": "draft-abc123", "staged_cards_deleted": 1, "staged_moves_deleted": 1})
        if u.path.endswith("/moves"): return self._send(200, draft_state())
        self._send(404, {})
    def do_DELETE(self): self._send(200, draft_state())

def free_port():
    with socket.socket() as s: s.bind(("127.0.0.1", 0)); return s.getsockname()[1]

fails = []
def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok: fails.append(msg)

def reset(**kw):
    state.update({"mode": "ok", "commit": "ok", "cards": [], "moves": [], "status": "open", "posts": [], "commit_calls": 0, "search_delay": 0}); state.update(kw)

def main():
    api_port, web_port = free_port(), free_port()
    api = ThreadingHTTPServer(("127.0.0.1", api_port), H); threading.Thread(target=api.serve_forever, daemon=True).start()
    env = dict(os.environ, PORT=str(web_port), HOST="127.0.0.1", COMMANDER_API_URL=f"http://127.0.0.1:{api_port}", ORIGIN=f"http://127.0.0.1:{web_port}")
    web = subprocess.Popen(["node", "build"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{web_port}"
    try:
        for _ in range(60):
            try: socket.create_connection(("127.0.0.1", web_port), 0.2).close(); break
            except OSError: time.sleep(0.25)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=os.environ.get("CHROMIUM") or None)
            ctx = browser.new_context(viewport={"width": 1280, "height": 900}); page = ctx.new_page()
            errors = []; page.on("pageerror", lambda e: errors.append(str(e)))
            if os.environ.get("DEBUG"): page.on("console", lambda m: print("console:", m.text)); page.on("request", lambda r: print("req", r.method, r.url, r.all_headers().get("origin"), r.all_headers().get("host")) if r.method == "POST" else None); page.on("response", lambda r: print("resp", r.status, r.url, r.text()[:200] if r.status >= 400 else ""))
            page.on("dialog", lambda d: (errors.append("window dialog: " + d.message), d.dismiss()))
            url = f"{base}/drafts/draft-abc123"
            combo = lambda: page.locator("input[role=combobox]")

            # --- empty draft: nothing staged, commit blocked with a reason
            reset(); page.goto(url); page.wait_for_load_state("networkidle")
            check(page.get_by_text("Nothing staged yet").count() == 1, "empty draft shows 'Nothing staged yet'")
            btn = page.get_by_role("button", name="Review & commit…")
            check(btn.get_attribute("aria-disabled") == "true" and "Stage at least one card" in page.locator("#commit-blocked").inner_text(), "Commit is aria-disabled with its reason")
            btn.click(force=True); page.wait_for_timeout(200)
            check(page.locator("dialog[open]").count() == 0, "blocked Commit does not open the dialog")

            # --- AC1: type, Enter adds the top owned match; focus stays; status announces; qty prefix; increments
            combo().fill("sol"); page.wait_for_selector("[role=option]")
            print("OPT", page.get_by_role("option").all_inner_texts()) if os.environ.get("DEBUG") else None; check(page.get_by_role("option").count() == 1 and "Own 4 · 1 free" in page.get_by_role("option").first.inner_text(), "listbox shows the owned match with Own/free counts")
            check(combo().get_attribute("aria-expanded") == "true" and combo().get_attribute("aria-controls"), "combobox exposes aria-expanded/aria-controls")
            combo().press("Enter"); page.wait_for_function("document.querySelector('[role=status]') && [...document.querySelectorAll('[role=status]')].some(e => /Added 1× Sol Ring to Main/.test(e.textContent))")
            check(page.evaluate("document.activeElement.getAttribute('role')") == "combobox" and combo().input_value() == "", "input cleared and keeps focus after add")
            check(page.get_by_role("link", name="Sol Ring").count() == 1, "staged table shows Sol Ring")
            combo().fill("2x sol"); page.wait_for_selector("[role=option]"); combo().press("Enter")
            page.wait_for_function("[...document.querySelectorAll('[role=status]')].some(e => /Added 2× Sol Ring to Main \\(now 3\\)/.test(e.textContent))")
            check(state["cards"][0]["qty"] == 3, f"adding again increments (qty {state['cards'][0]['qty']})")

            # --- AC4: Maybeboard stages as `maybe`
            page.get_by_label("Board", exact=True).select_option("maybeboard")
            combo().fill("arcane"); page.wait_for_selector("[role=option]"); combo().press("Enter")
            page.wait_for_function("[...document.querySelectorAll('[role=status]')].some(e => /Arcane Signet to Maybeboard/.test(e.textContent))")
            check(any(c["board"] == "maybe" for c in state["cards"]) and not any(b.get("board") == "maybeboard" for _, b in state["posts"]), "Maybeboard posts board=maybe")
            page.get_by_label("Board", exact=True).select_option("main")

            # --- AC2: keyboard-only: Ctrl+', type, ↓↓, Enter, Esc, Esc
            page.locator("h1").click(); page.keyboard.press("Control+'")
            check(page.evaluate("document.activeElement.getAttribute('role')") == "combobox", "Ctrl+' focuses the combobox")
            combo().fill("a"); page.wait_for_timeout(400)
            check(page.get_by_role("option").count() == 0, "1 character does not search")
            combo().fill("r"); combo().fill("ri"); page.wait_for_selector("[role=option]")
            n = page.get_by_role("option").count()
            combo().press("ArrowDown"); a1 = combo().get_attribute("aria-activedescendant"); combo().press("ArrowDown"); a2 = combo().get_attribute("aria-activedescendant")
            check(a1 and a2 and (a1 != a2 or n == 1), f"↓ moves aria-activedescendant ({n} options)")
            combo().press("Escape"); check(combo().get_attribute("aria-expanded") == "false" and combo().input_value() == "ri", "first Esc closes the list, keeps the text")
            combo().press("Escape"); check(combo().input_value() == "", "second Esc clears the input")

            # --- AC3: four non-result rows; failure never says 'No owned cards match'
            reset(mode="empty"); page.reload(); page.wait_for_load_state("networkidle"); combo().fill("zzz")
            page.get_by_text("No owned cards match").wait_for(); check("Draft staging only offers cards you own" in page.locator("[role=listbox]").inner_text(), "0 hits -> 'No owned cards match' + hint")
            reset(mode="fail"); page.reload(); page.wait_for_load_state("networkidle"); combo().fill("sol")
            page.get_by_text("Search failed — press Enter to retry").wait_for()
            check(page.get_by_text("No owned cards match").count() == 0, "502 -> 'Search failed', never 'No owned cards match'")
            state["mode"] = "ok"; combo().press("Enter"); page.wait_for_selector("[role=option]:has-text('Sol Ring')")
            check(True, "Enter on the failure row retries")
            reset(mode="degraded"); page.reload(); page.wait_for_load_state("networkidle"); combo().fill("sol")
            page.get_by_text("Card search is offline — showing local matches").wait_for(); check(True, "degraded:true -> offline header row")
            reset(search_delay=1.0); page.reload(); page.wait_for_load_state("networkidle"); combo().fill("sol")
            page.get_by_text("Searching…").wait_for(timeout=3000); check(True, "delayed response -> 'Searching…' row")

            # --- AC5: no raw ids anywhere (disclosure closed), incl. a staged move
            reset(cards=[{"oracle_id": OID["Atraxa, Praetors' Voice"], "name": "Atraxa, Praetors' Voice", "qty": 1, "board": "commander", "category": None, "type_line": "Legendary Creature", "cmc": 4, "color_identity": ["W", "U", "B", "G"], "image_uri": ""},
                          {"oracle_id": OID["Sol Ring"], "name": "Sol Ring", "qty": 99, "board": "main", "category": None, "type_line": "Artifact", "cmc": 1, "color_identity": [], "image_uri": ""}],
                  moves=[{"instance_id": INST, "action": "assign", "from_deck": "archidekt:123", "oracle_id": OID["Sol Ring"], "card_name": "Sol Ring", "status": "in_deck", "deck_id": "archidekt:123"}])
            page.reload(); page.wait_for_load_state("networkidle")
            visible = page.evaluate("""() => { const out = [document.body.innerText]; document.querySelectorAll('[aria-label],[aria-labelledby]').forEach(e => out.push(e.getAttribute('aria-label') || '')); document.querySelectorAll('button,a,input,select').forEach(e => { if (!e.closest('details:not([open])')) out.push(e.getAttribute('aria-label') || '', e.getAttribute('placeholder') || ''); }); return out.join('\\n'); }""")
            # innerText omits closed <details> content; only the disclosure may hold an id input
            check(not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}|archidekt:|draft:|draft-abc", visible), "no oracle/instance/deck ids in visible text or aria-labels")
            check("Sliver Swarm" in visible and "Back to collection" not in visible and "Into new deck" in visible, "moves list shows deck name and move label")

            # --- AC6/7: dialog: modal, focus on Cancel, summary, Esc returns focus, backdrop doesn't close
            trigger = page.get_by_role("button", name="Review & commit…"); trigger.focus(); trigger.click()
            dlg = page.locator("dialog[open]"); dlg.wait_for()
            check(page.evaluate("document.querySelector('dialog[open]').matches(':modal')"), "dialog is modal (showModal)")
            check(page.evaluate("document.activeElement.textContent.trim()") == "Cancel", "initial focus is Cancel")
            name = page.evaluate("document.querySelector('dialog[open]').getAttribute('aria-labelledby')")
            check(page.locator(f"#{name}").inner_text() == "Commit draft?" and bool(page.evaluate("document.querySelector('dialog[open]').getAttribute('aria-describedby')")), "dialog is labelled by its heading and described by its body")
            txt = dlg.inner_text()
            for want in ("Creates Atraxa Superfriends", "100 cards: 1 commander · 99 main", "Atraxa, Praetors' Voice", "1 copy moves into the new deck", "Pulls 1 copy out of Sliver Swarm", "Built (have)", "cannot be undone"):
                check(want in txt, f"summary contains '{want}'")
            check(not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}|archidekt:|draft:", txt), "summary has no ids")
            page.mouse.click(5, 5); page.wait_for_timeout(150)
            check(page.locator("dialog[open]").count() == 1, "backdrop click does not close")
            page.keyboard.press("Escape"); page.wait_for_timeout(150)
            check(page.locator("dialog[open]").count() == 0 and page.evaluate("document.activeElement.textContent.trim()") == "Review & commit…", "Esc closes and focus returns to the trigger")
            check(state["commit_calls"] == 0, "closing did not commit")

            # --- AC7: failure keeps dialog open with an alert and focus on Commit; success redirects
            reset(commit="409", cards=state["cards"], moves=state["moves"]); page.reload(); page.wait_for_load_state("networkidle")
            page.get_by_role("button", name="Review & commit…").click(); page.locator("dialog[open]").wait_for()
            page.get_by_role("button", name="Commit draft").click(); page.get_by_role("alert").filter(has_text="Commit failed").wait_for()
            check("draft is committed, not open" in page.get_by_role("alert").filter(has_text="Commit failed").inner_text(), "409 -> alert with the server reason")
            check(page.locator("dialog[open]").count() == 1 and page.evaluate("document.activeElement.textContent.trim()") == "Commit draft", "dialog stays open, focus on Commit")
            check(page.get_by_role("button", name="Close and refresh the draft").count() == 1, "changed-world error offers refresh")
            state["commit"] = "ok"; page.get_by_role("button", name="Commit draft").click()
            page.wait_for_url(re.compile(r"/decks/draft%3Adraft-abc123\?from_draft=1")); check(True, "success redirects to /decks/{deck_id}?from_draft=1")
            check(page.get_by_text("Built from a draft.").count() == 1, "deck page shows the 'Built from a draft' status")

            # --- AC8: Abandon has its own alertdialog and redirects to /drafts
            reset(cards=[{"oracle_id": OID["Sol Ring"], "name": "Sol Ring", "qty": 2, "board": "main", "category": None, "type_line": "Artifact", "cmc": 1, "color_identity": [], "image_uri": ""}])
            page.goto(url); page.wait_for_load_state("networkidle")
            ab = page.get_by_role("button", name="Abandon draft…"); cm = page.get_by_role("button", name="Review & commit…")
            b1, b2 = ab.bounding_box(), cm.bounding_box(); gap = b1["x"] - (b2["x"] + b2["width"])
            check(gap >= 32, f"Commit and Abandon are ≥2rem apart ({gap:.0f}px)")
            ab.click(); page.locator("dialog[open]").wait_for()
            check(page.locator("dialog[open]").get_attribute("role") == "alertdialog" and "Deletes 2 staged cards and 0 staged moves" in page.locator("dialog[open]").inner_text(), "Abandon is an alertdialog with counts")
            page.get_by_role("button", name="Abandon draft", exact=True).click(); page.wait_for_url(re.compile(r"/drafts$")); check(True, "Abandon redirects to /drafts")

            # --- AC10: no-JS: Commit renders the inline summary; only confirmed=1 commits
            reset(cards=[{"oracle_id": OID["Sol Ring"], "name": "Sol Ring", "qty": 2, "board": "main", "category": None, "type_line": "Artifact", "cmc": 1, "color_identity": [], "image_uri": ""}])
            nojs = browser.new_context(java_script_enabled=False, viewport={"width": 1280, "height": 900}).new_page()
            nojs.goto(url); nojs.get_by_role("button", name="Review & commit…").click(); nojs.wait_for_load_state()
            check(nojs.get_by_role("heading", name="Commit draft?").count() == 1 and state["commit_calls"] == 0, "no-JS: first POST shows the inline summary and does not commit")
            nojs.get_by_role("button", name="Commit draft", exact=True).click(); nojs.wait_for_load_state()
            check(state["commit_calls"] == 1 and "from_draft=1" in nojs.url, "no-JS: second POST (confirmed=1) commits and redirects")
            reset(); r = nojs.request.post(url + "?/commit", form={}, headers={"origin": base}); check(state["commit_calls"] == 0, f"a stray unconfirmed POST does not commit (status {r.status})")
            nojs.goto(url)
            nojs.get_by_label("Card name").fill("sol ring"); nojs.get_by_role("button", name="Add card").click(); nojs.wait_for_load_state()
            check(any(c["name"] == "Sol Ring" for c in state["cards"]), "no-JS add-by-name stages the exact owned match")
            nojs.get_by_label("Card name").fill("sol"); nojs.get_by_role("button", name="Add card").click(); nojs.wait_for_load_state()
            check("No owned card named" in nojs.content(), "no-JS add-by-name refuses a non-exact name")

            # --- 375px: no horizontal page scroll with the dialog open and closed
            m = browser.new_context(viewport={"width": 375, "height": 812}).new_page()
            reset(cards=[{"oracle_id": OID["Fire // Ice"], "name": "Fire // Ice", "qty": 1, "board": "main", "category": None, "type_line": "Instant // Instant", "cmc": 4, "color_identity": ["U", "R"], "image_uri": ""}])
            m.goto(url); m.wait_for_load_state("networkidle")
            check(m.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "375px: no page-level horizontal scroll")
            m.get_by_role("button", name="Review & commit…").click(); m.locator("dialog[open]").wait_for()
            check(m.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "375px: no horizontal scroll with the dialog open")

            check(not errors, f"no page errors / window.confirm {errors}")
            browser.close()
    finally:
        web.terminate(); api.shutdown()
    print(f"\n{len(fails)} failure(s)"); sys.exit(1 if fails else 0)

main()
