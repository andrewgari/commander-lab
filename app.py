from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import redis
import os
import asyncio
import logging
import github_sync
import json
import time
import html
from typing import List, Optional

import instances as instance_store
from instances import InstanceError
import registry
import commander_roles
import inventory_view
from providers import fetch_deck, ProviderError
import linked_accounts
from linked_accounts import LinkedAccountError
import deck_links
from deck_links import DeckLinkError
import deck_sync
from deck_sync import DeckSyncError
from version import __version__, VERSION
import httpx
from repo_status import enrich_decks_with_repo_status
from analytics.interactions import service as interactions_service
from analytics.interactions import combos
from analytics import composition as composition_mod
from analytics import identity as identity_mod
from analytics import insights
from analytics import journal as journal_mod
from analytics import tuner as tuner_mod
from analytics import bracket_worker
from analytics import brackets as brackets_mod
from analytics import engine as engine_mod
from analytics import personality_profile, personality_store
import urllib.parse

app = FastAPI()

github_sync_lock = False

@app.middleware("http")
async def github_sync_middleware(request: Request, call_next):
    global github_sync_lock
    version = None
    try:
        version = r.get(github_sync.CACHE_VERSION_KEY)
    except redis.exceptions.RedisError:
        pass
        
    if not version:
        if not github_sync_lock:
            github_sync_lock = True
            
            async def run_sync():
                global github_sync_lock
                try:
                    await github_sync.update_github_data(r)
                finally:
                    github_sync_lock = False
                    
            asyncio.create_task(run_sync())
            
        try:
            version = r.get(github_sync.STALE_VERSION_KEY)
        except redis.exceptions.RedisError:
            pass
            
        if not version:
            from version import __version__
            version = __version__
            
    request.state.app_version = version
    return await call_next(request)

templates = Jinja2Templates(directory="templates")
templates.env.globals["app_version"] = __version__
templates.env.globals["version"] = __version__

# Deck groups in the sidebar "Commander Decks" menu: each opens /decks filtered
# to one existing status value (registry.VALID_STATUSES). Labels only; the
# status values and their labels elsewhere in the app are unchanged.
DECK_GROUPS = [
    {"status": "physical", "label": "Physical Decks"},
    {"status": "digital", "label": "Virtual Decks"},
    {"status": "testing", "label": "WIP Decks"},
    {"status": "retired", "label": "Retired Decks"},
]
templates.env.globals["deck_groups"] = DECK_GROUPS

# Shared HUD stylesheet and other static assets (see docs/DESIGN_TOKENS.md).
app.mount("/static", StaticFiles(directory="static"), name="static")

# Connect to Redis
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
r = redis.from_url(redis_url, decode_responses=True)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="home.html")

@app.get("/decks", response_class=HTMLResponse)
async def decks(request: Request, status: Optional[str] = None):
    providers = [p.strip() for p in os.getenv("ENABLED_PROVIDERS", "archidekt,moxfield,commandersalt").split(",") if p.strip()]
    # ?status=<value> shows one deck group; an unknown value falls back to all decks.
    wanted = (status or "").strip().lower()
    group = next((g for g in DECK_GROUPS if g["status"] == wanted), None)
    return templates.TemplateResponse(request=request, name="decks.html", context={
        "providers": providers,
        "status_filter": group["status"] if group else None,
        "status_label": group["label"] if group else None,
    })

@app.get("/inventory", response_class=HTMLResponse)
async def inventory(request: Request):
    return templates.TemplateResponse(request=request, name="inventory.html")

@app.get("/tags", response_class=HTMLResponse)
async def tags(request: Request):
    return templates.TemplateResponse(request=request, name="tags.html")


@app.get("/instances", response_class=HTMLResponse)
async def instances_view(request: Request):
    """Instance list view: ownership source of truth for physical card copies."""
    return templates.TemplateResponse(request=request, name="instances.html")


@app.get("/profile", response_class=HTMLResponse)
async def profile_view(request: Request, scope: str = "physical"):
    """Player profile (PS-4): the average of every deck's ability scores, trend
    callouts and a per-deck table. Rendered client-side from
    /api/profile/personality; ?scope=physical (default) | all."""
    if scope not in ("physical", "all"):
        scope = "physical"
    return templates.TemplateResponse(request=request, name="profile.html", context={"scope": scope})


@app.get("/settings", response_class=HTMLResponse)
async def settings_view(request: Request):
    """Settings page. Sync section: linked Archidekt/Moxfield accounts (/api/linked-accounts)."""
    return templates.TemplateResponse(request=request, name="settings.html")


@app.get("/styleguide", response_class=HTMLResponse)
async def styleguide(request: Request):
    """Living reference for the HUD tokens and component kit (static sample data)."""
    return templates.TemplateResponse(request=request, name="styleguide.html")

@app.get("/deck/{deck_name}", response_class=HTMLResponse)
async def deck_view(request: Request, deck_name: str):
    providers = [p.strip() for p in os.getenv("ENABLED_PROVIDERS", "archidekt,moxfield,commandersalt").split(",") if p.strip()]
    head = await asyncio.to_thread(_deck_head_context, deck_name)
    return templates.TemplateResponse(request=request, name="deck.html", context={
        "providers": providers, "deck_name": deck_name, "head": head, "tab": "overview",
        "power_tol": engine_mod.POWER_ALIGN_TOLERANCE})  # BR-6: the power_aligned threshold, shown as-is


def _deck_head_context(deck_name: str) -> dict:
    """Shared deck header + tabs (partials/deck_head.html, UX_SPEC §3.0).
    Never raises: an unknown deck (or Redis being down) gives a bare header."""
    out = {"name": deck_name, "found": False, "commanders": [], "colors": "", "status": None,
           "art_uid": None, "commander_roles": [], "cards": None, "lands": None, "pillars": None, "tune": None, "ideas": None, "registry_id": None}
    try:
        deck = next((d for d in registry.list_decks(r) if d.get("name") == deck_name), None)
    except Exception:
        deck = None
    if not deck:
        return out
    out.update(found=True, commanders=deck.get("commanders") or [], status=deck.get("status") or None,
               colors="" if deck.get("color") == "C" else (deck.get("color") or ""),
               art_uid=(deck.get("commander_uids") or [None])[0], registry_id=registry.registry_id_of(deck))
    try:
        out["commander_roles"] = commander_roles.roles_for_decks(r, [dict(deck)])[0]["commander_roles"]
    except Exception:
        out["commander_roles"] = commander_roles.deck_commanders(deck, {})
    cards = deck.get("cards") or []
    out["cards"] = sum(int(c.get("quantity") or 1) for c in cards) or None
    try:
        rows = inventory_view.build_inventory(r, decks=[deck_name])
        land_names = {c.get("name") for c in rows if "Land" in (c.get("type") or "")}
        out["lands"] = sum(int(c.get("quantity") or 1) for c in cards if c.get("name") in land_names)
    except Exception:
        pass
    try:
        out["pillars"] = len(identity_mod.live_pillars(identity_mod.load_identity(r, deck))) or None
    except Exception:
        pass
    try:
        out["tune"] = tuner_mod.open_count(r, deck) or None
    except Exception:
        pass
    try:
        from analytics import theory as theory_mod
        out["ideas"] = int(r.zcard(theory_mod.index_key(out["registry_id"]))) or None
    except Exception:
        pass
    return out


async def _deck_tab(request: Request, deck_name: str, template: str, tab: str, extra=None):
    head = await asyncio.to_thread(_deck_head_context, deck_name)
    if not head["found"]:
        return HTMLResponse(
            "<html><head><title>Deck Not Found</title></head><body><h1>Deck Not Found</h1>"
            "<p>No deck named &quot;{}&quot; could be found.</p></body></html>".format(html.escape(deck_name)),
            status_code=404)
    ctx = {"deck_name": deck_name, "deck_id": head["registry_id"], "head": head, "tab": tab}
    if extra:
        ctx.update(await asyncio.to_thread(extra, deck_name))
    return templates.TemplateResponse(request=request, name=template, context=ctx)


@app.get("/deck/{deck_name}/stats", response_class=HTMLResponse)
async def deck_stats_view(request: Request, deck_name: str):
    """Deck Stats (UX_SPEC §3.1): interaction graph, shape, identity, combos. Renders from JSON."""
    return await _deck_tab(request, deck_name, "deck_stats.html", "stats")


@app.get("/deck/{deck_name}/identity", response_class=HTMLResponse)
async def deck_identity_view(request: Request, deck_name: str):
    """Thesis + pillars editor (UX_SPEC §3.2)."""
    return await _deck_tab(request, deck_name, "deck_identity.html", "identity")


@app.get("/deck/{deck_name}/journal", response_class=HTMLResponse)
async def deck_journal_view(request: Request, deck_name: str):
    """Deck Journal form; ?review=1 opens the check-in above it (UX_SPEC §3.3)."""
    return await _deck_tab(request, deck_name, "deck_journal.html", "journal", extra=_journal_page_context)


@app.get("/deck/{deck_name}/tune", response_class=HTMLResponse)
async def deck_tune_view(request: Request, deck_name: str):
    """Tune (UX_SPEC §3.4): shape fixes, swaps, weak performers, change list. Renders from JSON."""
    return await _deck_tab(request, deck_name, "deck_tune.html", "tune")


@app.get("/deck/{deck_name}/ideas", response_class=HTMLResponse)
async def deck_ideas_view(request: Request, deck_name: str):
    """Ideas (TH-3, THEORY_SPEC): past theory docs + the New theory form."""
    return await _deck_tab(request, deck_name, "deck_ideas.html", "ideas", extra=_ideas_page_context)


def _ideas_page_context(deck_name: str) -> dict:
    """Theory summaries, newest first. Never raises (Redis down = empty list + a note)."""
    from analytics import theory as theory_mod
    from analytics import theory_view

    out = {"docs": [], "list_error": False, "tv": theory_view}
    try:
        deck = next((d for d in registry.list_decks(r) if d.get("name") == deck_name), None)
        if deck:
            out["docs"] = theory_mod.list_docs(r, _theory_deck_id(deck))
    except Exception:
        out["list_error"] = True
    return out


@app.get("/deck/{deck_name}/ideas/{doc_id}", response_class=HTMLResponse)
async def deck_theory_view(request: Request, deck_name: str, doc_id: str):
    """One theory doc: one partial per section type, generic fallback for unknown types."""
    from analytics import theory as theory_mod
    from analytics import theory_view

    head = await asyncio.to_thread(_deck_head_context, deck_name)
    unavailable = HTMLResponse(
        "<html><head><title>Theory unavailable</title></head><body><h1>Theory unavailable</h1>"
        "<p>Storage is unreachable right now. Try again in a moment.</p></body></html>", status_code=503)
    if not head["found"]:
        # _deck_head_context hides registry errors: tell "no such deck" from "storage down".
        try:
            await asyncio.to_thread(registry.list_decks, r)
        except Exception:
            return unavailable
    doc = None
    if head["found"] and head["registry_id"]:
        try:
            doc = await asyncio.to_thread(theory_mod.load, r, head["registry_id"], doc_id)
        except Exception:
            return unavailable
    if not doc:
        return HTMLResponse(
            "<html><head><title>Theory Not Found</title></head><body><h1>Theory Not Found</h1>"
            "<p>No theory &quot;{}&quot; for deck &quot;{}&quot;. <a href=\"/deck/{}/ideas\">Back to Ideas</a></p>"
            "</body></html>".format(html.escape(doc_id), html.escape(deck_name), urllib.parse.quote(deck_name)),
            status_code=404)
    return templates.TemplateResponse(request=request, name="deck_theory.html", context={
        "deck_name": deck_name, "deck_id": head["registry_id"], "head": head, "tab": "ideas", "doc": doc,
        "meta": theory_view.doc_meta(doc), "sections": theory_view.sections(doc), "tv": theory_view})


def _journal_page_context(deck_name: str) -> dict:
    """Server-rendered Journal form data. Never raises (Redis down = empty form)."""
    out = {"j": journal_mod.public(None), "exists": False, "caps": None, "deck_cards": [], "pillars": [],
           "money": journal_mod.money, "wincons": list(journal_mod.WINCON_ORDER),
           "wincon_labels": journal_mod.WINCON_SHORT, "intents": list(journal_mod.INTENT_LABELS.items()),
           "brackets": [(b.number, b.name) for b in brackets_mod.BRACKETS],
           "power_tol": engine_mod.POWER_ALIGN_TOLERANCE}
    defaults = dict(journal_mod.DEFAULT_CAPS)
    try:
        deck = next((d for d in registry.list_decks(r) if d.get("name") == deck_name), None)
        if deck:
            out["deck_cards"] = sorted(journal_mod.deck_entries(deck))
            view = journal_mod.journal_view(r, deck)
            out.update(j=view["journal"], exists=view["exists"], caps=view["caps"])
            defaults = view["caps"]["defaults"]
            out["pillars"] = [p.get("name") for p in identity_mod.live_pillars(identity_mod.load_identity(r, deck))]
    except Exception:
        pass
    out["defaults"] = defaults
    return out

@app.get("/deck/{deck_name}/manage", response_class=HTMLResponse)
async def deck_manage_view(request: Request, deck_name: str):
    """Deck management view (see docs/DECK_MANAGEMENT_VIEW.md).

    /deck/{deck_name} (the read-only view) keys off the deck's display
    `name`, not its registry_id — but the /api/decks/{id}/manage aggregate
    and sibling manage endpoints all key off registry_id. Resolve name ->
    registry_id here so the template only ever has to call the registry_id
    APIs, matching how the rest of the manage API surface works.
    """
    decks = registry.list_decks(r)
    deck = next((d for d in decks if d.get("name") == deck_name), None)
    if not deck:
        return HTMLResponse(
            "<html><head><title>Deck Not Found</title></head>"
            "<body><h1>Deck Not Found</h1>"
            "<p>No deck named &quot;{}&quot; could be found.</p>"
            "</body></html>".format(html.escape(deck_name)),
            status_code=404,
        )
    deck_id = registry.registry_id_of(deck)
    return templates.TemplateResponse(request=request, name="deck_manage.html", context={"deck_id": deck_id, "deck_name": deck_name})

@app.get("/api/version")
async def get_version(request: Request):
    """Return the application version information."""
    return {"version": getattr(request.state, "app_version", "unknown")}



def extract_short_description(body: str | None, max_length: int = 280) -> str:
    """Extract a concise summary from a GitHub release body."""
    if not body:
        return ""
    lines = [line.strip() for line in body.strip().splitlines()]
    # Ignore markdown headings
    content_lines = [line for line in lines if line and not line.startswith("#")]
    if not content_lines:
        content_lines = [line for line in lines if line]
    if not content_lines:
        return ""
    text = " ".join(content_lines)
    if len(text) > max_length:
        truncated = text[:max_length].rsplit(" ", 1)[0]
        return f"{truncated}..."
    return text


@app.get("/changelog", response_class=HTMLResponse)
async def changelog(request: Request):
    """Display a scrollable changelog page pulled from GitHub releases."""
    releases = None
    error_msg = None

    # Try active cache first
    cached = None
    try:
        cached = r.get(github_sync.CACHE_CHANGELOG_KEY)
    except redis.exceptions.RedisError:
        pass
        
    if not cached:
        # Trigger background sync if not running
        global github_sync_lock
        if not github_sync_lock:
            github_sync_lock = True
            async def run_sync():
                global github_sync_lock
                try:
                    await github_sync.update_github_data(r)
                finally:
                    github_sync_lock = False
            asyncio.create_task(run_sync())
            
        try:
            cached = r.get(github_sync.STALE_CHANGELOG_KEY)
        except redis.exceptions.RedisError:
            pass

    if cached:
        try:
            releases = json.loads(cached)
        except Exception:
            pass

    if not releases:
        error_msg = "Changelog is currently unavailable. Please check back later."

    # Format releases with tag, date, and short description
    formatted_releases = []
    if releases:
        for rel in releases:
            rel_dict = dict(rel)
            body = rel.get("body") or ""
            rel_dict["short_description"] = extract_short_description(body)
            pub_date = rel.get("published_at")
            rel_dict["release_date"] = pub_date[:10] if pub_date else "Unknown date"
            formatted_releases.append(rel_dict)

    return templates.TemplateResponse(
        request=request,
        name="changelog.html",
        context={"releases": formatted_releases, "error_msg": error_msg},
    )

@app.get("/api/decks")
async def get_decks():
    decks_json = r.get("decks")
    decks = json.loads(decks_json) if decks_json else []
    decks = enrich_decks_with_repo_status(decks)
    for d in decks:
        d["status_locked"] = registry.is_locked(d)
    try:
        commander_roles.roles_for_decks(r, decks)
    except Exception:  # Redis hiccup: views fall back to plain commanders
        for d in decks:
            d["commander_roles"] = commander_roles.deck_commanders(d, {})
    return {"decks": decks}

@app.post("/api/decks/{deck_id}/status")
async def update_deck_status(deck_id: str, request: Request):
    """Update a deck's lifecycle status: digital | testing | retired.

    Physical is never set here: a deck is physical only while it is in the
    Archidekt physical folder (registry.physical_folder_id(), synced by
    sync.py / linked-account sync). Physical decks are locked. Errors:
    400 bad body or a status that isn't allowed (including physical),
    404 unknown deck, 409 locked deck.
    """
    try:
        data = await request.json()
    except Exception:
        data = None
    if not isinstance(data, dict):
        return JSONResponse(status_code=400, content={"success": False, "error": "Request body must be a JSON object"})
    status = data.get("status")
    try:
        result = registry.set_user_status(r, deck_id, status)
    except registry.StatusLockedError as e:
        return JSONResponse(status_code=409, content={"success": False, "locked": True, "error": str(e)})
    except registry.StatusNotAllowedError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})
    except ValueError as e:
        return JSONResponse(status_code=404, content={"success": False, "error": str(e)})
    return {"success": True, "status": status, "auto_bind": result["auto_bind"],
            "pending_removal": result["pending_removal"]}


@app.post("/api/import")
async def import_deck(request: Request):
    """Import a single deck from Archidekt or Moxfield by URL/id and add it
    to the deck registry (default lifecycle status: "testing"). Re-importing
    an already-registered deck refreshes its cardlist/metadata in place and
    preserves its current status.
    """
    data = await request.json()
    provider = data.get("provider", "")
    identifier = data.get("url") or data.get("id") or ""
    if not identifier:
        return {"success": False, "error": "url or id is required"}
    try:
        normalized = fetch_deck(provider, identifier)
    except ProviderError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"fetch failed: {e}"}

    deck = None
    if normalized.get("source") == "moxfield":
        # A Moxfield copy of a deck that's already in the registry gets
        # links.moxfield attached to that deck instead of a duplicate entry.
        attached = registry.attach_provider_deck(r, normalized)
        if attached["result"] != "unmatched":
            deck = attached["deck"]
    if deck is None:
        deck = registry.upsert_deck(r, normalized, default_status="testing")
    interactions_service.start_rebuild(r, trigger="sync", deck_ids=[registry.registry_id_of(deck)])
    _wake_bracket_worker(registry.registry_id_of(deck))
    return {"success": True, "deck": deck}


@app.get("/api/decks/{deck_id}/links")
async def get_deck_links(deck_id: str):
    """List every external source link (Archidekt/Moxfield/Commander Salt)
    configured on a deck."""
    deck = registry.find_deck(r, deck_id)
    if not deck:
        return JSONResponse(status_code=404, content={"success": False, "error": f"Deck '{deck_id}' not found"})
    return {"success": True, "links": deck_links.get_links(deck)}


@app.post("/api/decks/{deck_id}/links")
async def set_deck_link(deck_id: str, request: Request):
    """Add or edit one provider's link on a deck (save-to-record, not a
    one-off copy/paste): body {"provider": "archidekt"|"moxfield"|"commandersalt",
    "identifier": "<url or id>"}. Pass an empty/missing identifier to clear
    that provider's link. Validates/normalizes the identifier via the
    provider's own parser before persisting, so a bad URL is rejected here
    rather than at the next sync.
    """
    data = await request.json()
    provider = data.get("provider", "")
    identifier = data.get("identifier", "")
    try:
        deck = deck_links.set_link(r, deck_id, provider, identifier)
    except DeckLinkError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})
    return {"success": True, "links": deck_links.get_links(deck)}


@app.delete("/api/decks/{deck_id}/links/{provider}")
async def remove_deck_link(deck_id: str, provider: str):
    """Remove one provider's link from a deck. Does not touch the deck's
    already-synced cardlist."""
    try:
        deck = deck_links.remove_link(r, deck_id, provider)
    except DeckLinkError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})
    return {"success": True, "links": deck_links.get_links(deck)}


@app.post("/api/decks/{deck_id}/sync")
async def sync_deck(deck_id: str):
    """Sync a deck's cardlist from every linked provider (Archidekt and/or
    Moxfield) in a single operation, merging the results (see
    docs/DUAL_SOURCE_SYNC.md). One source being unreachable does not block
    the other -- the response's `warnings`/`sources` show exactly what
    happened per source. Fails with 400 only if the deck has no syncable
    link at all, or if every linked source failed.
    """
    try:
        report = deck_sync.sync_deck(r, deck_id)
    except DeckSyncError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})
    interactions_service.start_rebuild(r, trigger="sync", deck_ids=[deck_id])
    _wake_bracket_worker(deck_id)
    return {"success": True, "report": report.to_dict()}

@app.get("/api/inventory")
async def get_inventory(query: str = "", deck: Optional[List[str]] = Query(None)):
    """Flat per-copy inventory rows, read from the instance registry
    (instance:* + card_meta:*). See inventory_view.py. `deck` filters by
    deck name (or id / registry_id) and may be repeated."""
    return {"inventory": inventory_view.build_inventory(r, query=query, decks=deck)}

@app.get("/api/tags")
async def get_tags():
    """Get all Lab-managed tags"""
    lab_tags_json = r.get("lab_tags")
    lab_tags = json.loads(lab_tags_json) if lab_tags_json else {}
    
    # Aggregate all unique tags from Lab
    all_tags = set()
    for card_tags in lab_tags.values():
        all_tags.update(card_tags)
    
    return {"tags": sorted(all_tags)}

@app.get("/api/card/{card_name:path}/tags")
async def get_card_tags(card_name: str):
    """Get Lab tags and Archidekt reference tags for a specific card"""
    # Lab tags (our source of truth)
    lab_tags_json = r.get("lab_tags")
    lab_tags_all = json.loads(lab_tags_json) if lab_tags_json else {}
    card_lab_tags = lab_tags_all.get(card_name, [])
    
    # Archidekt reference tags (for comparison)
    vocab_json = r.get("tag_vocabulary")
    vocabulary = json.loads(vocab_json) if vocab_json else {}
    card_archidekt_tags = vocabulary.get(card_name, {})
    
    # Core tags defined globally
    core_tags_json = r.get("core_tags")
    core_tags = json.loads(core_tags_json) if core_tags_json else []
    
    # Deck appearances for cardinality, from the instance registry (the
    # legacy card:{name} blobs no longer exist on prod).
    insts = instance_store.list_instances(r, card_name=card_name)
    if not insts and card_name not in lab_tags_all and card_name not in vocabulary:
        return JSONResponse(status_code=404, content={"error": "Card not found"})
    total_decks = len({
        str(i.get("deck_id") if i.get("ownership_status") == "in_deck" else i.get("considered_for_deck"))
        for i in insts
        if (i.get("deck_id") if i.get("ownership_status") == "in_deck" else i.get("considered_for_deck"))
    })
    
    # Calculate confidence for Archidekt tags (reference only)
    archidekt_stats = []
    for tag, count in card_archidekt_tags.items():
        confidence = count / total_decks if total_decks > 0 else 0
        archidekt_stats.append({
            "tag": tag,
            "count": count,
            "total_decks": total_decks,
            "confidence": confidence
        })
    
    archidekt_stats.sort(key=lambda x: -x["confidence"])
    
    return {
        "card_name": card_name,
        "lab_tags": card_lab_tags,
        "archidekt_tags": archidekt_stats,
        "total_decks": total_decks,
        "core_tags": core_tags
    }

@app.get("/api/tags/core")
async def get_core_tags():
    core_tags_json = r.get("core_tags")
    return {"core_tags": json.loads(core_tags_json) if core_tags_json else []}

@app.post("/api/tags/core")
async def toggle_core_tag(request: Request):
    """Mark or unmark a tag as a core archetype tag"""
    data = await request.json()
    tag = data.get("tag")
    action = data.get("action")
    
    if not tag or action not in ["add", "remove"]:
        return {"error": "Invalid request"}, 400
        
    core_tags_json = r.get("core_tags")
    core_tags = set(json.loads(core_tags_json)) if core_tags_json else set()
    
    if action == "add":
        core_tags.add(tag)
    else:
        core_tags.discard(tag)
        
    r.set("core_tags", json.dumps(list(core_tags)))
    return {"success": True, "core_tags": list(core_tags)}

@app.post("/api/card/{card_name:path}/tags")
async def update_card_tags(card_name: str, request: Request):
    """Add or remove Lab tags from a card"""
    data = await request.json()
    tag = data.get("tag")
    action = data.get("action")  # "add" or "remove"
    
    if not tag or action not in ["add", "remove"]:
        return {"error": "Invalid request"}, 400
    
    # Get Lab tags
    lab_tags_json = r.get("lab_tags")
    lab_tags = json.loads(lab_tags_json) if lab_tags_json else {}
    
    # Initialize if card doesn't exist
    if card_name not in lab_tags:
        lab_tags[card_name] = []
    
    # Update Lab tags
    if action == "add" and tag not in lab_tags[card_name]:
        lab_tags[card_name].append(tag)
    elif action == "remove" and tag in lab_tags[card_name]:
        lab_tags[card_name].remove(tag)
    
    # Save Lab tags
    r.set("lab_tags", json.dumps(lab_tags))
    
    return {
        "success": True,
        "lab_tags": lab_tags[card_name],
        "action": action,
        "tag": tag
    }

@app.get("/api/decks/{deck_id}/categories")
async def get_deck_categories(deck_id: str):
    """Get per-deck category overrides: {card_name: [tags]}.

    This is the Deck Categories override layer (see docs/DECK_MANAGEMENT_VIEW.md) —
    distinct from and secondary to the global Lab Tags (/api/card/{name}/tags).
    """
    deck = registry.find_deck(r, deck_id)
    if not deck:
        return JSONResponse({"error": "Deck not found"}, status_code=404)

    deck_categories_json = r.get(f"deck_categories:{deck_id}")
    deck_categories = json.loads(deck_categories_json) if deck_categories_json else {}

    return {"deck_id": deck_id, "categories": deck_categories}


@app.post("/api/decks/{deck_id}/categories")
async def update_deck_categories(deck_id: str, request: Request):
    """Add or remove a per-deck category override tag for a card.

    NOTE: this endpoint is for the Deck Categories override layer only. Editing
    a card's global Lab Tags from the deck-manage UI must go through the
    existing /api/card/{name}/tags endpoint instead, per the DECISION comment
    on this task — Deck Categories is a secondary/override layer, not primary.
    """
    deck = registry.find_deck(r, deck_id)
    if not deck:
        return JSONResponse({"error": "Deck not found"}, status_code=404)

    data = await request.json()
    card_name = data.get("card_name")
    tag = data.get("tag")
    action = data.get("action")  # "add" or "remove"

    if not card_name or not tag or action not in ["add", "remove"]:
        return JSONResponse({"error": "Invalid request"}, status_code=400)

    deck_categories_json = r.get(f"deck_categories:{deck_id}")
    deck_categories = json.loads(deck_categories_json) if deck_categories_json else {}

    if card_name not in deck_categories:
        deck_categories[card_name] = []

    if action == "add" and tag not in deck_categories[card_name]:
        deck_categories[card_name].append(tag)
    elif action == "remove" and tag in deck_categories[card_name]:
        deck_categories[card_name].remove(tag)

    # Drop empty entries so the map only ever holds cards with an actual override
    if not deck_categories[card_name]:
        del deck_categories[card_name]

    r.set(f"deck_categories:{deck_id}", json.dumps(deck_categories))

    return {
        "success": True,
        "deck_id": deck_id,
        "card_name": card_name,
        "categories": deck_categories.get(card_name, []),
        "action": action,
        "tag": tag
    }


@app.post("/api/decks/{deck_id}/cards")
async def add_card_to_deck(deck_id: str, request: Request):
    """Manually add a card to a deck (the sanctioned manual add path — see
    docs/DECK_MANAGEMENT_VIEW.md "Add / remove flow").

    - Reuses an existing `in_collection` instance of `card_name` if one
      exists, else creates a new one via instances.create_instance, then
      transitions it to `in_deck` bound to this deck.
    - Appends card_name to the deck's local decklist if not already present.
    - Explicitly bypasses the is_deck_physical lock (this endpoint is the
      intended exception to that lock, unlike direct instance PATCH calls).
    """
    deck = registry.find_deck(r, deck_id)
    if not deck:
        return JSONResponse(status_code=404, content={"success": False, "error": "Deck not found"})

    data = await request.json()
    card_name = data.get("card_name")
    if not card_name:
        return JSONResponse(status_code=400, content={"success": False, "error": "card_name is required"})

    registry_id = registry.registry_id_of(deck)
    deck_name = deck.get("name", "")

    try:
        existing = [
            inst for inst in instance_store.list_instances(r, card_name=card_name, ownership_status="in_collection")
        ]
        if existing:
            instance = existing[0]
        else:
            instance = instance_store.create_instance(
                r, card_name=card_name, ownership_status="in_collection"
            )

        instance = instance_store.transition_status(
            r,
            instance["id"],
            new_status="in_deck",
            deck_id=registry_id,
            deck_name=deck_name,
            _allow_physical_lock_bypass=True,
        )
    except InstanceError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})

    registry.add_card_to_decklist(r, registry_id, card_name)

    return {"success": True, "instance": instance, "deck_id": registry_id, "card_name": card_name}


@app.delete("/api/decks/{deck_id}/cards/{instance_id}")
async def remove_card_from_deck(deck_id: str, instance_id: str):
    """Manually remove a card instance from a deck (the sanctioned manual
    remove path — see docs/DECK_MANAGEMENT_VIEW.md "Add / remove flow").

    - Validates the instance is currently bound `in_deck` to this deck_id
      (mismatched deck_id or unknown instance_id is a 404, not a silent
      no-op).
    - Transitions the instance to `in_collection` via
      instance_store.transition_status, which clears deck_id per the
      existing state machine — the instance becomes plain inventory
      immediately.
    - Explicitly bypasses the is_deck_physical lock (this endpoint is the
      intended exception to that lock, unlike direct instance PATCH calls),
      mirroring add_card_to_deck.
    - If multiple instances of the same card are bound to the deck, the
      caller (UI) picks which instance_id to unbind; this endpoint only
      ever acts on the single instance_id given.
    """
    deck = registry.find_deck(r, deck_id)
    if not deck:
        return JSONResponse(status_code=404, content={"success": False, "error": "Deck not found"})

    registry_id = registry.registry_id_of(deck)

    instance = instance_store.get_instance(r, instance_id)
    if not instance:
        return JSONResponse(status_code=404, content={"success": False, "error": "Instance not found"})

    if instance.get("ownership_status") != "in_deck" or str(instance.get("deck_id")) not in registry.deck_instance_ids(deck):
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": "Instance is not bound to this deck"},
        )

    try:
        instance = instance_store.transition_status(
            r,
            instance_id,
            new_status="in_collection",
            _allow_physical_lock_bypass=True,
        )
    except InstanceError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})

    return {"success": True, "instance": instance, "deck_id": registry_id, "instance_id": instance_id}


@app.get("/api/decks/{deck_id}/resync-review")
async def get_deck_resync_review(deck_id: str):
    """Review changes detected during a physical deck resync.

    Returns:
    - pending_removal: instances currently flagged with pending_removal for this deck
    - unbound_cards: informational list of cards in remote decklist without bound instances
    """
    deck = registry.find_deck(r, deck_id)
    if not deck:
        return JSONResponse(status_code=404, content={"success": False, "error": "Deck not found"})

    registry_id = registry.registry_id_of(deck)
    raw_id = str(deck.get("id")) if deck.get("id") is not None else None

    # Fetch flagged instances using helper
    pending_instances = instance_store.list_pending_removal(r, registry_id)
    if raw_id and raw_id != registry_id:
        seen = {inst["id"] for inst in pending_instances}
        for inst in instance_store.list_pending_removal(r, raw_id):
            if inst["id"] not in seen:
                pending_instances.append(inst)
                seen.add(inst["id"])

    # Also check bound instances for pending_removal marker in case secondary index was bypassed
    bound = instance_store.list_instances(r, deck_id=registry_id)
    if raw_id and raw_id != registry_id:
        bound.extend(instance_store.list_instances(r, deck_id=raw_id))
    seen = {inst["id"] for inst in pending_instances}
    for inst in bound:
        if inst.get("pending_removal") and inst["id"] not in seen:
            pending_instances.append(inst)
            seen.add(inst["id"])

    pending_instances.sort(key=lambda x: x.get("card_name", ""))

    cards = deck.get("cards", [])
    unbound_cards = instance_store.find_unbound_remote_cards(r, registry.deck_instance_ids(deck), cards)
    unbound_card_names = [c["name"] for c in unbound_cards]

    return {
        "success": True,
        "deck_id": deck_id,
        "registry_id": registry_id,
        "pending_removal": pending_instances,
        "instances": pending_instances,
        "unbound_cards": unbound_cards,
        "unbound_card_names": unbound_card_names,
        "remotely_added": unbound_card_names,
    }


@app.post("/api/decks/{deck_id}/resync-review/{instance_id}")
async def resolve_deck_resync_review(deck_id: str, instance_id: str, request: Request):
    """Resolve one pending_removal instance flagged during deck resync.

    - action 'keep' or 'save': transition to 'in_collection' (clears deck_id) and clear pending_removal
    - action 'toss': transition to 'not_owned' and clear pending_removal
    Enforces 404 if the instance does not belong to the deck or lacks the pending_removal marker.
    """
    deck = registry.find_deck(r, deck_id)
    if not deck:
        return JSONResponse(status_code=404, content={"success": False, "error": "Deck not found"})

    registry_id = registry.registry_id_of(deck)
    instance = instance_store.get_instance(r, instance_id)
    if not instance:
        return JSONResponse(status_code=404, content={"success": False, "error": "Instance not found"})

    pending = instance.get("pending_removal")
    if not pending:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": "Instance does not have pending_removal marker"},
        )

    valid_deck_ids = {str(deck_id), str(registry_id)}
    if deck.get("id") is not None:
        valid_deck_ids.add(str(deck.get("id")))

    inst_deck = str(instance.get("deck_id")) if instance.get("deck_id") else None
    pending_reg = str(pending.get("deck_registry_id") or pending.get("registry_id")) if pending else None

    if inst_deck not in valid_deck_ids and pending_reg not in valid_deck_ids:
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": "Instance does not belong to this deck"},
        )

    try:
        data = await request.json()
    except Exception:
        data = {}

    action = (data.get("action") or "").strip().lower()
    if action not in ("keep", "save", "toss"):
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": f"Invalid action: must be 'keep' or 'toss', got {action!r}"},
        )

    try:
        updated = instance_store.resolve_resync_removal(r, instance_id, action)
    except InstanceError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})

    return {
        "success": True,
        "instance": updated,
        "action": action,
        "deck_id": deck_id,
        "registry_id": registry_id,
        "instance_id": instance_id,
    }


@app.get("/api/decks/{deck_id}/search")
async def search_deck_cards(deck_id: str, q: str = ""):
    """Local card-name autocomplete for the add-card dropdown in the deck
    management view. Case-insensitive substring search over `card_meta:*`
    records (same filtering pattern as /api/tags-adjacent card lookups).

    v1 scope: local Redis card_meta matching only — no live Scryfall lookups.
    """
    deck = registry.find_deck(r, deck_id)
    if not deck:
        return JSONResponse({"error": "Deck not found"}, status_code=404)

    query = q.strip()
    if not query:
        return {"deck_id": deck_id, "query": q, "results": []}

    query_lower = query.lower()
    results = []
    for key in r.keys("card_meta:*"):
        card_name = key[len("card_meta:"):]
        if query_lower not in card_name.lower():
            continue

        raw = r.get(key)
        if not raw:
            continue
        try:
            meta = json.loads(raw)
        except (TypeError, ValueError):
            continue

        results.append({
            "name": card_name,
            "type": meta.get("type", "Unknown"),
            "cmc": meta.get("cmc", 0),
        })

    results.sort(key=lambda c: c["name"])
    results = results[:50]

    return {"deck_id": deck_id, "query": q, "results": results}


@app.get("/api/decks/{deck_id}/manage")
async def get_deck_manage(deck_id: str):
    """Single-call aggregate for the deck management view (see
    docs/DECK_MANAGEMENT_VIEW.md "New API surface").

    Assembles, in one response, so the template never does N+1 fetches:
    - the deck's bound instances (ownership_status == "in_deck" for this deck)
    - each bound card's card_meta (type/cmc/color), joined by card_name
    - the deck's category overrides (deck_categories:{deck_id}), read using
      the same key derivation as the /api/decks/{deck_id}/categories
      endpoints (the raw URL deck_id, not registry_id) so overrides written
      via those endpoints are visible here.
    - each card's global lab_tags (lab_tags redis key)
    - effective_category per card: deck category override, else lab tag,
      else "Uncategorized" (first tag wins when a card has several) — lets
      the UI group cards without any further calls.
    """
    deck = registry.find_deck(r, deck_id)
    if not deck:
        return JSONResponse({"error": "Deck not found"}, status_code=404)

    registry_id = registry.registry_id_of(deck)

    instances = instance_store.list_deck_instances(
        r, registry.deck_instance_ids(deck), ownership_status="in_deck"
    )

    # Match the /api/decks/{deck_id}/categories endpoints, which key off the
    # raw URL deck_id (not registry_id) — otherwise overrides written via
    # those endpoints for numeric deck ids would silently be dropped here.
    deck_categories_json = r.get(f"deck_categories:{deck_id}")
    deck_categories = json.loads(deck_categories_json) if deck_categories_json else {}

    lab_tags_json = r.get("lab_tags")
    lab_tags_all = json.loads(lab_tags_json) if lab_tags_json else {}

    # Pre-fetch card_meta for every distinct card_name bound to this deck to
    # avoid a per-card GET round trip.
    card_names = sorted({inst["card_name"] for inst in instances})
    card_meta_by_name = {}
    if card_names:
        meta_pipe = r.pipeline()
        for name in card_names:
            meta_pipe.get(f"card_meta:{name}")
        for name, raw in zip(card_names, meta_pipe.execute()):
            card_meta_by_name[name] = json.loads(raw) if raw else {}

    cards = []
    for inst in instances:
        card_name = inst["card_name"]
        meta = card_meta_by_name.get(card_name, {})
        card_lab_tags = lab_tags_all.get(card_name, [])
        card_deck_categories = deck_categories.get(card_name, [])

        if card_deck_categories:
            effective_category = card_deck_categories[0]
        elif card_lab_tags:
            effective_category = card_lab_tags[0]
        else:
            effective_category = "Uncategorized"

        cards.append({
            "instance_id": inst["id"],
            "card_name": card_name,
            "type": meta.get("type", "Unknown"),
            "cmc": meta.get("cmc", 0),
            "color": meta.get("color", ""),
            "lab_tags": card_lab_tags,
            "deck_categories": card_deck_categories,
            "effective_category": effective_category,
        })

    return {
        "deck_id": deck_id,
        "deck_name": deck.get("name", ""),
        "status": deck.get("status"),
        "cards": cards,
        "deck_categories": deck_categories,
    }


# ---------------------------------------------------------------------------
# Card Instance Registry — ownership source of truth (see docs/CARD_DATABASE.md)
# ---------------------------------------------------------------------------

@app.post("/api/instances")
async def create_instance(request: Request):
    """Create a new unique card instance (in_mail, in_collection, in_deck, or not_owned)."""
    data = await request.json()
    try:
        record = instance_store.create_instance(
            r,
            card_name=data.get("card_name"),
            ownership_status=data.get("ownership_status", "not_owned"),
            set=data.get("set", ""),
            set_name=data.get("set_name", ""),
            collector_number=data.get("collector_number", ""),
            finish=data.get("finish", "nonfoil"),
            condition=data.get("condition", "NM"),
            language=data.get("language", "EN"),
            deck_id=data.get("deck_id"),
            deck_name=data.get("deck_name", ""),
            considered_for_deck=data.get("considered_for_deck"),
            price_paid=data.get("price_paid", 0.0),
            source=data.get("source", ""),
            date_ordered=data.get("date_ordered", ""),
            date_acquired=data.get("date_acquired", ""),
            archidekt_uid=data.get("archidekt_uid", ""),
            notes=data.get("notes", ""),
        )
        return {"success": True, "instance": record}
    except InstanceError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})


@app.get("/api/instances")
async def list_instances(
    card: Optional[str] = None,
    status: Optional[str] = None,
    deck_id: Optional[str] = None,
    set: Optional[str] = None,
    q: Optional[str] = None,
):
    """List/filter instances by card name, ownership status, deck, or set.

    `q` is a free-text search (case-insensitive substring match against card
    name, set name, deck name, and notes) for the inventory search box —
    separate from the exact-match `card` filter.
    """
    records = instance_store.list_instances(
        r, card_name=card, ownership_status=status, deck_id=deck_id, set=set, q=q
    )
    return {"instances": records, "count": len(records)}


@app.get("/api/instances/{instance_id}")
async def get_instance(instance_id: str):
    record = instance_store.get_instance(r, instance_id)
    if not record:
        return JSONResponse(status_code=404, content={"error": "Instance not found"})
    return {"instance": record}


@app.patch("/api/instances/{instance_id}")
async def update_instance(instance_id: str, request: Request):
    """Update instance fields, or transition ownership_status/deck assignment.

    Body may include `ownership_status` (+ optional `deck_id`/`deck_name` when
    moving to in_deck) to transition state, and/or any editable detail fields
    (set, condition, finish, price_paid, source, notes, etc).
    """
    data = await request.json()
    try:
        record = None
        if "ownership_status" in data:
            record = instance_store.transition_status(
                r,
                instance_id,
                new_status=data["ownership_status"],
                deck_id=data.get("deck_id"),
                deck_name=data.get("deck_name", ""),
            )

        detail_fields = {
            k: v for k, v in data.items()
            if k not in {"ownership_status", "deck_id", "deck_name"}
        }
        if detail_fields:
            record = instance_store.update_instance_fields(r, instance_id, **detail_fields)

        if record is None:
            record = instance_store.get_instance(r, instance_id)
            if not record:
                return JSONResponse(status_code=404, content={"error": "Instance not found"})

        return {"success": True, "instance": record}
    except InstanceError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})


@app.delete("/api/instances/{instance_id}")
async def delete_instance(instance_id: str):
    deleted = instance_store.delete_instance(r, instance_id)
    if not deleted:
        return JSONResponse(status_code=404, content={"error": "Instance not found"})
    return {"success": True}


@app.get("/api/cards/{card_name:path}/instances")
async def get_card_instances(card_name: str):
    """Ownership rollup for a card: counts by status and per-deck breakdown."""
    return instance_store.card_rollup(r, card_name)


# ---------------------------------------------------------------------------
# Linked Accounts — provider-account registry + sync (see docs/LINKED_ACCOUNTS.md)
# Thin wrappers over linked_accounts.py; no business logic lives here.
# ---------------------------------------------------------------------------

@app.get("/api/linked-accounts")
async def list_linked_accounts():
    """List every linked account with its sync status."""
    accounts = linked_accounts.list_accounts(r)
    return {"accounts": accounts, "count": len(accounts)}


@app.post("/api/linked-accounts")
async def add_linked_account(request: Request):
    """Link a new provider account and trigger its immediate first sync.

    Body: {"provider": "archidekt"|"moxfield", "username": str}
    """
    data = await request.json()
    provider = data.get("provider", "")
    username = data.get("username", "")
    try:
        account = linked_accounts.add_account(r, provider, username)
    except LinkedAccountError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})

    account = linked_accounts.sync_account(r, account["id"])
    return {"success": True, "account": account}


@app.delete("/api/linked-accounts/{account_id}")
async def remove_linked_account(account_id: str):
    """Unlink an account. Decks already pulled from it stay as-is (pull-only,
    non-destructive design — see docs/LINKED_ACCOUNTS.md)."""
    try:
        linked_accounts.remove_account(r, account_id)
    except LinkedAccountError as e:
        return JSONResponse(status_code=404, content={"success": False, "error": str(e)})
    return {"success": True}


@app.post("/api/linked-accounts/{account_id}/sync")
async def sync_linked_account(account_id: str):
    """Manually resync one linked account."""
    try:
        account = linked_accounts.sync_account(r, account_id)
    except LinkedAccountError as e:
        return JSONResponse(status_code=404, content={"success": False, "error": str(e)})
    interactions_service.start_rebuild(r, trigger="sync")
    _wake_bracket_worker()
    return {"success": True, "account": account}


@app.post("/api/linked-accounts/sync-all")
async def sync_all_linked_accounts():
    """Manually resync every enabled linked account."""
    accounts = linked_accounts.sync_all(r)
    interactions_service.start_rebuild(r, trigger="sync")
    _wake_bracket_worker()
    return {"success": True, "accounts": accounts, "count": len(accounts)}


@app.post("/api/intake")
async def api_deck_intake(request: Request):
    """Unified deck intake orchestration endpoint.
    Ingests raw decklist or card structure and user review intent,
    validates both, and returns a consolidated deck intake object ready
    for downstream analysis.
    """
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON body"})

    from deck_intake import DeckIntakeService, DeckIntakeValidationError
    service = DeckIntakeService()
    try:
        result = service.process_payload(data)
        return {"success": True, "intake": result.to_dict()}
    except DeckIntakeValidationError as e:
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error": str(e),
                "errors": e.errors,
                "warnings": e.warnings,
            },
        )
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})


@app.post("/api/intake/validate")
async def api_deck_intake_validate(request: Request):
    """Validate incoming decklist structure and intent without throwing errors."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON body"})

    from deck_intake import DeckIntakeService
    service = DeckIntakeService()
    result = service.validate(
        decklist=data.get("decklist") or data.get("cards"),
        intent=data.get("user_intent"),
        name=data.get("name"),
        format=data.get("format", "commander"),
        commanders=data.get("commanders"),
        deck_id=data.get("deck_id"),
        strict_coherence=data.get("strict_coherence", True),
        strict_deck_size=data.get("strict_deck_size", False),
        strict_singleton=data.get("strict_singleton", False),
    )
    return {"success": True, "validation": result.to_dict()}


@app.post("/api/analytics")
@app.post("/api/decks/analytics")
async def api_deck_analytics(request: Request):
    """Run aggregated deck analytics across enabled providers with configurable timeouts.

    Accepts:
    1. Direct DecklistInput: {"commanders": [...], "cards": [...]}
    2. Wrapped payload: {"deck": {...}, "providers": ["edhrec", "commandersalt"], "timeout": 10.0}
    3. Decklist text: {"decklist": "Commander\\n...", "commanders": [...]}
    4. Deck ID reference: {"deck_id": "archidekt:123"}
    """
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON body"})

    if not isinstance(data, dict):
        return JSONResponse(status_code=400, content={"success": False, "error": "Request body must be a JSON object"})

    from analytics.aggregator import default_aggregator, coerce_to_decklist_input

    deck_id = data.get("deck_id")
    deck_data = data.get("deck") or data

    if deck_id and not data.get("cards") and not data.get("deck") and not data.get("decklist"):
        deck_obj = registry.find_deck(r, deck_id)
        if not deck_obj:
            return JSONResponse(status_code=404, content={"success": False, "error": f"Deck '{deck_id}' not found"})
        deck_data = deck_obj

    providers = data.get("providers")
    if providers is not None:
        if not isinstance(providers, list) or not all(isinstance(p, str) for p in providers):
            return JSONResponse(status_code=400, content={"success": False, "error": "'providers' must be a list of strings"})

    timeout = data.get("timeout")
    if timeout is not None:
        try:
            timeout = float(timeout)
        except (ValueError, TypeError):
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid timeout parameter"})

    try:
        deck_input = coerce_to_decklist_input(deck_data)
    except Exception as e:
        return JSONResponse(status_code=422, content={"success": False, "error": f"Deck validation error: {str(e)}"})

    try:
        report = await default_aggregator.aggregate_async(
            deck=deck_input,
            providers=providers,
            timeout=timeout,
        )
        return {"success": True, "report": report.model_dump()}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@app.get("/api/decks/{deck_id}/analytics")
async def api_get_deck_analytics(
    deck_id: str,
    timeout: Optional[float] = None,
    providers: Optional[str] = None,
):
    """Run aggregated deck analytics for an existing deck in the library."""
    deck_obj = registry.find_deck(r, deck_id)
    if not deck_obj:
        return JSONResponse(status_code=404, content={"success": False, "error": f"Deck '{deck_id}' not found"})

    from analytics.aggregator import default_aggregator, coerce_to_decklist_input

    prov_list = [p.strip() for p in providers.split(",")] if providers else None
    try:
        deck_input = coerce_to_decklist_input(deck_obj)
    except Exception as e:
        return JSONResponse(status_code=422, content={"success": False, "error": f"Deck validation error: {str(e)}"})

    try:
        report = await default_aggregator.aggregate_async(
            deck=deck_input,
            providers=prov_list,
            timeout=timeout,
        )
        return {"success": True, "report": report.model_dump()}
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@app.get("/api/analytics/providers")
async def api_list_analytics_providers():
    """List available deck analytics providers and their supported queries."""
    from analytics.aggregator import default_aggregator
    providers_info = default_aggregator.get_available_providers()
    return {"success": True, "providers": providers_info}


@app.post("/api/review")
@app.post("/api/decks/review")
async def api_deck_review(request: Request):
    """Generate an actionable deck review synthesizing analysis, recommendations, and card swaps."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"success": False, "error": "Invalid JSON body"})

    if not isinstance(data, dict):
        return JSONResponse(status_code=400, content={"success": False, "error": "Request body must be a JSON object"})

    from deck_review import review_deck
    from user_intent import parse_user_intent

    deck_id = data.get("deck_id")
    deck_input = data.get("deck") or data.get("decklist") or data.get("cards")

    if deck_id and not deck_input:
        deck_obj = registry.find_deck(r, deck_id)
        if not deck_obj:
            return JSONResponse(status_code=404, content={"success": False, "error": f"Deck '{deck_id}' not found"})
        deck_input = deck_obj

    if not deck_input:
        deck_input = data

    intent_data = data.get("user_intent")
    intent_obj = None
    if intent_data:
        try:
            intent_obj = parse_user_intent(intent_data)
        except Exception as e:
            return JSONResponse(status_code=422, content={"success": False, "error": f"Invalid user intent: {str(e)}"})

    try:
        review = review_deck(deck=deck_input, intent=intent_obj)
        return {
            "success": True,
            "review": review.model_dump(),
            "markdown": review.to_markdown(),
            "cli": review.to_cli(),
        }
    except Exception as e:
        return JSONResponse(status_code=422, content={"success": False, "error": f"Deck review failed: {str(e)}"})


@app.get("/api/decks/{deck_id}/review")
async def api_get_deck_review(deck_id: str):
    """Generate an actionable deck review for an existing deck in the library.
    `deck_id` may be the deck id, registry_id, or display name (the deck page
    only knows the name)."""
    try:
        deck_obj = registry.find_deck(r, deck_id) or next(
            (d for d in registry.list_decks(r) if d.get("name") == deck_id), None
        )
    except Exception:
        deck_obj = None

    if not deck_obj:
        return JSONResponse(status_code=404, content={"success": False, "error": f"Deck '{deck_id}' not found"})

    from deck_review import review_deck
    try:
        review = review_deck(deck=deck_obj)
        return {
            "success": True,
            "review": review.model_dump(),
            "markdown": review.to_markdown(),
            "cli": review.to_cli(),
        }
    except Exception as e:
        return JSONResponse(status_code=422, content={"success": False, "error": f"Deck review failed: {str(e)}"})


# ---------------------------------------------------------------------------
# Card interaction graph (AN-1) -- see docs/INTERACTION_GRAPH.md
# ---------------------------------------------------------------------------


def _find_deck_any(deck_id: str):
    """Deck by id, registry_id, or display name."""
    try:
        return registry.find_deck(r, deck_id) or next(
            (d for d in registry.list_decks(r) if d.get("name") == deck_id), None
        )
    except Exception:
        return None


@app.get("/api/decks/{deck_id}/interactions")
async def api_deck_interactions(deck_id: str):
    """Interaction graph for one deck: nodes (produces/consumes, in/out/total
    degree, per-reason counts), labeled edges (reasons, weight), resource
    labels and the most-connected cards. `deck_id` may be the id,
    registry_id or deck name."""
    deck = _find_deck_any(deck_id)
    if not deck:
        return JSONResponse(status_code=404, content={"success": False, "error": f"Deck '{deck_id}' not found"})
    graph = await asyncio.to_thread(interactions_service.get_deck_graph, r, deck)
    return {"success": True, "graph": graph}


@app.post("/api/decks/{deck_id}/interactions/refresh")
async def api_refresh_deck_interactions(deck_id: str):
    deck = _find_deck_any(deck_id)
    if not deck:
        return JSONResponse(status_code=404, content={"success": False, "error": f"Deck '{deck_id}' not found"})
    reg_id = deck.get("registry_id") or registry.registry_id_of(deck)
    started = interactions_service.start_rebuild(r, trigger="manual", deck_ids=[reg_id])
    return JSONResponse(status_code=202, content={"success": True, **started})


@app.post("/api/interactions/refresh")
async def api_refresh_interactions(wait: bool = False):
    """Manual rebuild of every deck graph + the collection graph. Runs in the
    background (202) unless ?wait=true, which returns the run log."""
    if wait:
        run = await asyncio.to_thread(interactions_service.rebuild, r, "manual")
        return {"success": run.get("status") in ("ok", "deferred"), "run": run}
    started = interactions_service.start_rebuild(r, trigger="manual")
    return JSONResponse(status_code=202, content={"success": True, **started})


@app.get("/api/interactions/runs")
async def api_interaction_runs(limit: int = 20):
    """Per-run call/cost log (newest first)."""
    return {"success": True, "runs": interactions_service.list_runs(r, limit=limit)}


@app.get("/api/interactions/collection")
async def api_collection_interactions(card: Optional[str] = None):
    """Whole-collection graph: nodes with degrees + resource index. With
    ?card=NAME, returns that card's labeled in/out edges across the collection."""
    coll = interactions_service.get_collection_graph(r)
    if coll is None:
        return JSONResponse(status_code=404, content={"success": False, "error": "collection graph not built yet; POST /api/interactions/refresh"})
    if card:
        from analytics.interactions.graph import collection_neighbors
        return {"success": True, "card": collection_neighbors(coll, card)}
    return {"success": True, "graph": coll}


@app.get("/api/interactions/vocabulary")
async def api_interaction_vocabulary():
    from analytics.interactions.vocab import VOCAB_VERSION, vocab_payload
    return {"success": True, "version": VOCAB_VERSION, "resources": vocab_payload()}


# ---------------------------------------------------------------------------
# Commander Brackets reference (BR-1) -- see docs/BRACKETS_SPEC.md
# ---------------------------------------------------------------------------


@app.get("/api/brackets")
async def api_brackets(refresh: bool = False):
    """The official bracket table plus the current Game Changers list
    (Redis cache -> Scryfall is:gamechanger -> checked-in snapshot)."""
    from analytics import brackets as br
    gc = await br.get_game_changers(r, refresh=refresh)
    return {"success": True, **br.brackets_payload(), "game_changers": gc.to_dict()}


# ---------------------------------------------------------------------------
# Engines + Commander Spellbook combos (AN-2) -- see docs/ENGINES_AND_COMBOS.md
# ---------------------------------------------------------------------------


@app.get("/api/decks/{deck_id}/engines")
async def api_deck_engines(deck_id: str):
    """Engines detected in the deck's interaction graph: resource chains
    (sources -> converters -> payoffs, with fuel) and two-card loops, each
    named ("Aristocrats: tokens → sac → drain") and scored."""
    deck = _find_deck_any(deck_id)
    if not deck:
        return JSONResponse(status_code=404, content={"success": False, "error": f"Deck '{deck_id}' not found"})
    out = await asyncio.to_thread(interactions_service.get_deck_engines, r, deck)
    return {"success": True, "engines": out}


@app.get("/api/decks/{deck_id}/combos")
async def api_deck_combos(deck_id: str, refresh: bool = False, owned_only: bool = False,
                          limit: Optional[int] = None):
    """Commander Spellbook combos: fully in the deck, and one card away (the
    missing card flagged when owned). Cached per decklist; ?refresh=true
    refetches, ?owned_only=true keeps only near-misses you own, ?limit=N
    caps the near-miss lists (counts stay complete)."""
    deck = _find_deck_any(deck_id)
    if not deck:
        return JSONResponse(status_code=404, content={"success": False, "error": f"Deck '{deck_id}' not found"})
    try:
        out = await asyncio.to_thread(combos.deck_combos, r, deck, refresh=refresh,
                                      owned_only=owned_only, limit=limit)
    except combos.SpellbookError as exc:
        return JSONResponse(status_code=502, content={"success": False, "error": str(exc)})
    return {"success": True, "combos": out}


# ---------------------------------------------------------------------------
# Composition, identity (thesis + pillars), pillar fit, EDHREC inclusion,
# weak performers (AN-3) -- see docs/DECK_IDENTITY.md
# ---------------------------------------------------------------------------


def _deck_or_404(deck_id: str):
    deck = _find_deck_any(deck_id)
    if not deck:
        return None, JSONResponse(status_code=404, content={"success": False, "error": f"Deck '{deck_id}' not found"})
    return deck, None


def _bad_request(exc: Exception, status: int = 400):
    return JSONResponse(status_code=status, content={"success": False, "error": str(exc)})


async def _json_body(request: Request):
    try:
        return await request.json()
    except Exception:
        return None


def _wake_bracket_worker(deck_id: str = "") -> None:
    """Latency hint for the separate bracket-evaluation process (BR-5). The
    worker also re-checks every deck's input hash on its own schedule, so a
    lost wake-up only delays the evaluation; it must never fail the caller."""
    try:
        bracket_worker.wake(r, deck_id)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("commander_lab.app").warning("bracket worker wake failed: %s", exc)


@app.get("/api/decks/{deck_id}/composition")
async def api_deck_composition(deck_id: str):
    """Cards classified into Lands / Ramp / Card advantage / Targeted disruption /
    Mass disruption / Plan, vs the deck's template (Command Zone 2025 by default)."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    out = await asyncio.to_thread(insights.deck_composition, r, deck)
    return {"success": True, "composition": out}


@app.put("/api/decks/{deck_id}/composition/template")
async def api_set_composition_template(deck_id: str, request: Request):
    """Per-deck template override, e.g. {"lands": 36, "ramp": 12}. Rows not sent keep their value."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request)
    try:
        tpl = composition_mod.set_template_override(r, deck, body)
    except composition_mod.CompositionError as exc:
        return _bad_request(exc)
    return {"success": True, "template": tpl}


@app.delete("/api/decks/{deck_id}/composition/template")
async def api_reset_composition_template(deck_id: str):
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    return {"success": True, "template": composition_mod.reset_template(r, deck)}


@app.get("/api/decks/{deck_id}/identity")
async def api_deck_identity(deck_id: str, cards: bool = True):
    """Thesis + pillars with pillar fit (local embeddings). ?cards=false drops per_card."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    out = await asyncio.to_thread(insights.identity_view, r, deck, cards)
    return {"success": True, "identity": out}


@app.post("/api/decks/{deck_id}/identity/generate")
async def api_generate_identity(deck_id: str):
    """Ask the LLM (Gemini, then Nous) for a thesis + 3-5 pillars. First time:
    drafts. Afterwards: only proposes NEW draft pillars; never edits yours."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    out = await asyncio.to_thread(insights.generate, r, deck)
    ok = out.get("status") in ("generated", "proposed")
    view = await asyncio.to_thread(insights.identity_view, r, deck, False)
    return JSONResponse(status_code=200 if ok else 502,
                        content={"success": ok, "result": out, "identity": view})


@app.put("/api/decks/{deck_id}/identity/thesis")
async def api_update_thesis(deck_id: str, request: Request):
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    try:
        thesis = identity_mod.update_thesis(r, deck, await _json_body(request) or {})
    except identity_mod.IdentityError as exc:
        return _bad_request(exc)
    return {"success": True, "thesis": thesis}


@app.post("/api/decks/{deck_id}/identity/pillars")
async def api_add_pillar(deck_id: str, request: Request):
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    try:
        p = identity_mod.add_pillar(r, deck, await _json_body(request) or {})
    except identity_mod.IdentityError as exc:
        return _bad_request(exc)
    return {"success": True, "pillar": p}


@app.put("/api/decks/{deck_id}/identity/pillars/order")
async def api_reorder_pillars(deck_id: str, request: Request):
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request) or {}
    try:
        ps = identity_mod.reorder_pillars(r, deck, body.get("order") if isinstance(body, dict) else None)
    except identity_mod.PillarNotFound as exc:
        return _bad_request(exc, 404)
    except identity_mod.IdentityError as exc:
        return _bad_request(exc)
    return {"success": True, "pillars": ps}


@app.patch("/api/decks/{deck_id}/identity/pillars/{pillar_id}")
async def api_update_pillar(deck_id: str, pillar_id: str, request: Request):
    """{"action": "pin"|"unpin"|"keep"|"reject"|"restore"} and/or {"name", "description"} (edits pin)."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    try:
        p = identity_mod.update_pillar(r, deck, pillar_id, await _json_body(request) or {})
    except identity_mod.PillarNotFound as exc:
        return _bad_request(exc, 404)
    except identity_mod.IdentityError as exc:
        return _bad_request(exc)
    return {"success": True, "pillar": p}


@app.get("/api/decks/{deck_id}/edhrec")
async def api_deck_edhrec(deck_id: str, refresh: bool = False):
    """EDHREC inclusion per card for the deck's commander (cached 7 days)."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    out = await asyncio.to_thread(insights.deck_edhrec, r, deck, refresh)
    return {"success": True, "edhrec": out}


@app.get("/api/decks/{deck_id}/weak-performers")
async def api_deck_weak_performers(deck_id: str, limit: Optional[int] = None):
    """Computed only: few graph edges + off-pillar + low EDHREC inclusion, equally weighted."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    graph = await asyncio.to_thread(interactions_service.get_deck_graph, r, deck)
    out = await asyncio.to_thread(insights.deck_weak_performers, r, deck, graph, limit=limit)
    return {"success": True, "weak_performers": out}


@app.get("/api/decks/{deck_id}/played-with")
async def api_deck_played_with(deck_id: str, top: int = 8):
    """card2vec (AN-7): how much each card is 'played with' the rest of this
    deck in real Commander decklists. Most/least lists + coverage."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    out = await asyncio.to_thread(insights.deck_played_view, r, deck, None, max(1, min(top, 30)))
    return {"success": True, "played_with": out}


@app.get("/api/card2vec")
async def api_card2vec(card: Optional[str] = None, n: int = 10):
    """card2vec model info; with ?card=, its nearest 'played-with' neighbours."""
    from analytics import card2vec as card2vec_mod

    m = card2vec_mod.default_model()
    info = await asyncio.to_thread(m.info)
    out = {"success": True, "model": info}
    if card:
        out["card"] = card
        out["known"] = m.has(card)
        out["neighbours"] = await asyncio.to_thread(m.neighbours, card, max(1, min(n, 50)))
    return out


# ---------------------------------------------------------------------------
# Bracket evaluation (BR-5): stored results from the separate worker process
# (analytics/bracket_worker.py, compose service bracket-worker). These routes
# never evaluate; they read bracket:eval:{registry_id} or queue a request.
# ---------------------------------------------------------------------------


def _bracket_deck(deck_id: str):
    deck = _find_deck_any(deck_id)
    if not deck:
        return None, JSONResponse(status_code=404, content={
            "success": False, "status": "deck_not_found", "error": f"Deck '{deck_id}' not found"})
    return deck, None


@app.get("/api/deck/{deck_id}/bracket")
@app.get("/api/decks/{deck_id}/bracket")
async def api_deck_bracket(deck_id: str):
    """The stored bracket evaluation (estimated bracket + power level,
    conflicts, explanation). 404 with status "not_evaluated" until the
    worker has evaluated the deck; stale=true when the decklist or journal
    changed since (the worker picks that up on its own)."""
    deck, err = _bracket_deck(deck_id)
    if err:
        return err
    rid = registry.registry_id_of(deck)
    try:
        rec = bracket_worker.load_result(r, rid)
        queued = bracket_worker.is_pending(r, rid)
        current = bracket_worker.current_input_hash(r, deck)
    except Exception as exc:  # noqa: BLE001 - Redis trouble: say so, don't 500
        return JSONResponse(status_code=503, content={"success": False, "status": "unavailable",
                                                      "registry_id": rid, "error": str(exc)[:200]})
    base = {"registry_id": rid, "deck_name": deck.get("name"), "pending": queued}
    if not rec or not rec.get("result"):
        status = "error" if rec and rec.get("status") == "error" else "not_evaluated"
        body = {"success": False, **base, "status": status,
                "error": (rec or {}).get("error") or "Not evaluated yet.", "evaluation": None}
        return JSONResponse(status_code=404, content=body)
    result_hash = rec.get("result_hash") or rec.get("input_hash")
    return {"success": True, **base, "status": rec.get("status") or "ok",
            "evaluated_at": rec.get("evaluated_at"), "input_hash": result_hash,
            "stale": result_hash != current,
            "error": rec.get("error"), "error_at": rec.get("error_at"),
            "evaluation": rec["result"]}


@app.post("/api/deck/{deck_id}/bracket")
@app.post("/api/decks/{deck_id}/bracket")
async def api_request_deck_bracket(deck_id: str):
    """Queue a re-evaluation for the worker (202). Returns immediately."""
    deck, err = _bracket_deck(deck_id)
    if err:
        return err
    rid = registry.registry_id_of(deck)
    try:
        bracket_worker.request_evaluation(r, rid)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"success": False, "status": "unavailable",
                                                      "registry_id": rid, "error": str(exc)[:200]})
    return JSONResponse(status_code=202, content={"success": True, "queued": True, "registry_id": rid})


# ---------------------------------------------------------------------------
# Personality scores (PS-3): stored per deck by the worker process
# (analytics/personality_store.py, run each bracket-worker tick). These
# routes read personality:deck:{registry_id}; they never score.
# ---------------------------------------------------------------------------


@app.get("/api/decks/{deck_id}/personality")
async def api_deck_personality(deck_id: str):
    """Stored PersonalityScores. 404 status=not_scored until the worker has
    scored the deck; 500 status=error when scoring failed and no earlier
    scores exist; stale=true when the inputs changed since (the worker
    rescores on its own)."""
    deck = _find_deck_any(deck_id)
    if not deck:
        return JSONResponse(status_code=404, content={
            "success": False, "status": "deck_not_found", "error": f"Deck '{deck_id}' not found"})
    rid = registry.registry_id_of(deck)
    try:
        rec = personality_store.load_record(r, rid)
        queued = rid in personality_store.pending(r)
        current = await asyncio.to_thread(personality_store.current_input_hash, r, deck)
    except Exception as exc:  # noqa: BLE001 - Redis trouble: say so, don't 500
        return JSONResponse(status_code=503, content={"success": False, "status": "unavailable",
                                                      "registry_id": rid, "error": str(exc)[:200]})
    base = {"registry_id": rid, "deck_name": deck.get("name"), "pending": queued}
    if not rec or not rec.get("scores"):
        if rec and rec.get("status") == "error":
            return JSONResponse(status_code=500, content={
                "success": False, **base, "status": "error", "error": rec.get("error"),
                "error_at": rec.get("error_at"), "personality": None})
        return JSONResponse(status_code=404, content={
            "success": False, **base, "status": "not_scored", "error": "Not scored yet.", "personality": None})
    result_hash = rec.get("result_hash") or rec.get("input_hash")
    from analytics import journal as journal_mod
    from analytics import personality_read

    rec_read = await asyncio.to_thread(personality_read.load_read, r, rid)
    read_data = None
    oracle_status = "not_read"
    if rec_read and rec_read.get("status") == "ok":
        journal_rec = await asyncio.to_thread(journal_mod.load_journal, r, deck)
        cur_read_hash = personality_read.compute_input_hash(deck, journal_rec)
        read_data = rec_read.get("read")
        oracle_status = "ok" if rec_read.get("input_hash") == cur_read_hash else "stale"

    return {"success": True, **base, "status": rec.get("status") or "ok", "computed_at": rec.get("computed_at"),
            "stale": result_hash != current, "error": rec.get("error"), "error_at": rec.get("error_at"),
            "personality": rec["scores"], "read": read_data, "oracle_read": read_data, "oracle_status": oracle_status}


@app.post("/api/decks/{deck_id}/personality/read")
async def api_deck_personality_read(deck_id: str, request: Request):
    """Trigger an async Oracle personality read (30s to minutes), or return cached read.
    Poll GET /api/decks/{deck_id}/personality/read/jobs/{job_id}."""
    from analytics import journal as journal_mod
    from analytics import personality_read

    deck = _find_deck_any(deck_id)
    if not deck:
        return JSONResponse(status_code=404, content={
            "success": False, "status": "deck_not_found", "error": f"Deck '{deck_id}' not found"})
    rid = registry.registry_id_of(deck)
    body = await _json_body(request)
    force = bool(isinstance(body, dict) and body.get("force"))

    journal_rec = await asyncio.to_thread(journal_mod.load_journal, r, deck)
    cur_hash = personality_read.compute_input_hash(deck, journal_rec)
    cached = await asyncio.to_thread(personality_read.load_read, r, rid)

    if not force and cached and cached.get("status") == "ok" and cached.get("input_hash") == cur_hash:
        return JSONResponse(status_code=200, content={
            "success": True, "cached": True, "status": "done",
            "registry_id": rid, "read": cached.get("read")
        })

    try:
        job = await asyncio.to_thread(personality_read.new_job, r, deck)
    except personality_read.PersonalityReadBusy as exc:
        return JSONResponse(status_code=409 if exc.per_deck else 429,
                            content={"success": False, "error": str(exc), "error_kind": "busy"})
    except personality_read.PersonalityReadError as exc:
        return _bad_request(exc)

    offline = await asyncio.to_thread(_oracle_preflight)
    if offline is not None:
        personality_read.release(job["deck_id"], job["job_id"])
        await asyncio.to_thread(r.delete, personality_read.job_key(job["job_id"]))
        return offline

    try:
        personality_read.start_job(r, job, deck, _oracle_client)
    except Exception:
        personality_read.release(job["deck_id"], job["job_id"])
        raise

    return JSONResponse(status_code=202, content={
        "success": True, "job_id": job["job_id"], "status": job["status"],
        "status_url": f"/api/decks/{urllib.parse.quote(rid, safe='')}/personality/read/jobs/{job['job_id']}"
    })


@app.get("/api/decks/{deck_id}/personality/read/jobs/{job_id}")
async def api_deck_personality_read_job(deck_id: str, job_id: str):
    from analytics import personality_read

    deck = _find_deck_any(deck_id)
    if not deck:
        return JSONResponse(status_code=404, content={
            "success": False, "status": "deck_not_found", "error": f"Deck '{deck_id}' not found"})
    rid = registry.registry_id_of(deck)
    job = await asyncio.to_thread(personality_read.load_job, r, job_id)
    if not job or job.get("deck_id") != rid:
        return JSONResponse(status_code=404, content={"success": False, "error": f"Job '{job_id}' not found"})
    return {"success": True, "job": job}


@app.get("/api/decks/{deck_id}/personality/read")
async def api_deck_personality_read_get(deck_id: str):
    from analytics import journal as journal_mod
    from analytics import personality_read

    deck = _find_deck_any(deck_id)
    if not deck:
        return JSONResponse(status_code=404, content={
            "success": False, "status": "deck_not_found", "error": f"Deck '{deck_id}' not found"})
    rid = registry.registry_id_of(deck)
    rec = await asyncio.to_thread(personality_read.load_read, r, rid)
    if not rec or rec.get("status") != "ok":
        return JSONResponse(status_code=404, content={
            "success": False, "status": "not_read", "error": "No personality read for this deck yet."})
    journal_rec = await asyncio.to_thread(journal_mod.load_journal, r, deck)
    cur_hash = personality_read.compute_input_hash(deck, journal_rec)
    return {
        "success": True,
        "registry_id": rid,
        "created_at": rec.get("created_at"),
        "stale": rec.get("input_hash") != cur_hash,
        "read": rec.get("read"),
        "rulebook": rec.get("rulebook"),
    }


@app.post("/api/decks/{deck_id}/personality")
async def api_request_deck_personality(deck_id: str):
    """Queue a rescore for the worker (202). Returns immediately."""
    deck = _find_deck_any(deck_id)
    if not deck:
        return JSONResponse(status_code=404, content={
            "success": False, "status": "deck_not_found", "error": f"Deck '{deck_id}' not found"})
    rid = registry.registry_id_of(deck)
    try:
        personality_store.request(r, rid)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"success": False, "status": "unavailable",
                                                      "registry_id": rid, "error": str(exc)[:200]})
    return JSONResponse(status_code=202, content={"success": True, "queued": True, "registry_id": rid})


@app.get("/api/profile/personality")
async def api_profile_personality(scope: str = "physical"):
    """PlayerProfile over every stored deck score (analytics.personality_profile).
    ?scope=physical (default) | all. Failed decks are listed, never averaged."""
    if scope not in ("physical", "all"):
        return JSONResponse(status_code=400, content={"success": False, "error": "scope must be physical or all"})
    try:
        decks = registry.list_decks(r)
        scored, failed, missing = personality_store.load_all(r, decks)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"success": False, "status": "unavailable",
                                                      "error": str(exc)[:200]})
    in_scope = [d for d in decks if scope == "all" or d.get("status") == "physical"]
    profile = personality_profile.build_player_profile(scored, failed=failed, scope=scope,
                                                       decks_total=len(in_scope))
    body = profile.model_dump()
    body["not_scored"] = sum(1 for d in missing if scope == "all" or d.get("status") == "physical")
    body["scope_counts"] = {"physical": sum(1 for d in decks if d.get("status") == "physical"), "all": len(decks)}
    return {"success": True, "profile": body}


@app.on_event("startup")
async def _start_interactions_nightly():
    interactions_service.start_nightly_thread(r)


# ---------------------------------------------------------------------------
# Deck Journal + review check-in (AN-5) -- see docs/DECK_JOURNAL.md
# ---------------------------------------------------------------------------


def _journal_error(exc: Exception, status: int = 400):
    return JSONResponse(status_code=status, content={"success": False, "error": str(exc),
                                                     "errors": getattr(exc, "errors", [str(exc)])})


@app.get("/api/journal/defaults")
async def api_journal_defaults():
    """Global per-card price caps (USD). Each deck's journal may override them."""
    return {"success": True, "defaults": journal_mod.get_defaults(r)}


@app.put("/api/journal/defaults")
async def api_set_journal_defaults(request: Request):
    try:
        d = journal_mod.set_defaults(r, await _json_body(request))
    except journal_mod.JournalError as exc:
        return _journal_error(exc)
    return {"success": True, "defaults": d}


@app.get("/api/decks/{deck_id}/journal")
async def api_deck_journal(deck_id: str):
    """The deck's journal (user_intent + pets, novelties, per-card caps), the
    effective caps, the deck's card names (pet autocomplete) and live metrics."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    view = await asyncio.to_thread(journal_mod.journal_view, r, deck)
    ctx = await asyncio.to_thread(journal_mod.collect_context, r, deck, journal_mod.load_journal(r, deck))
    view.update(success=True, deck_cards=sorted(journal_mod.deck_entries(deck)),
                metrics={k: ctx.get(k) for k in ("power", "price", "on_plan", "unpriced")},
                pillars=ctx.get("pillars") or [])
    return view


@app.put("/api/decks/{deck_id}/journal")
async def api_save_deck_journal(deck_id: str, request: Request):
    """Save (merge whole sections). The first save records the baseline the
    first check-in diffs against."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request)
    if not isinstance(body, dict):
        return _journal_error(journal_mod.JournalError("Journal must be a JSON object."))
    snap = None
    if journal_mod.load_journal(r, deck) is None:
        ctx = await asyncio.to_thread(journal_mod.collect_context, r, deck, body)
        snap = journal_mod.snapshot(deck, ctx)
    try:
        rec = journal_mod.save_journal(r, deck, body, snapshot=snap)
    except journal_mod.JournalError as exc:
        return _journal_error(exc)
    _wake_bracket_worker(registry.registry_id_of(deck))
    return {"success": True, "journal": journal_mod.public(rec), "caps": journal_mod.effective_caps(r, rec)}


@app.get("/api/decks/{deck_id}/checkin")
async def api_get_checkin(deck_id: str):
    """The check-in in progress, or null."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    review = journal_mod.load_open(r, deck)
    if not review:
        return {"success": True, "checkin": None}
    return {"success": True,
            "checkin": journal_mod.review_view(r, deck, review, journal_mod.load_journal(r, deck) or {})}


@app.post("/api/decks/{deck_id}/checkin")
async def api_start_checkin(deck_id: str):
    """Open (or resume) a review check-in with fresh metrics."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    rec = journal_mod.load_journal(r, deck)
    if not rec:
        return _journal_error(journal_mod.JournalError("Fill in the Deck Journal first."), 409)
    ctx = await asyncio.to_thread(journal_mod.collect_context, r, deck, rec)
    return {"success": True, "checkin": journal_mod.start_review(r, deck, ctx)}


@app.patch("/api/decks/{deck_id}/checkin/rows/{field}")
async def api_answer_checkin_row(deck_id: str, field: str, request: Request):
    """{"answer": "true"} | {"answer": "changed", "journal": {section: ...}} | {"answer": null} (undo)."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request) or {}
    if not isinstance(body, dict):
        return _journal_error(journal_mod.JournalError("Body must be a JSON object."))
    try:
        view = journal_mod.answer_row(r, deck, field, body.get("answer"), body.get("journal"))
    except journal_mod.NoOpenReview as exc:
        return _journal_error(exc, 409)
    except journal_mod.JournalError as exc:
        return _journal_error(exc)
    _wake_bracket_worker(registry.registry_id_of(deck))
    return {"success": True, "checkin": view}


@app.post("/api/decks/{deck_id}/checkin/finish")
async def api_finish_checkin(deck_id: str, request: Request):
    """Store the review in history ({"skipped": true} for Skip check-in)."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request) or {}
    try:
        rec = journal_mod.finish_review(r, deck, skipped=bool(isinstance(body, dict) and body.get("skipped")))
    except journal_mod.NoOpenReview as exc:
        return _journal_error(exc, 409)
    # UX_SPEC D4: finishing the check-in lands on Tune.
    nxt = "/deck/" + urllib.parse.quote(deck.get("name") or "") + "/tune"
    return {"success": True, "review": rec, "next": nxt}


@app.get("/api/decks/{deck_id}/reviews")
async def api_deck_reviews(deck_id: str, limit: int = 20):
    """Review history, newest first."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    return {"success": True, "reviews": journal_mod.list_reviews(r, deck, max(1, min(limit, 50)))}


# ---------------------------------------------------------------------------
# Tuner (AN-6) -- see docs/TUNER.md
# ---------------------------------------------------------------------------


@app.get("/api/decks/{deck_id}/tune")
async def api_deck_tune(deck_id: str, mode: str = "owned_first", sleeved: bool = True):
    """Shape fixes, swaps (cut -> best owned / best buy), weak performers and the
    pet notice. Deterministic; cached LLM 'why' lines are attached when present.
    mode = owned_first | owned_only | best; sleeved=false drops cards in other decks."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    try:
        out = await asyncio.to_thread(tuner_mod.compute, r, deck, mode, sleeved)
    except tuner_mod.TunerError as exc:
        return _bad_request(exc)
    return {"success": True, "tune": out}


@app.post("/api/decks/{deck_id}/tune/why")
async def api_deck_tune_why(deck_id: str, request: Request):
    """Ask the LLM (Gemini, then Nous) for the one-line 'why' of each swap. Prose
    only: every number is the tuner's. Cached per cut/add pair; cost is logged
    to /api/interactions/runs (trigger 'tuner')."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request) or {}
    mode = body.get("mode", "owned_first") if isinstance(body, dict) else "owned_first"
    sleeved = bool(body.get("sleeved", True)) if isinstance(body, dict) else True
    try:
        tune = await asyncio.to_thread(tuner_mod.compute, r, deck, mode, sleeved)
    except tuner_mod.TunerError as exc:
        return _bad_request(exc)
    from analytics.interactions.llm import default_chain
    out = await asyncio.to_thread(tuner_mod.generate_prose, r, deck, tune, default_chain(),
                                  tuner_mod.prose_context(r, deck))
    return JSONResponse(status_code=200 if out["llm"]["status"] in ("ok", "cached") else 502,
                        content={"success": out["llm"]["status"] in ("ok", "cached"), **out, "tune": tune})


@app.put("/api/decks/{deck_id}/tune/decisions")
async def api_deck_tune_decide(deck_id: str, request: Request):
    """{"cut": name, "decision": "own"|"buy"|"dismissed"|null, "add": name}. Decisions
    last until the next finished review."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request)
    if not isinstance(body, dict):
        return _bad_request(tuner_mod.TunerError("Body must be a JSON object."))
    try:
        st = tuner_mod.decide(r, deck, body.get("cut"), body.get("decision"), body.get("add"))
    except tuner_mod.TunerError as exc:
        return _bad_request(exc)
    return {"success": True, "state": st}


@app.put("/api/decks/{deck_id}/tune/adds")
async def api_deck_tune_adds(deck_id: str, request: Request):
    """Extra adds on the change list (basics from a shape fix, a combo piece): [{name, qty, source}]."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request)
    try:
        st = tuner_mod.set_adds(r, deck, (body or {}).get("adds") if isinstance(body, dict) else None)
    except tuner_mod.TunerError as exc:
        return _bad_request(exc)
    return {"success": True, "state": st}


# ---------------------------------------------------------------------------
# Theory (TH-2) -- see docs/THEORY_SPEC.md, docs/THEORY.md
# ---------------------------------------------------------------------------

THEORY_PREFLIGHT_TIMEOUT_S = 8.0


def _oracle_client():
    from analytics.oracle_client import OracleClient

    return OracleClient.from_env()


def _oracle_offline(detail: str = ""):
    from analytics.oracle_client import OFFLINE_MESSAGE

    body = {"success": False, "error": OFFLINE_MESSAGE, "error_kind": "oracle_offline"}
    if detail:
        body["detail"] = detail
    return JSONResponse(status_code=503, content=body)


def _oracle_preflight() -> Optional[JSONResponse]:
    """Fail fast (503) when oracle is not configured or not answering /v1/models."""
    from analytics.oracle_client import OracleAuthError, OracleError, OracleUnavailable

    try:
        client = _oracle_client()
        client.timeout = THEORY_PREFLIGHT_TIMEOUT_S
        client.models()
    except OracleUnavailable as exc:
        return _oracle_offline(exc.detail)
    except OracleAuthError:
        return _oracle_offline("oracle rejected the API key")
    except OracleError as exc:
        return _oracle_offline(str(exc)[:200])
    return None


def _theory_deck_id(deck) -> str:
    return deck.get("registry_id") or registry.registry_id_of(deck)


@app.post("/api/decks/{deck_id}/theory")
async def api_theory_generate(deck_id: str, request: Request):
    """{"theme": str, "commander_mode": "keep"|"open"} -> 202 {job_id}. Oracle writes the
    doc in the background (30 s to minutes); poll GET .../theory/jobs/{job_id}."""
    from analytics import theory as theory_mod

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request)
    if not isinstance(body, dict):
        return _bad_request(theory_mod.TheoryError("Body must be a JSON object."))
    theme = body.get("theme") if isinstance(body.get("theme"), str) else ""
    mode = body.get("commander_mode") or "keep"
    try:
        job = theory_mod.new_job(r, deck, theme, mode)
    except theory_mod.TheoryBusy as exc:
        return JSONResponse(status_code=409 if exc.per_deck else 429,
                            content={"success": False, "error": str(exc), "error_kind": "busy"})
    except theory_mod.TheoryError as exc:
        return _bad_request(exc)
    offline = await asyncio.to_thread(_oracle_preflight)
    if offline is not None:
        theory_mod.release(job["deck_id"], job["job_id"])
        r.delete(theory_mod.job_key(job["job_id"]))
        return offline
    try:
        theory_mod.start_job(r, job, deck, _oracle_client)
    except Exception:
        theory_mod.release(job["deck_id"], job["job_id"])
        raise
    return JSONResponse(status_code=202, content={
        "success": True, "job_id": job["job_id"], "status": job["status"],
        "status_url": f"/api/decks/{urllib.parse.quote(_theory_deck_id(deck), safe='')}/theory/jobs/{job['job_id']}"})


@app.get("/api/decks/{deck_id}/theory/jobs/{job_id}")
async def api_theory_job(deck_id: str, job_id: str):
    from analytics import theory as theory_mod

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    job = theory_mod.load_job(r, job_id)
    if not job or job.get("deck_id") != _theory_deck_id(deck):
        return JSONResponse(status_code=404, content={"success": False, "error": f"Job '{job_id}' not found"})
    return {"success": True, "job": job}


@app.get("/api/decks/{deck_id}/theory")
async def api_theory_list(deck_id: str):
    from analytics import theory as theory_mod

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    return {"success": True, "docs": theory_mod.list_docs(r, _theory_deck_id(deck))}


@app.get("/api/decks/{deck_id}/theory/{doc_id}")
async def api_theory_get(deck_id: str, doc_id: str):
    from analytics import theory as theory_mod

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    doc = theory_mod.load(r, _theory_deck_id(deck), doc_id)
    if doc is None:
        return JSONResponse(status_code=404, content={"success": False, "error": f"Theory '{doc_id}' not found"})
    return {"success": True, "doc": doc}


@app.delete("/api/decks/{deck_id}/theory/{doc_id}")
async def api_theory_delete(deck_id: str, doc_id: str):
    from analytics import theory as theory_mod

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    if not theory_mod.delete(r, _theory_deck_id(deck), doc_id):
        return JSONResponse(status_code=404, content={"success": False, "error": f"Theory '{doc_id}' not found"})
    return {"success": True, "deleted": doc_id}


@app.post("/api/decks/{deck_id}/tune/pet")
async def api_deck_tune_pet(deck_id: str, request: Request):
    """{"name": card}: add the card to the Journal's pets (Tune key `h`)."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request) or {}
    try:
        out = tuner_mod.mark_pet(r, deck, body.get("name") if isinstance(body, dict) else None)
    except (tuner_mod.TunerError, journal_mod.JournalError) as exc:
        return _bad_request(exc)
    return {"success": True, **out}

