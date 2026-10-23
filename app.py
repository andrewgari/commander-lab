from fastapi import FastAPI, Request, Query, BackgroundTasks
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
import markdown
import bleach
from typing import List, Optional
from datetime import datetime, timezone

import instances as instance_store
from instances import InstanceError
import registry
import reconstruct
import commander_roles
import inventory_view
import card_refs as card_refs_mod
import tcgplayer
import tags as tag_store
import lab_tags as lab_tag_store
import autotag
from autotag import engine as autotag_engine_mod
from autotag import tag_registry
from providers import fetch_deck, ProviderError
import linked_accounts
from linked_accounts import LinkedAccountError
import deck_links
from deck_links import DeckLinkError
import deck_export
import deck_sync
from deck_sync import DeckSyncError
from version import __version__, VERSION
import httpx
from src.services.repo_status import enrich_decks_with_repo_status
from analytics.interactions import service as interactions_service
from analytics.interactions import combos
from analytics import composition as composition_mod
from analytics import composition_service as comp_svc
from analytics.composition_models import CompositionValidationError
from analytics import categories as categories_mod
from analytics import identity as identity_mod
from analytics import insights
from analytics import journal as journal_mod
import deck_genre
from analytics import tuner as tuner_mod
from analytics import tune_store
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

import route_cache
app.middleware("http")(route_cache.make_route_cache_middleware(lambda: r))

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
# Hover preview: {{ card_ref_attrs(name, head.card_refs) }} marks a rendered
# card name / thumbnail with data-card-name + data-card-ref (card_refs.py).
templates.env.globals["card_ref_attrs"] = card_refs_mod.html_attrs

templates.env.globals["card_preview_attrs"] = card_refs_mod.preview_attrs
# "Buy on TCGplayer" cart button: {{ hud.tcg_cart(cards) }} -> tcgplayer.cart().
templates.env.globals["tcgplayer_cart"] = tcgplayer.cart

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


@app.get("/genres", response_class=HTMLResponse)
async def genres_view(request: Request):
    """Genre registry (RV-1): the list Deep Reviews label decks from."""
    return templates.TemplateResponse(request=request, name="genres.html")


@app.get("/archetypes", response_class=HTMLResponse)
async def archetypes_view(request: Request):
    """Archetype registry (vocab PR3): the global, user-editable list of deck archetypes."""
    return templates.TemplateResponse(request=request, name="archetypes.html")


@app.get("/staple-themes", response_class=HTMLResponse)
async def staple_themes_view(request: Request):
    """Staple theme registry (vocab PR5): user-editable card-name -> theme vocabulary used
    by the offline heuristic theme/archetype aligner (analytics/engine.py)."""
    return templates.TemplateResponse(request=request, name="staple_themes.html")


@app.get("/composition/presets", response_class=HTMLResponse)
async def composition_presets_view(request: Request):
    """Composition Presets registry: create/edit/delete, compare side by side."""
    return templates.TemplateResponse(request=request, name="composition_presets.html")


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
           "art_uid": None, "commander_roles": [], "cards": None, "lands": None, "pillars": None, "tune": None, "ideas": None, "registry_id": None,
           "genre": None, "card_refs": {}}
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
    try:
        # {card_key: CardRef} printing map for every card name on the deck
        # pages (hover preview; card_refs.py). Name-keyed so analytics output
        # that only carries names can still resolve the owned printing.
        out["card_refs"] = {k: card_refs_mod.compact(v) for k, v in card_refs_mod.deck_card_refs(r, deck).items()}
    except Exception:
        pass
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
    try:
        # GN view mode: the archetype / kindred / theme chip strip (None = hidden).
        rec = journal_mod.load_journal(r, deck)
        if rec:
            out["genre"] = deck_genre.header_strip(journal_mod.public(rec), deck_genre.creature_types(r), r)
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


@app.get("/deck/{deck_name}/review", response_class=HTMLResponse)
async def deck_review_view(request: Request, deck_name: str):
    """Review (RV-4, REVIEW_SPEC): the saved Deep Review. Read-only: never builds on page load."""
    return await _deck_tab(request, deck_name, "deck_review.html", "review", extra=_review_page_context)


def _review_page_context(deck_name: str) -> dict:
    """Saved doc + view-model. Never raises (Redis down = a quiet error state)."""
    from analytics import review_doc, review_view, theory_view

    out = {"rv": review_view, "tv": theory_view, "meta": {"exists": False}, "sections": [], "load_error": False}
    try:
        deck = next((d for d in registry.list_decks(r) if d.get("name") == deck_name), None)
        if deck:
            doc = review_doc.refresh_stale(r, {**deck, "registry_id": _theory_deck_id(deck)})
            out.update(meta=review_view.doc_meta(doc), sections=review_view.sections(doc) if doc else [])
    except Exception:
        out["load_error"] = True
    return out


@app.get("/deck/{deck_name}/sim", response_class=HTMLResponse)
async def deck_sim_view(request: Request, deck_name: str):
    """Simulate (SM-4, docs/design/sim/UX_SPEC.md): one-off Forge simulation + Oracle sim review.

    The page itself is static chrome; static/js/deck_sim.js fetches /api/decks/{id}/sim and
    renders the builder/job/report exactly like docs/design/sim/mockup.html, but against the
    real API instead of sample data.
    """
    return await _deck_tab(request, deck_name, "deck_sim.html", "sim")


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
           "power_tol": engine_mod.POWER_ALIGN_TOLERANCE,
           "genre_options": deck_genre.page_options(), "creature_types": deck_genre.creature_types(r)}
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



_CHANGELOG_ALLOWED_TAGS = [
    "p", "ul", "ol", "li", "strong", "em", "code", "pre", "blockquote",
    "h1", "h2", "h3", "h4", "a", "br", "hr",
]
_CHANGELOG_ALLOWED_ATTRS = {"a": ["href", "title", "rel"]}


def render_release_body(body: str | None) -> str:
    """Render a full GitHub release body as sanitized HTML.

    Unlike the old extract_short_description(), this never truncates --
    the full prose, headings, and bullet lists are preserved so the
    changelog page shows the complete, readable release notes.
    """
    if not body:
        return ""
    html_out = markdown.markdown(body.strip(), extensions=["fenced_code", "nl2br"])
    return bleach.clean(
        html_out,
        tags=_CHANGELOG_ALLOWED_TAGS,
        attributes=_CHANGELOG_ALLOWED_ATTRS,
        strip=True,
    )


def extract_short_description(body: str | None, max_length: int = 280) -> str:
    """Deprecated: kept only for backward compatibility with callers that
    still want a plain-text summary. The changelog page itself now uses
    render_release_body() and never truncates. No "..." is ever appended
    here either -- callers that need a hard-capped label should truncate
    on their own with an explicit, visible affordance (e.g. a tooltip).
    """
    if not body:
        return ""
    lines = [line.strip() for line in body.strip().splitlines()]
    content_lines = [line for line in lines if line and not line.startswith("#")]
    if not content_lines:
        content_lines = [line for line in lines if line]
    if not content_lines:
        return ""
    return " ".join(content_lines)


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
            rel_dict["body_html"] = render_release_body(body)
            pub_date = rel.get("published_at")
            rel_dict["release_date"] = pub_date[:10] if pub_date else "Unknown date"
            formatted_releases.append(rel_dict)

    return templates.TemplateResponse(
        request=request,
        name="changelog.html",
        context={"releases": formatted_releases, "error_msg": error_msg},
    )

@app.get("/reconstruct", response_class=HTMLResponse)
async def reconstruct_landing_view(request: Request):
    providers = [p.strip() for p in os.getenv("ENABLED_PROVIDERS", "archidekt,moxfield,commandersalt").split(",") if p.strip()]
    return templates.TemplateResponse(
        request=request,
        name="reconstruct.html",
        context={"session_id": "", "providers": providers},
    )

@app.get("/reconstruct/{session_id}", response_class=HTMLResponse)
async def reconstruct_view(session_id: str, request: Request):
    providers = [p.strip() for p in os.getenv("ENABLED_PROVIDERS", "archidekt,moxfield,commandersalt").split(",") if p.strip()]
    return templates.TemplateResponse(
        request=request,
        name="reconstruct.html",
        context={"session_id": session_id, "providers": providers},
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


@app.post("/api/decks/{deck_id}/decommission")
async def decommission_deck(deck_id: str, request: Request):
    """Decommission a physical deck (single transaction): remove the status tag
    (default "removed", override with JSON body {"tag": "..."}), move the deck
    to the "retired" category, and return all its cards to the owned pool
    (instances in_deck -> in_collection; nothing deleted). Idempotent.
    Errors: 404 deck not found, 400 deck is not physical.
    """
    try:
        data = await request.json()
    except Exception:
        data = {}
    tag = (data or {}).get("tag") or registry.DEFAULT_DECOMMISSION_TAG
    try:
        result = registry.decommission_deck(r, deck_id, tag=tag)
    except registry.DecommissionError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})
    except ValueError as e:
        return JSONResponse(status_code=404, content={"success": False, "error": str(e)})
    deck = result["deck"]
    return {
        "success": True,
        "deck_id": registry.registry_id_of(deck),
        "status": deck["status"],
        "tags": deck.get("tags", []),
        "already_decommissioned": result["already_decommissioned"],
        "tag_removed": result["tag_removed"],
        "unassigned_count": result["unassigned_count"],
        "unassigned_instance_ids": result["unassigned_instance_ids"],
    }


@app.post("/api/decks/decommission")
async def decommission_decks_bulk(request: Request):
    """Bulk decommission physical decks. Same logic as the single-deck endpoint
    (registry.decommission_decks), one Redis transaction for all that succeed.

    Body: {"deck_ids": ["archidekt:42", ...], "tag": "removed" (optional)}
    200: {"success": true, "requested": N, "succeeded": [...], "failed":
          [{"deck_id", "reason", "code"}]}  -- per-deck results; `success` is
          true when the request was valid even if some decks failed
          (check `failed`). Duplicates are collapsed; already-decommissioned
          decks are no-op successes. 400: empty/invalid/oversized deck_ids.
    """
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    tag = data.get("tag") or registry.DEFAULT_DECOMMISSION_TAG
    try:
        result = registry.decommission_decks(r, data.get("deck_ids"), tag=tag)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})
    return {
        "success": True,
        "requested": len(result["succeeded"]) + len(result["failed"]),
        **result,
    }


# ---------------------------------------------------------------------------
# Reconstruct flow endpoints (decommission variant + review/rebuild)
# ---------------------------------------------------------------------------


@app.post("/api/reconstruct/start")
@app.post("/api/reconstruct")
async def start_reconstruct(request: Request, dry_run: Optional[bool] = Query(None)):
    """Start a Reconstruct flow on 1 or 2 physical decks.
    Captures pre-retirement decklist snapshots, decommissions each source deck
    identically to standard decommission (moves to "retired", removes tag,
    unassigns in_deck instances to in_collection), and initializes a Reconstruct
    session in status 'pending_preferences'.

    Body: {"deck_ids": ["archidekt:42", "archidekt:43"], "tag": "removed" (optional)}
          or {"deck_id": "archidekt:42"}
    """
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    deck_ids = data.get("deck_ids")
    if deck_ids is None and "deck_id" in data:
        deck_ids = [data["deck_id"]]
    if deck_ids is None:
        deck_ids = []
    tag = data.get("tag") or registry.DEFAULT_DECOMMISSION_TAG
    if dry_run is not None:
        is_dry_run = bool(dry_run)
    else:
        raw_dry = data.get("dry_run", False)
        is_dry_run = raw_dry.strip().lower() in ("1", "true", "yes", "on") if isinstance(raw_dry, str) else bool(raw_dry)

    try:
        session = reconstruct.start_reconstruct(r, deck_ids, tag=tag, dry_run=is_dry_run)
    except registry.DecommissionError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})
    except ValueError as e:
        msg = str(e)
        status_code = 404 if "not found" in msg.lower() else 400
        return JSONResponse(status_code=status_code, content={"success": False, "error": msg})

    return JSONResponse(
        status_code=201,
        content={
            "success": True,
            "session_id": session["session_id"],
            "session": session,
        },
    )


@app.get("/api/reconstruct/sessions")
async def list_reconstruct_sessions(limit: int = 50):
    """List recent Reconstruct sessions."""
    sessions = reconstruct.list_sessions(r, limit=limit)
    return {"success": True, "sessions": sessions}


@app.get("/api/reconstruct/{session_id}/pools")
async def get_reconstruct_pools(session_id: str):
    """Tier 1 / Tier 2 available pools for a session (same counting for dry runs)."""
    try:
        pools_data = reconstruct.get_session_pools(r, session_id)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"success": False, "error": str(e)})
    return {"success": True, **pools_data}


@app.get("/api/reconstruct/{session_id}")
async def get_reconstruct_session(session_id: str):
    """Fetch a Reconstruct session by ID, including source decklists and status."""
    session = reconstruct.get_session(r, session_id)
    if not session:
        return JSONResponse(status_code=404, content={"success": False, "error": f"session not found: {session_id}"})
    return {"success": True, "session": session}


@app.post("/api/reconstruct/{session_id}/preferences")
async def submit_reconstruct_preferences(session_id: str, request: Request):
    """Submit target deck preferences (for up to 2 decks).

    Body: {"preferences": [{"commanders": [...], "themes": [...], ...}]}
          or [{"commanders": [...], ...}]
    """
    try:
        data = await request.json()
    except Exception:
        data = {}

    prefs = data.get("preferences") if isinstance(data, dict) and "preferences" in data else data
    try:
        session = reconstruct.submit_preferences(r, session_id, prefs)
    except ValueError as e:
        msg = str(e)
        status_code = 404 if "not found" in msg.lower() else 400
        return JSONResponse(status_code=status_code, content={"success": False, "error": msg})

    return {"success": True, "session": session}


@app.post("/api/reconstruct/{session_id}/build")
async def trigger_reconstruct_build(
    session_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    sync: Optional[bool] = Query(None),
):
    """Trigger the deck builder for a Reconstruct session.

    Calls pluggable BaseReconstructBuilder. If sync is True (via query param ?sync=true
    or JSON body {"sync": true}), builds inline; otherwise runs as a background task.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    is_sync = sync if sync is not None else bool(body.get("sync", False))

    try:
        session = reconstruct.trigger_build(r, session_id, sync=is_sync)
    except ValueError as e:
        msg = str(e)
        status_code = 404 if "not found" in msg.lower() else 400
        return JSONResponse(status_code=status_code, content={"success": False, "error": msg})

    if not is_sync:
        background_tasks.add_task(reconstruct.execute_build, r, session_id)
        return JSONResponse(
            status_code=202,
            content={
                "success": True,
                "status": session["status"],
                "session": session,
            },
        )

    return {
        "success": True,
        "status": session["status"],
        "session": session,
    }


@app.post("/api/export/deck")
async def export_deck_api(request: Request):
    """Create a NEW deck on Moxfield and/or Archidekt from a decklist. Body:
    {"name", "cards": [{"name","quantity"}], "commanders": [], "providers":
    ["moxfield","archidekt"], "description", "private": true, "dry_run": bool}.
    Per-provider outcomes are in results; 200 if all succeeded, 207 if any failed."""
    data = await _sync_body(request)
    try:
        out = await asyncio.to_thread(
            deck_export.export_decklist, data.get("name"), data.get("cards") or [],
            data.get("commanders") or [], data.get("providers"),
            data.get("description") or "", data.get("private", True) is not False,
            bool(data.get("dry_run")))
    except deck_export.ExportError as e:
        return JSONResponse(status_code=e.status, content={"success": False, "code": e.code, "error": str(e)})
    return JSONResponse(status_code=200 if out["success"] else 207, content=out)


@app.post("/api/reconstruct/{session_id}/drafts/{draft_index}/export")
async def export_reconstruct_draft(session_id: str, draft_index: int, request: Request):
    """Export one Reconstruct draft as a new deck on Moxfield/Archidekt.
    Body (optional): {"providers": [...], "description", "private", "dry_run"}."""
    data = await _sync_body(request)
    try:
        out = await asyncio.to_thread(
            deck_export.export_draft, r, session_id, draft_index, data.get("providers"),
            data.get("description") or "", data.get("private", True) is not False,
            bool(data.get("dry_run")))
    except deck_export.ExportError as e:
        return JSONResponse(status_code=e.status, content={"success": False, "code": e.code, "error": str(e)})
    return JSONResponse(status_code=200 if out["success"] else 207, content=out)


@app.get("/api/reconstruct/{session_id}/drafts")
async def get_reconstruct_drafts(session_id: str):
    """Fetch generated draft decks for a Reconstruct session."""
    session = reconstruct.get_session(r, session_id)
    if not session:
        return JSONResponse(status_code=404, content={"success": False, "error": f"session not found: {session_id}"})
    return {
        "success": True,
        "status": session["status"],
        "drafts": session.get("drafts", []),
    }


@app.post("/api/reconstruct/{session_id}/accept")
async def accept_reconstruct_draft(session_id: str, request: Request):
    """Accept one or more drafts, creating the new decks and dedicating cards.

    Body (optional): {"draft_index": 0} or {"draft_indices": [0, 1]}, {"status": "physical"}
    """
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    draft_indices = data.get("draft_indices")
    if draft_indices is None and "draft_index" in data:
        draft_indices = [data["draft_index"]]
    status = data.get("status", "physical")

    try:
        result = reconstruct.accept_draft(r, session_id, draft_indices=draft_indices, status=status)
    except ValueError as e:
        msg = str(e)
        status_code = 404 if "not found" in msg.lower() else 400
        return JSONResponse(status_code=status_code, content={"success": False, "error": msg})

    return {
        "success": True,
        "session": result["session"],
        "created_decks": result["created_decks"],
    }


@app.post("/api/reconstruct/{session_id}/abandon")
async def abandon_reconstruct(session_id: str, request: Request):
    """Abandon a Reconstruct session.

    Body (optional): {"reason": "User abandoned draft"}
    """
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    reason = data.get("reason", "")

    try:
        session = reconstruct.abandon_reconstruct(r, session_id, reason=reason)
    except ValueError as e:
        msg = str(e)
        status_code = 404 if "not found" in msg.lower() else 400
        return JSONResponse(status_code=status_code, content={"success": False, "error": msg})

    return {"success": True, "session": session}


@app.post("/api/decks/{deck_id}/decommission")
async def decommission_deck(deck_id: str, request: Request):
    """Decommission a physical deck (single transaction): remove the status tag
    (default "removed", override with JSON body {"tag": "..."}), move the deck
    to the "retired" category, and return all its cards to the owned pool
    (instances in_deck -> in_collection; nothing deleted). Idempotent.
    Errors: 404 deck not found, 400 deck is not physical.
    """
    try:
        data = await request.json()
    except Exception:
        data = {}
    tag = (data or {}).get("tag") or registry.DEFAULT_DECOMMISSION_TAG
    try:
        result = registry.decommission_deck(r, deck_id, tag=tag)
    except registry.DecommissionError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})
    except ValueError as e:
        return JSONResponse(status_code=404, content={"success": False, "error": str(e)})
    deck = result["deck"]
    return {
        "success": True,
        "deck_id": registry.registry_id_of(deck),
        "status": deck["status"],
        "tags": deck.get("tags", []),
        "already_decommissioned": result["already_decommissioned"],
        "tag_removed": result["tag_removed"],
        "unassigned_count": result["unassigned_count"],
        "unassigned_instance_ids": result["unassigned_instance_ids"],
    }


@app.post("/api/decks/decommission")
async def decommission_decks_bulk(request: Request):
    """Bulk decommission physical decks. Same logic as the single-deck endpoint
    (registry.decommission_decks), one Redis transaction for all that succeed.

    Body: {"deck_ids": ["archidekt:42", ...], "tag": "removed" (optional)}
    200: {"success": true, "requested": N, "succeeded": [...], "failed":
          [{"deck_id", "reason", "code"}]}  -- per-deck results; `success` is
          true when the request was valid even if some decks failed
          (check `failed`). Duplicates are collapsed; already-decommissioned
          decks are no-op successes. 400: empty/invalid/oversized deck_ids.
    """
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    tag = data.get("tag") or registry.DEFAULT_DECOMMISSION_TAG
    try:
        result = registry.decommission_decks(r, data.get("deck_ids"), tag=tag)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})
    return {
        "success": True,
        "requested": len(result["succeeded"]) + len(result["failed"]),
        **result,
    }


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


def _sync_error(e: DeckSyncError, operation: str, provider: Optional[str]) -> JSONResponse:
    return JSONResponse(status_code=e.status, content={
        "success": False, "operation": operation, "provider": provider,
        "code": e.code, "error": str(e),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


async def _sync_body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001 - empty body is fine
        return {}
    return data if isinstance(data, dict) else {}


@app.get("/api/decks/{deck_id}/sync/status")
async def deck_sync_status(deck_id: str):
    """Per-provider Update/Push capabilities and last_updated_at /
    last_pushed_at timestamps. Read-only; never contacts a provider."""
    try:
        return await asyncio.to_thread(deck_sync.sync_status, r, deck_id)
    except DeckSyncError as e:
        return _sync_error(e, "status", None)


@app.post("/api/decks/{deck_id}/sync/pull")
async def deck_sync_pull(deck_id: str, request: Request):
    """UPDATE (external -> commander.lab): pull the linked Archidekt/Moxfield
    deck into this deck. Body (optional): {"provider": "archidekt"|"moxfield",
    "dry_run": bool}. No provider = every linked source, merged. Explicit
    user action only -- nothing calls this automatically."""
    data = await _sync_body(request)
    provider = data.get("provider") or None
    dry_run = bool(data.get("dry_run"))
    try:
        result = await asyncio.to_thread(deck_sync.update_deck, r, deck_id, provider, dry_run)
    except DeckSyncError as e:
        return _sync_error(e, "update", provider)
    if not dry_run:
        interactions_service.start_rebuild(r, trigger="sync", deck_ids=[result["registry_id"]])
        _wake_bracket_worker(result["registry_id"])
    return result


@app.post("/api/decks/{deck_id}/sync/push")
async def deck_sync_push(deck_id: str, request: Request):
    """PUSH (commander.lab -> external): overwrite the linked deck on one
    provider with this deck's cardlist. Body: {"provider": "archidekt"|
    "moxfield", "dry_run": bool}. 501 push_not_configured when the provider's
    credential env var is unset. Explicit user action only."""
    data = await _sync_body(request)
    provider = data.get("provider") or None
    dry_run = bool(data.get("dry_run"))
    try:
        return await asyncio.to_thread(deck_sync.push_deck, r, deck_id, provider, dry_run)
    except DeckSyncError as e:
        return _sync_error(e, "push", provider)


@app.post("/api/decks/{deck_id}/sync")
async def sync_deck(deck_id: str):
    """DEPRECATED: use POST /api/decks/{id}/sync/pull. Kept so the existing
    deck-modal button keeps working; it is an Update from every linked
    provider (explicit user action), with the legacy report shape."""
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
        "lab_tags": lab_tag_store.with_source(r, card_name, card_lab_tags),
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


@app.get("/api/autotag-tags")
async def list_autotag_tags():
    """The Auto Tag engine's editable vocabulary (Vocab cleanup PR4): the FUNCTION_TAGS/
    THEME_TAGS taxonomy the heuristic engine is allowed to assign, as a Redis-backed registry
    instead of hardcoded lists. Builtin entries were seeded once from the original taxonomy;
    renamed/deleted entries never come back."""
    tags = tag_registry.list_tags(r)
    return {"tags": tags, "categories": list(tag_registry.CATEGORIES)}


@app.post("/api/autotag-tags")
async def create_autotag_tag(request: Request):
    data = await request.json()
    try:
        tag = tag_registry.create_tag(r, data.get("name"), data.get("category"))
    except tag_registry.TagRegistryError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"success": True, "tag": tag}


@app.patch("/api/autotag-tags/{tag_id}")
async def rename_autotag_tag(tag_id: str, request: Request):
    data = await request.json()
    try:
        tag = tag_registry.rename_tag(r, tag_id, data.get("name"))
    except tag_registry.TagNotFound:
        return JSONResponse({"error": "Tag not found"}, status_code=404)
    except tag_registry.TagRegistryError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return {"success": True, "tag": tag}


@app.delete("/api/autotag-tags/{tag_id}")
async def delete_autotag_tag(tag_id: str):
    try:
        tag = tag_registry.delete_tag(r, tag_id)
    except tag_registry.TagNotFound:
        return JSONResponse({"error": "Tag not found"}, status_code=404)
    return {"success": True, "tag": tag}


@app.post("/api/card/{card_name:path}/tags")
async def update_card_tags(card_name: str, request: Request):
    """Add or remove Lab tags from a card.

    A tag added through this endpoint is always "manual" — the only other
    source, "learned", comes from sync.py's Archidekt-confidence seeding.
    """
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
        lab_tag_store.set_source(r, card_name, tag, lab_tag_store.MANUAL)
    elif action == "remove" and tag in lab_tags[card_name]:
        lab_tags[card_name].remove(tag)
        lab_tag_store.clear_source(r, card_name, tag)
    
    # Save Lab tags
    r.set("lab_tags", json.dumps(lab_tags))
    
    return {
        "success": True,
        "lab_tags": lab_tag_store.with_source(r, card_name, lab_tags[card_name]),
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
            # Printing identity for the hover preview (audit G3, additive).
            "uid": inst.get("archidekt_uid") or "",
            "set": inst.get("set") or "",
            "collector_number": inst.get("collector_number") or "",
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
# Auto-tag endpoints — deck-level & per-deck-card tags (see docs/AUTO_TAG_DESIGN.md)
# ---------------------------------------------------------------------------

def _enrich_deck_for_autotag(r, deck: dict) -> dict:
    deck_copy = dict(deck)
    cards = list(deck.get("cards", []))
    commanders = list(deck.get("commanders", []))

    names_to_fetch = set()
    for c in cards + commanders:
        if isinstance(c, dict):
            if not c.get("oracle_text") or not c.get("type_line"):
                name = c.get("name")
                if name:
                    names_to_fetch.add(name)
        elif isinstance(c, str):
            names_to_fetch.add(c)

    metadata_by_name = {}
    if names_to_fetch:
        mget = r.mget if hasattr(r, "mget") else (lambda ks: [r.get(k) for k in ks])
        names_list = sorted(names_to_fetch)
        interaction_raws = mget([f"card_interactions:{n}" for n in names_list])
        meta_raws = mget([f"card_meta:{n}" for n in names_list])
        for name, iraw, mraw in zip(names_list, interaction_raws, meta_raws):
            mdata = {}
            if iraw:
                try:
                    cobj = (json.loads(iraw) or {}).get("card")
                    if cobj:
                        mdata = {
                            "type_line": cobj.get("type_line") or "",
                            "oracle_text": cobj.get("oracle_text") or "",
                            "keywords": cobj.get("keywords") or [],
                            "cmc": cobj.get("cmc") or 0,
                            "produced_mana": cobj.get("produced_mana") or [],
                            "game_changer": cobj.get("game_changer", False),
                        }
                except Exception:
                    pass
            if mraw and (not mdata.get("type_line") or not mdata.get("oracle_text")):
                try:
                    m = json.loads(mraw)
                    if m:
                        st = list(m.get("super_types") or [])
                        t = [m.get("type") or ""]
                        sub = list(m.get("sub_types") or [])
                        head = " ".join(st + t).strip()
                        tl = head + (" — " + " ".join(sub) if sub else "")
                        if not mdata.get("type_line"):
                            mdata["type_line"] = tl
                        if not mdata.get("oracle_text"):
                            mdata["oracle_text"] = m.get("oracle_text") or ""
                        if not mdata.get("keywords"):
                            mdata["keywords"] = m.get("keywords") or []
                        if not mdata.get("cmc"):
                            mdata["cmc"] = m.get("cmc") or 0
                        if not mdata.get("produced_mana"):
                            mdata["produced_mana"] = m.get("produced_mana") or []
                        if "game_changer" not in mdata:
                            mdata["game_changer"] = m.get("game_changer", False)
                except Exception:
                    pass
            metadata_by_name[name] = mdata

    enriched_cards = []
    for c in cards:
        if isinstance(c, dict):
            c_dict = dict(c)
            name = c_dict.get("name")
            if name and name in metadata_by_name:
                meta = metadata_by_name[name]
                for k, v in meta.items():
                    if not c_dict.get(k):
                        c_dict[k] = v
            enriched_cards.append(c_dict)
        elif isinstance(c, str):
            c_dict = {"name": c}
            if c in metadata_by_name:
                c_dict.update(metadata_by_name[c])
            enriched_cards.append(c_dict)

    enriched_commanders = []
    for c in commanders:
        if isinstance(c, dict):
            c_dict = dict(c)
            name = c_dict.get("name")
            if name and name in metadata_by_name:
                meta = metadata_by_name[name]
                for k, v in meta.items():
                    if not c_dict.get(k):
                        c_dict[k] = v
            enriched_commanders.append(c_dict)
        elif isinstance(c, str):
            c_dict = {"name": c}
            if c in metadata_by_name:
                c_dict.update(metadata_by_name[c])
            enriched_commanders.append(c_dict)

    deck_copy["cards"] = enriched_cards
    deck_copy["commanders"] = enriched_commanders
    return deck_copy


@app.post("/decks/{deck_id}/auto-tag")
@app.post("/api/decks/{deck_id}/auto-tag")
async def auto_tag_deck(deck_id: str, request: Request):
    """Run the auto-tag engine for *deck_id*.

    Runs the intent-aware two-pass engine and persists deck-level and
    per-card tags in Redis via tag_store, preserving user-sourced tags.
    """
    deck = registry.find_deck(r, deck_id)
    if not deck:
        return JSONResponse({"error": "Deck not found"}, status_code=404)

    enriched = _enrich_deck_for_autotag(r, deck)
    autotag_engine_mod.load_tag_vocab(r)
    engine_result = autotag.run_auto_tag(enriched)
    result = tag_store.apply_auto_tags(r, registry.registry_id_of(deck), engine_result)
    return result


@app.get("/api/decks/{deck_id}/tags")
async def get_tags_for_deck(deck_id: str):
    """Return deck-level tags for *deck_id*.

    Response shape: {\"tags\": [{\"tag\", \"source\", \"created_at\", \"version\"}, ...]}
    """
    deck = registry.find_deck(r, deck_id)
    if not deck:
        return JSONResponse({"error": "Deck not found"}, status_code=404)
    return {"deck_id": registry.registry_id_of(deck), "tags": tag_store.get_deck_tags(r, registry.registry_id_of(deck))}


@app.post("/api/decks/{deck_id}/tags")
async def add_tag_to_deck(deck_id: str, request: Request):
    """Add a tag to *deck_id* (deck-level).

    Body: {\"tag\": \"Ramp\", \"source\": \"user\"}  (source defaults to \"user\")
    """
    deck = registry.find_deck(r, deck_id)
    if not deck:
        return JSONResponse({"error": "Deck not found"}, status_code=404)
    data = await request.json()
    tag = data.get("tag")
    source = data.get("source", "user")
    if not tag:
        return JSONResponse({"error": "tag is required"}, status_code=400)
    result = tag_store.add_deck_tag(r, registry.registry_id_of(deck), tag, source=source)
    return {"success": True, "tag": result}


@app.delete("/api/decks/{deck_id}/tags")
async def remove_tag_from_deck(deck_id: str, request: Request):
    """Remove all entries matching *tag* from *deck_id*.

    Body: {\"tag\": \"Ramp\"}
    """
    deck = registry.find_deck(r, deck_id)
    if not deck:
        return JSONResponse({"error": "Deck not found"}, status_code=404)
    data = await request.json()
    tag = data.get("tag")
    if not tag:
        return JSONResponse({"error": "tag is required"}, status_code=400)
    removed = tag_store.remove_deck_tag(r, registry.registry_id_of(deck), tag)
    return {"success": True, "removed": removed}


@app.get("/api/decks/{deck_id}/card-tags")
async def get_deck_card_tags(deck_id: str, card_name: Optional[str] = None):
    """Return per-deck-card tags for *deck_id*.

    If *card_name* is provided, returns that single card's tags.
    Otherwise returns the full {card_name: [tags]} map.

    Response shape: {\"deck_id\", \"tags\": {card: [{\"tag\", \"source\", \"created_at\", \"version\"}]}}
    """
    deck = registry.find_deck(r, deck_id)
    if not deck:
        return JSONResponse({"error": "Deck not found"}, status_code=404)
    if card_name:
        tags = tag_store.get_card_tags_for_deck(r, registry.registry_id_of(deck), card_name)
        return {"deck_id": registry.registry_id_of(deck), "card": card_name, "tags": tags}
    return {"deck_id": registry.registry_id_of(deck), "tags": tag_store.get_deck_card_tags(r, registry.registry_id_of(deck))}


@app.post("/api/decks/{deck_id}/card-tags")
async def add_card_tag_to_deck(deck_id: str, request: Request):
    """Add a tag to one card in *deck_id*.

    Body: {\"card_name\": \"Sol Ring\", \"tag\": \"Ramp\", \"source\": \"user\"}
    (source defaults to \"user\")
    """
    deck = registry.find_deck(r, deck_id)
    if not deck:
        return JSONResponse({"error": "Deck not found"}, status_code=404)
    data = await request.json()
    card_name = data.get("card_name")
    tag = data.get("tag")
    source = data.get("source", "user")
    if not card_name or not tag:
        return JSONResponse({"error": "card_name and tag are required"}, status_code=400)
    result = tag_store.add_card_tag_to_deck(r, registry.registry_id_of(deck), card_name, tag, source=source)
    return {"success": True, "card": card_name, "tag": result}


@app.delete("/api/decks/{deck_id}/card-tags")
async def remove_card_tag_from_deck(deck_id: str, request: Request):
    """Remove a tag from one card in *deck_id*.

    Body: {\"card_name\": \"Sol Ring\", \"tag\": \"Ramp\"}
    """
    deck = registry.find_deck(r, deck_id)
    if not deck:
        return JSONResponse({"error": "Deck not found"}, status_code=404)
    data = await request.json()
    card_name = data.get("card_name")
    tag = data.get("tag")
    if not card_name or not tag:
        return JSONResponse({"error": "card_name and tag are required"}, status_code=400)
    removed = tag_store.remove_card_tag_from_deck(r, registry.registry_id_of(deck), card_name, tag)
    return {"success": True, "card": card_name, "removed": removed}


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
    """Link a new provider account. Does NOT pull its decks: linking is not
    a sync trigger. The user pulls explicitly via
    POST /api/linked-accounts/{id}/sync.

    Body: {"provider": "archidekt"|"moxfield", "username": str}
    """
    data = await request.json()
    provider = data.get("provider", "")
    username = data.get("username", "")
    try:
        account = linked_accounts.add_account(r, provider, username)
    except LinkedAccountError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})

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

    engine_mod.load_staple_theme_vocab(r)
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
            intent_obj = parse_user_intent(intent_data, r=r)
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
    """RV-3: the saved Deep Review doc (null when none yet). Read-only: never rebuilds; it only
    refreshes the stale flag (decklist fingerprint / genre-registry version)."""
    from analytics import review_doc

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    doc = await asyncio.to_thread(review_doc.refresh_stale, r, deck)
    return {"success": True, "exists": doc is not None, "review": doc}


@app.get("/api/decks/{deck_id}/review/history")
async def api_deck_review_history(deck_id: str):
    from analytics import review_doc

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    return {"success": True, "history": await asyncio.to_thread(review_doc.history, r, _theory_deck_id(deck))}


@app.post("/api/decks/{deck_id}/review/rebuild")
async def api_deck_review_rebuild(deck_id: str):
    """RV-3: Rebuild click. Background job: RV-2 metrics, then oracle prose, saved as the latest
    doc. Oracle offline still saves the math sections. Poll GET .../review/jobs/{job_id}."""
    from analytics import review_doc

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    engine_mod.load_staple_theme_vocab(r)
    try:
        job = review_doc.new_job(r, {**deck, "registry_id": _theory_deck_id(deck)})
    except review_doc.ReviewBusy as exc:
        return JSONResponse(status_code=409 if exc.per_deck else 429,
                            content={"success": False, "error": str(exc), "error_kind": "busy"})
    try:
        review_doc.start_job(r, job, {**deck, "registry_id": _theory_deck_id(deck)}, _oracle_client)
    except Exception:
        review_doc.release(job["deck_id"], job["job_id"])
        raise
    return JSONResponse(status_code=202, content={
        "success": True, "job_id": job["job_id"], "status": job["status"],
        "status_url": f"/api/decks/{urllib.parse.quote(job['deck_id'], safe='')}/review/jobs/{job['job_id']}"})


@app.get("/api/decks/{deck_id}/review/jobs/{job_id}")
async def api_deck_review_job(deck_id: str, job_id: str):
    from analytics import review_doc

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    job = review_doc.load_job(r, job_id)
    if not job or job.get("deck_id") != _theory_deck_id(deck):
        return JSONResponse(status_code=404, content={"success": False, "error": f"Job '{job_id}' not found"})
    return {"success": True, "job": job}


# ---------------------------------------------------------------------------
# SM-3: one-off Forge simulation + Oracle sim review (analytics/sim_service.py)
# ---------------------------------------------------------------------------


@app.get("/api/decks/{deck_id}/sim")
async def api_get_deck_sim(deck_id: str):
    """Saved simulation doc + meta + the opponent pool the Run form can pick from. Read-only."""
    from analytics import sim_service

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    view = await asyncio.to_thread(sim_service.saved_view, r, deck)
    pool = await asyncio.to_thread(sim_service.build_opponent_pool, r, None, deck)
    running = await asyncio.to_thread(sim_service.active_job, r, deck)
    focus = await asyncio.to_thread(sim_service.focus_view, r, deck)
    std_pod = await asyncio.to_thread(sim_service.resolve_pod, r, deck)
    return {"success": True, **view, "focus": focus, "opponent_pool": pool["opponents"], "opponent_pool_error": pool["daemon_error"],
            "standard_pod": std_pod,
            "running_job": sim_service.public_job(running) if running else None}


@app.get("/api/sim/standard-pod")
async def api_get_standard_pod():
    from analytics import sim_service

    return {"success": True, **await asyncio.to_thread(sim_service.resolve_pod, r)}


@app.put("/api/sim/standard-pod")
async def api_put_standard_pod(request: Request):
    """{"decks": [1-5 opponent ids]} -> saved pod. Forge precons only if the caller lists them."""
    from analytics import sim_service

    body = await _json_body(request)
    try:
        await asyncio.to_thread(sim_service.save_pod, r, (body or {}).get("decks") if isinstance(body, dict) else None)
    except sim_service.SimParamError as exc:
        return _bad_request(exc, 422)
    return {"success": True, **await asyncio.to_thread(sim_service.resolve_pod, r)}


@app.delete("/api/sim/standard-pod")
async def api_delete_standard_pod():
    from analytics import sim_service

    await asyncio.to_thread(sim_service.clear_pod, r)
    return {"success": True, **await asyncio.to_thread(sim_service.resolve_pod, r)}


@app.post("/api/decks/{deck_id}/sim/run")
async def api_run_deck_sim(deck_id: str, request: Request):
    """{"opponent_mode": "standard"|"random"|"specific", "opponents": [3 ids], "games": 20|60, "notes": str, "focus": [slug, ...max 3]}
    -> 202 {job_id}. Poll GET .../sim/jobs/{job_id}. The previous saved report stays until the run succeeds."""
    from analytics import sim_client, sim_service

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request)
    try:
        job = await asyncio.to_thread(sim_service.begin_run, r, deck, body if isinstance(body, dict) else {})
    except sim_service.SimParamError as exc:
        return _bad_request(exc, 422)
    except sim_service.SimRunBusy as exc:
        return JSONResponse(status_code=409, content={"success": False, "error": str(exc), "error_kind": "busy",
                                                      "job_id": exc.job_id})
    except sim_client.SimBusy as exc:
        return JSONResponse(status_code=409, content={"success": False, "error": str(exc), "error_kind": "busy"})
    except sim_client.SimUnavailable as exc:
        return JSONResponse(status_code=503, content={"success": False, "error": str(exc), "error_kind": "sim_unavailable"})
    except sim_client.SimClientError as exc:
        return JSONResponse(status_code=502, content={"success": False, "error": str(exc), "error_kind": "sim_error"})
    except ValueError as exc:  # deck can't be exported (no commander / not 100 cards)
        return _bad_request(exc, 422)
    try:
        sim_service.start_thread(r, job, deck, None, _oracle_client)
    except Exception:
        sim_service.release(job["deck_id"], job["job_id"])
        raise
    return JSONResponse(status_code=202, content={
        "success": True, "job_id": job["job_id"], "status": job["status"],
            "status_url": f"/api/decks/{urllib.parse.quote(job['deck_id'], safe='')}/sim/jobs/{job['job_id']}"})



def _focus_err(exc: Exception):
    return _bad_request(exc, 422)


@app.get("/api/sim/focus-chips")
async def api_focus_chips(all: bool = False):
    """Persisted Focus-chip catalog (all=1 includes chips hidden for lack of telemetry)."""
    from analytics import sim_focus

    items = await asyncio.to_thread(sim_focus.list_catalog if all else sim_focus.available_catalog, r)
    return {"success": True, "chips": items, "supported_telemetry": sorted(sim_focus.SUPPORTED_TELEMETRY),
            "options": sim_focus.options()}


@app.post("/api/sim/focus-chips")
async def api_focus_chip_create(request: Request):
    from analytics import sim_focus

    body = await _json_body(request)
    try:
        return {"success": True, "chip": await asyncio.to_thread(sim_focus.create, r, body if isinstance(body, dict) else {})}
    except sim_focus.FocusError as exc:
        return _focus_err(exc)


@app.put("/api/sim/focus-chips/order")
async def api_focus_chip_reorder(request: Request):
    from analytics import sim_focus

    body = await _json_body(request)
    try:
        slugs = body.get("slugs") if isinstance(body, dict) else None
        if not isinstance(slugs, list):
            raise sim_focus.FocusError("slugs must be a list")
        return {"success": True, "chips": await asyncio.to_thread(sim_focus.reorder, r, slugs)}
    except sim_focus.FocusError as exc:
        return _focus_err(exc)


@app.put("/api/sim/focus-chips/{slug}")
async def api_focus_chip_update(slug: str, request: Request):
    from analytics import sim_focus

    body = await _json_body(request)
    try:
        return {"success": True, "chip": await asyncio.to_thread(sim_focus.update, r, slug, body if isinstance(body, dict) else {})}
    except sim_focus.FocusError as exc:
        return _focus_err(exc)


@app.delete("/api/sim/focus-chips/{slug}")
async def api_focus_chip_delete(slug: str):
    from analytics import sim_focus

    try:
        await asyncio.to_thread(sim_focus.delete, r, slug)
    except sim_focus.FocusError as exc:
        return JSONResponse(status_code=404, content={"success": False, "error": str(exc)})
    return {"success": True}


@app.get("/api/decks/{deck_id}/sim/jobs/{job_id}")
async def api_deck_sim_job(deck_id: str, job_id: str):
    from analytics import sim_service

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    job = await asyncio.to_thread(sim_service.load_job, r, job_id)
    if not job or job.get("deck_id") != _theory_deck_id(deck):
        return JSONResponse(status_code=404, content={"success": False, "error": f"Job '{job_id}' not found"})
    return {"success": True, **sim_service.public_job(job)}


# ---------------------------------------------------------------------------
# Single Card Review (keep/remove debate + replacements) -- see docs/CARD_REVIEW.md
# ---------------------------------------------------------------------------


def _card_review_llm():
    from analytics.card_review import OracleLLM

    return OracleLLM(_oracle_client)


@app.post("/api/decks/{deck_id}/cards/{card_id:path}/review")
async def api_card_review(deck_id: str, card_id: str, request: Request):
    """Start a background card review: Remove vs Keep debate, a judge, and (on remove) 3-5 replacements.
    Body (optional): {"rounds": 2|3}. Poll GET .../review/jobs/{job_id} for progress and the result."""
    from analytics.card_review import jobs, rounds_from_env
    from analytics.card_review import service as review_service

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request)
    rounds = rounds_from_env()
    if isinstance(body, dict) and body.get("rounds") is not None:
        if isinstance(body["rounds"], bool) or not isinstance(body["rounds"], int) or body["rounds"] not in (2, 3):
            return _bad_request(ValueError("rounds must be 2 or 3"), 422)
        rounds = body["rounds"]
    try:
        card = await asyncio.to_thread(review_service.resolve_card, r, deck, card_id)
    except review_service.CardNotInDeck as exc:
        return _bad_request(exc, 404)
    if any(card.lower() == (c or "").lower() for c in deck.get("commanders") or []):
        return _bad_request(ValueError("the commander can't be reviewed"), 400)

    offline = await asyncio.to_thread(_oracle_preflight)
    if offline is not None:
        return offline
    rid = registry.registry_id_of(deck)
    try:
        job = await asyncio.to_thread(jobs.new_job, r, rid, card, rounds)
    except jobs.ReviewBusy as exc:
        return JSONResponse(status_code=409 if exc.per_card else 429,
                            content={"success": False, "error": str(exc), "error_kind": "busy"})
    llm = _card_review_llm()
    try:
        jobs.start_job(r, job, lambda emit: review_service.review_card(r, deck, card, llm, rounds, emit))
    except Exception:
        jobs.release(job["slot"], job["job_id"])
        raise
    return JSONResponse(status_code=202, content={
        "success": True, "job_id": job["job_id"], "status": job["status"], "rounds": rounds,
        "status_url": f"/api/decks/{urllib.parse.quote(rid, safe='')}/cards/{urllib.parse.quote(card, safe='')}"
                      f"/review/jobs/{job['job_id']}"})


@app.get("/api/decks/{deck_id}/cards/{card_id:path}/review/jobs/{job_id}")
async def api_card_review_job(deck_id: str, card_id: str, job_id: str):
    """status: queued|running|done|failed. While running, `stage` and `transcript` (validated turns so far)
    update after every debate turn; when done, `result` holds the full review."""
    from analytics.card_review import jobs

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    job = await asyncio.to_thread(jobs.load_job, r, job_id)
    if not job or job.get("deck_id") != registry.registry_id_of(deck):
        return JSONResponse(status_code=404, content={"success": False, "error": f"Job '{job_id}' not found"})
    return {"success": True, "job": {k: v for k, v in job.items() if k != "slot"}}


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
    named by the dominant resource (e.g. "tokens → sacrifice → drain") and scored."""
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
    Mass disruption / Plan, vs the deck's template (default unless overridden)."""
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


def _comp_error(exc: Exception):
    if isinstance(exc, comp_svc.PresetNotFound):
        return JSONResponse(status_code=404, content={"success": False, "error": f"Preset '{exc.args[0]}' not found"})
    if isinstance(exc, CompositionValidationError):
        issues = getattr(exc, "issues", None)
        return JSONResponse(status_code=422, content={"success": False, "error": str(exc), "issues": issues})
    raise exc


@app.get("/api/composition/refs")
async def api_composition_refs(scope: str = "preset", deck_id: Optional[str] = None):
    """Read-only catalog of refs ``check_ref`` accepts (for the Ref combobox)."""
    if scope not in ("preset", "deck"):
        return _bad_request(ValueError("scope must be 'preset' or 'deck'"))
    rid = None
    if scope == "deck":
        if not deck_id:
            return _bad_request(ValueError("deck_id is required for scope=deck"))
        deck, err = await asyncio.to_thread(_deck_or_404, deck_id)
        if err:
            return err
        rid = registry.registry_id_of(deck)
    from analytics import composition_refs
    refs = await asyncio.to_thread(composition_refs.enumerate_refs, scope, r, rid)
    return {"success": True, "scope": scope, "refs": refs, "patterns": composition_refs.patterns()}


@app.get("/api/composition/presets")
async def api_list_presets(kind: Optional[str] = None):
    """User composition presets (there are no built-ins). ?kind=user is a no-op filter; ?kind=builtin
    always returns []."""
    return {"success": True, "presets": await asyncio.to_thread(comp_svc.list_all, r, kind)}


@app.post("/api/composition/presets")
async def api_create_preset(request: Request):
    body = await _json_body(request)
    if not isinstance(body, dict):
        return _bad_request(ValueError("JSON object body required"))
    try:
        p = await asyncio.to_thread(comp_svc.create, r, body)
    except (CompositionValidationError) as exc:
        return _comp_error(exc)
    return JSONResponse(status_code=201, content={"success": True, "preset": p})


@app.get("/api/composition/presets/{preset_id}")
async def api_get_preset(preset_id: str):
    try:
        return {"success": True, "preset": await asyncio.to_thread(comp_svc.get, r, preset_id)}
    except comp_svc.PresetNotFound as exc:
        return _comp_error(exc)


@app.put("/api/composition/presets/{preset_id}")
async def api_update_preset(preset_id: str, request: Request):
    body = await _json_body(request)
    if not isinstance(body, dict):
        return _bad_request(ValueError("JSON object body required"))
    try:
        p = await asyncio.to_thread(comp_svc.update, r, preset_id, body)
    except (comp_svc.PresetNotFound, CompositionValidationError) as exc:
        return _comp_error(exc)
    return {"success": True, "preset": p}


@app.delete("/api/composition/presets/{preset_id}")
async def api_delete_preset(preset_id: str):
    try:
        await asyncio.to_thread(comp_svc.delete, r, preset_id)
    except (comp_svc.PresetNotFound) as exc:
        return _comp_error(exc)
    return {"success": True, "deleted": preset_id}


@app.get("/api/decks/{deck_id}/composition/selection")
async def api_deck_preset_selection(deck_id: str):
    """Resolved preset for this deck (pinned, or auto-selected by archetype/tags/commander MV)."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    out = await asyncio.to_thread(comp_svc.resolve_for_deck, r, deck, registry.registry_id_of(deck))
    return {"success": True, "selection": out}


@app.put("/api/decks/{deck_id}/composition/selection")
async def api_set_deck_preset_selection(deck_id: str, request: Request):
    """Body {mode:'auto'} or {mode:'pinned', preset_id, lands?, overrides?}."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request)
    if not isinstance(body, dict):
        return _bad_request(ValueError("JSON object body required"))
    rid = registry.registry_id_of(deck)
    try:
        await asyncio.to_thread(comp_svc.set_deck_selection, r, rid, body)
    except CompositionValidationError as exc:
        return _comp_error(exc)
    return {"success": True, "selection": await asyncio.to_thread(comp_svc.resolve_for_deck, r, deck, rid)}


@app.delete("/api/decks/{deck_id}/composition/selection")
async def api_reset_deck_preset_selection(deck_id: str):
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    rid = registry.registry_id_of(deck)
    await asyncio.to_thread(comp_svc.reset_deck_selection, r, rid)
    return {"success": True, "selection": await asyncio.to_thread(comp_svc.resolve_for_deck, r, deck, rid)}


def _presetgen_run(deck: dict, mode: str, body: dict):
    """Build the presetgen packet for one deck and run generate/improve (drafts are never saved)."""
    from analytics import presetgen
    from analytics.interactions.llm import default_chain

    rid = registry.registry_id_of(deck)
    texts, card_tags, deck_tags, _tpl = _cat_load(deck)
    users = comp_svc.user_presets(r)
    current = None
    if mode == "improve":
        pid = body.get("preset_id")
        current = comp_svc.get(r, pid) if pid else comp_svc.resolve_for_deck(r, deck, rid).get("preset")
    packet = presetgen.build_packet(
        deck, texts, card_tags, deck_tags=deck_tags, ident=identity_mod.load_identity(r, deck),
        current_preset=current, global_custom_ids=[c["id"] for c in categories_mod.list_global(r)],
        existing_preset_names=[p.get("name", "") for p in comp_svc.list_all(r)], user_presets=users)
    chain = default_chain() if body.get("ai", True) is not False else None
    return presetgen.generate(packet, chain) if mode == "generate" else presetgen.improve(packet, chain)


async def _presetgen_route(deck_id: str, request: Request, mode: str):
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request)
    body = body if isinstance(body, dict) else {}
    try:
        out = await asyncio.to_thread(_presetgen_run, deck, mode, body)
    except (comp_svc.PresetNotFound, CompositionValidationError) as exc:
        return _comp_error(exc)
    return {"success": True, "draft": out}


@app.post("/api/decks/{deck_id}/composition/presets/generate")
async def api_presetgen_generate(deck_id: str, request: Request):
    """Draft a new preset for this deck (Baseline, or AI draft). Body {ai?: bool}. Nothing is saved."""
    return await _presetgen_route(deck_id, request, "generate")


@app.post("/api/decks/{deck_id}/composition/presets/improve")
async def api_presetgen_improve(deck_id: str, request: Request):
    """Draft an improved copy of the deck's current (or body.preset_id) preset. Nothing is saved."""
    return await _presetgen_route(deck_id, request, "improve")


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


@app.post("/api/decks/{deck_id}/identity/pillars/describe")
async def api_describe_pillar(deck_id: str, request: Request):
    """Generate or improve one pillar description. Body: {"name"?, "prompt"?, "description"?, "pillar_id"?}
    (at least one of name/prompt/description). Nothing is saved; returns {"description", "mode"}."""
    from analytics import pillar_describe
    from analytics.interactions.llm import default_chain

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request)
    pillar_id = body.get("pillar_id") if isinstance(body, dict) else None

    def _run():
        ident = identity_mod.load_identity(r, deck)
        return pillar_describe.describe_pillar(
            deck, default_chain(), insights.card_texts(r, deck), body, ident,
            exclude_id=pillar_id if isinstance(pillar_id, str) else None)

    try:
        out = await asyncio.to_thread(_run)
    except pillar_describe.PillarDescribeError as exc:
        return _bad_request(exc, 422)
    except pillar_describe.PillarDescribeUnavailable as exc:
        status = 503 if exc.kind == "no_llm" else 502
        return JSONResponse(status_code=status, content={"success": False, "error": str(exc), "error_kind": exc.kind})
    return {"success": True, **out}


@app.get("/api/decks/{deck_id}/edhrec")
async def api_deck_edhrec(deck_id: str, refresh: bool = False):
    """EDHREC inclusion per card for the deck's commander (cached 7 days)."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    out = await asyncio.to_thread(insights.deck_edhrec, r, deck, refresh)
    return {"success": True, "edhrec": out}


@app.get("/api/decks/{deck_id}/review-metrics")
async def api_deck_review_metrics(deck_id: str):
    """RV-2 debug/API: the deterministic Deep Review numbers (genre, composition, tempo, focus,
    strong cards, stragglers, combos/engines). Computed on demand, not stored; no oracle call."""
    from analytics import review_metrics

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    out = await asyncio.to_thread(review_metrics.review_deck, r, deck)
    return {"success": True, "review_metrics": out}


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


@app.get("/api/similar")
async def api_similar(card: Optional[str] = None, n: int = 10):
    """AN-8 'mechanically similar' (similar rules text, NOT synergy, NOT played-with):
    model info; with ?card=, its nearest neighbours. Unknown card -> 200, empty list."""
    from analytics import mech_similar as mech_similar_mod

    m = mech_similar_mod.default_model()
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
        cur_read_hash = await asyncio.to_thread(personality_read.current_input_hash, r, deck)
        if rec_read.get("input_hash") == cur_read_hash:
            read_data = rec_read.get("read")
            oracle_status = "ok"
        else:
            # Withhold: the deck/journal changed since this read, so the current
            # rulebook stays visible until a new read completes.
            oracle_status = "stale"

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

    cur_hash = await asyncio.to_thread(personality_read.current_input_hash, r, deck)
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
    cur_hash = await asyncio.to_thread(personality_read.current_input_hash, r, deck)
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


@app.get("/api/deck-genre/options")
async def api_deck_genre_options():
    """GN: the Deck Genre option lists (docs/deck_genre_taxonomy.yaml) + the creature-type catalog."""
    return {"success": True, "options": deck_genre.page_options(r),
            "creature_types": await asyncio.to_thread(deck_genre.creature_types, r)}


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
async def api_deck_tune(deck_id: str, mode: str = "flex", in_deck: bool = True, buy: bool = True, goal: str = "balanced"):
    """Shape fixes, swaps (cut -> best owned / best buy), weak performers and the
    pet notice. Deterministic; cached LLM 'why' lines are attached when present.
    mode = free | flex | best. flex honours in_deck (owned cards sleeved in another
    physical deck) and buy (cards you don't own), both default true. Legacy
    owned_only / owned_first are accepted and mapped to free / flex."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    try:
        out = await asyncio.to_thread(tuner_mod.compute, r, deck, mode, in_deck, buy, goal=goal)
    except tuner_mod.TunerError as exc:
        return _bad_request(exc)
    return {"success": True, "tune": out}


@app.get("/api/decks/{deck_id}/tune/saved")
async def api_deck_tune_saved(deck_id: str):
    """The deck's saved (active) Tune recommendation. 404 with `recommendation: null`
    when none has been computed yet. See docs/TUNER.md 'Saved recommendations'."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    rec = await asyncio.to_thread(tune_store.get_active, r, tune_store.deck_key(deck))
    if not rec:
        return JSONResponse(status_code=404, content={"success": False, "recommendation": None,
                                                      "error": "No saved tune recommendation for this deck"})
    return {"success": True, "recommendation": rec}


@app.get("/api/decks/{deck_id}/tune/state")
async def api_deck_tune_state(deck_id: str):
    """Decisions, extra adds and pet cards as of now. A saved recommendation is a snapshot;
    the Tune page lays this over it so accepts, dismissals and pets survive a reload."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    st = await asyncio.to_thread(tuner_mod.load_state, r, deck)
    pets = await asyncio.to_thread(insights.pet_cards, r, deck)
    return {"success": True, "state": st, "pet_cards": pets}


@app.post("/api/decks/{deck_id}/tune/recompute")
async def api_deck_tune_recompute(deck_id: str, request: Request):
    """Run the tuner, save the result as the deck's active recommendation and archive
    the previous one (same MULTI/EXEC). Body (optional): {"mode": "free|flex|best",
    "in_deck": true, "buy": true} (legacy owned_first / owned_only accepted). If generation fails nothing is saved and the old one stays active."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request)
    body = body if isinstance(body, dict) else {}
    mode = body.get("mode", "flex")
    in_deck = body.get("in_deck", True)
    buy = body.get("buy", True)
    goal = body.get("goal", "balanced")
    if not isinstance(in_deck, bool) or not isinstance(buy, bool):
        return _bad_request(tuner_mod.TunerError("in_deck and buy must be booleans."))
    try:
        new, old = await asyncio.to_thread(tune_store.recompute, r, deck, tuner_mod.compute, mode, in_deck, buy, goal)
    except tuner_mod.TunerError as exc:
        return _bad_request(exc)
    except tune_store.TuneStoreError as exc:
        return _bad_request(exc, 409)
    archived = {k: v for k, v in old.items() if k != "payload"} if old else None
    return {"success": True, "recommendation": new, "archived": archived}


@app.get("/api/decks/{deck_id}/tune/history")
async def api_deck_tune_history(deck_id: str, limit: int = 20, include_payload: bool = False):
    """Archived recommendations, newest first (the active one is excluded). Payload
    omitted unless include_payload=true."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    recs = await asyncio.to_thread(tune_store.list_history, r, tune_store.deck_key(deck),
                                   max(1, min(limit, 100)), include_payload)
    return {"success": True, "history": recs}


@app.post("/api/decks/{deck_id}/tune/why")
async def api_deck_tune_why(deck_id: str, request: Request):
    """Ask the LLM (Gemini, then Nous) for the one-line 'why' of each swap. Prose
    only: every number is the tuner's. Cached per cut/add pair; cost is logged
    to /api/interactions/runs (trigger 'tuner')."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request) or {}
    body = body if isinstance(body, dict) else {}
    mode = body.get("mode", "flex")
    in_deck = bool(body.get("in_deck", True))
    buy = bool(body.get("buy", True))
    goal = body.get("goal", "balanced")
    try:
        tune = await asyncio.to_thread(tuner_mod.compute, r, deck, mode, in_deck, buy, goal=goal)
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


# ---------------------------------------------------------------------------
# Genre registry (RV-1) -- see analytics/genres.py, docs/REVIEW_SPEC.md
# ---------------------------------------------------------------------------

GENRE_AUTOFILL_TIMEOUT_S = 300.0


def _genre_payload(**extra):
    from analytics import genres as genres_mod

    return {"success": True, "version": genres_mod.get_version(r), **extra}


@app.get("/api/genres")
async def api_genres_list():
    from analytics import genres as genres_mod

    genres = await asyncio.to_thread(genres_mod.list_genres, r)
    return _genre_payload(genres=genres, vocabulary=genres_mod.role_vocabulary())


@app.post("/api/genres/autofill")
async def api_genres_autofill(request: Request):
    """{"theme": str} -> oracle drafts description + roles; the app validates them against the
    tag vocabulary (unknown tags are dropped and listed in `flagged`). Nothing is saved."""
    from analytics import genres as genres_mod
    from analytics.oracle_client import OracleAuthError, OracleError, OracleUnavailable

    body = await _json_body(request)
    theme = body.get("theme") if isinstance(body, dict) and isinstance(body.get("theme"), str) else ""
    theme = " ".join(theme.split())
    if not theme or len(theme) > genres_mod.MAX_NAME:
        return _bad_request(genres_mod.GenreError(f"Give a theme name (1-{genres_mod.MAX_NAME} characters)."))
    offline = await asyncio.to_thread(_oracle_preflight)
    if offline is not None:
        return offline

    def _ask() -> str:
        client = _oracle_client()
        client.timeout = GENRE_AUTOFILL_TIMEOUT_S
        return client.ask(genres_mod.autofill_prompt(theme), system=genres_mod.AUTOFILL_SYSTEM)

    try:
        reply = await asyncio.to_thread(_ask)
    except OracleUnavailable as exc:
        return _oracle_offline(exc.detail)
    except OracleAuthError:
        return _oracle_offline("oracle rejected the API key")
    except OracleError as exc:
        return JSONResponse(status_code=502, content={"success": False, "error": str(exc)[:300]})
    try:
        draft = genres_mod.validate_autofill(theme, genres_mod.parse_oracle_reply(reply))
    except genres_mod.GenreError as exc:
        return JSONResponse(status_code=422, content={"success": False, "error": str(exc), "error_kind": "autofill_invalid"})
    return {"success": True, "draft": draft}


@app.post("/api/deck-genre/improve-text")
async def api_deck_genre_improve_text(request: Request):
    """Reword one short Journal genre tag (theme/kindred custom entry). Body: {kind, text}.
    Stateless, never saves: {text, original, kind, provider, model}. The caller shows the
    suggestion and only overwrites the field if the user accepts it (GN-TXT-1)."""
    from analytics import genre_text_improve as gti
    from analytics.interactions.llm import default_chain

    body = await _json_body(request)
    try:
        out = await asyncio.to_thread(gti.improve_text, default_chain(), body)
    except gti.GenreTextImproveError as exc:
        return _bad_request(exc, 422)
    except gti.GenreTextImproveUnavailable as exc:
        status = 503 if exc.kind == "no_llm" else 502
        return JSONResponse(status_code=status, content={"success": False, "error": str(exc), "error_kind": exc.kind})
    return {"success": True, **out}


# ---------------------------------------------------------------------------
# Category registry (CT) -- see analytics/categories.py, docs/design/categories/SPEC.md
# ---------------------------------------------------------------------------

def _cat_error(exc):
    body = {"success": False, "error": str(exc), "error_kind": exc.kind, **exc.extra}
    return JSONResponse(status_code=exc.status, content=body)


def _cat_not_found(cat_id: str):
    return JSONResponse(status_code=404, content={"success": False, "error": f"Category '{cat_id}' not found"})


def _cat_deck(deck_id: Optional[str]):
    """(deck, registry_id, err) for an optional ?deck_id=."""
    if not deck_id:
        return None, None, None
    deck, err = _deck_or_404(deck_id)
    if err:
        return None, None, err
    return deck, registry.registry_id_of(deck), None


def _cat_global_usage() -> dict:
    """{category id: number of decks with at least one member}. Best effort: needs SCAN."""
    usage: dict = {}
    scan = getattr(r, "scan_iter", None)
    if scan is None:
        return usage
    try:
        for key in scan(match="deck_card_tags:*"):
            try:
                data = json.loads(r.get(key) or "{}")
            except ValueError:
                continue
            seen = {str(e.get("tag", "")) for entries in data.values() for e in entries}
            for tag in seen:
                if tag.startswith(categories_mod.CAT_TAG_PREFIX + "g-"):
                    cid = tag[len(categories_mod.CAT_TAG_PREFIX):]
                    usage[cid] = usage.get(cid, 0) + 1
    except Exception:  # noqa: BLE001  redis hiccup: the "decks" caption just shows 0
        pass
    return usage


def _cat_strip_tag(registry_id: str, tag: str) -> None:
    for name, entries in tag_store.get_deck_card_tags(r, registry_id).items():
        if any(e.get("tag") == tag for e in entries):
            tag_store.remove_card_tag_from_deck(r, registry_id, name, tag)


def _cat_strip_everywhere(tag: str) -> None:
    scan = getattr(r, "scan_iter", None)
    if scan is None:
        return
    for key in scan(match="deck_card_tags:*"):
        k = key.decode() if isinstance(key, bytes) else key
        _cat_strip_tag(k[len("deck_card_tags:"):], tag)


def _cat_load(deck: Optional[dict]):
    """(texts, card_tags, deck tag names, template) for one deck, or empty for the global scope."""
    if deck is None:
        return {}, {}, [], {}
    rid = registry.registry_id_of(deck)
    texts = insights.card_texts(r, deck)
    card_tags = tag_store.get_deck_card_tags(r, rid)
    deck_tags = [t.get("tag", "") for t in tag_store.get_deck_tags(r, rid)]
    return texts, card_tags, deck_tags, composition_mod.get_template(r, deck)


@app.get("/categories", response_class=HTMLResponse)
async def categories_view(request: Request):
    """Category registry (CT): core roles, global custom and deck-specific categories."""
    return templates.TemplateResponse(request=request, name="categories.html")


@app.get("/api/categories")
async def api_categories_list(deck_id: Optional[str] = None):
    """Registry payload. Without ``deck_id`` (Global scope): core definitions with template targets and floors,
    plus the global custom categories. With it: live counts, the deck's targets and statuses, the deck's own
    categories and theme suggestions."""
    deck, rid, err = await asyncio.to_thread(_cat_deck, deck_id)
    if err:
        return err

    def _build():
        texts, card_tags, deck_tags, template = _cat_load(deck)
        if deck is None:
            template = composition_mod.DEFAULT_TEMPLATE
        rows, meta = categories_mod.core_rows(deck, texts, card_tags, deck_tags, template)
        pillar_names: dict = {}
        if deck is not None:
            ident = identity_mod.load_identity(r, deck)
            pillar_names = {p["id"]: p.get("name", "") for p in identity_mod.live_pillars(ident)}
        usage = _cat_global_usage() if deck is None else {}
        rows += [categories_mod.custom_row(c, card_tags if deck else None, deck, decks_using=usage.get(c["id"], 0))
                 for c in categories_mod.list_global(r)]
        out = {"success": True, "version": categories_mod.get_version(r), "spec": categories_mod.SPEC_VERSION,
               "groups": list(categories_mod.GROUPS), "themes": categories_mod.theme_tags(r),
               "rule_kinds": list(categories_mod.RULE_KINDS), "caps": {"deck": categories_mod.DECK_CAP},
               "aliases": categories_mod.ALIASES, "group_aliases": list(categories_mod.GROUP_ALIASES),
               "limits": {"name": categories_mod.NAME_MAX, "description": categories_mod.DESC_MAX,
                          "target": categories_mod.TARGET_MAX, "rule_value": categories_mod.RULE_VALUE_MAX},
               "interaction_distinct": meta["interaction_distinct"]}
        if deck is not None:
            mine = categories_mod.list_deck(r, rid)
            rows += [categories_mod.custom_row(c, card_tags, deck, pillar_names) for c in mine]
            out["deck"] = {"id": rid, "name": deck.get("name"), "cards": meta["cards"], "built": meta["built"],
                           "commander_mv": meta["commander_mv"], "categories_used": len(mine),
                           "cap": categories_mod.DECK_CAP,
                           "pillars": [{"id": k, "name": v} for k, v in pillar_names.items()]}
            out["suggestions"] = categories_mod.theme_suggestions(deck_tags, mine)
        out["categories"] = rows
        return out

    return await asyncio.to_thread(_build)


@app.post("/api/categories/describe")
async def api_categories_describe(request: Request):
    """Draft/improve ONE custom category description. Body: {name?, description?, hint?, group?, deck_id?,
    avoid?: [str]}. Stateless, never saves. 422 {error_kind:"alias", core_id} for core-alias names (no model call);
    returns {description, rule, duplicate_of, flagged?, mode}."""
    from analytics import category_describe
    from analytics.interactions.llm import default_chain

    body = await _json_body(request)
    deck_id = body.get("deck_id") if isinstance(body, dict) else None
    deck, _rid, err = await asyncio.to_thread(_cat_deck, deck_id if isinstance(deck_id, str) else None)
    if err:
        return err

    def _run():
        texts, _tags, deck_tags, _tpl = _cat_load(deck)
        pillars = []
        if deck is not None:
            pillars = identity_mod.live_pillars(identity_mod.load_identity(r, deck))
        return category_describe.describe_category(default_chain(), body, deck, texts, deck_tags, pillars)

    try:
        out = await asyncio.to_thread(_run)
    except categories_mod.CategoryError as exc:
        return _cat_error(exc)
    except category_describe.CategoryDescribeUnavailable as exc:
        status = 503 if exc.kind == "no_llm" else 502
        return JSONResponse(status_code=status, content={"success": False, "error": str(exc), "error_kind": exc.kind})
    return {"success": True, **out}


@app.post("/api/categories")
async def api_categories_create(request: Request):
    body = await _json_body(request)
    deck_id = body.get("deck_id") if isinstance(body, dict) else None
    deck, rid, err = await asyncio.to_thread(_cat_deck, deck_id if isinstance(deck_id, str) else None)
    if err:
        return err
    try:
        cat = await asyncio.to_thread(categories_mod.create, r, body, rid)
    except categories_mod.CategoryError as exc:
        return _cat_error(exc)
    return JSONResponse(status_code=201, content={"success": True, "category": cat,
                                                  "version": categories_mod.get_version(r)})


@app.put("/api/categories/{cat_id}")
async def api_categories_update(cat_id: str, request: Request, deck_id: Optional[str] = None):
    body = await _json_body(request)
    deck, rid, err = await asyncio.to_thread(_cat_deck, deck_id)
    if err:
        return err
    try:
        cat = await asyncio.to_thread(categories_mod.update, r, cat_id, body, rid)
    except categories_mod.CategoryNotFound:
        return _cat_not_found(cat_id)
    except categories_mod.CategoryError as exc:
        return _cat_error(exc)
    return {"success": True, "category": cat, "version": categories_mod.get_version(r)}


@app.delete("/api/categories/{cat_id}")
async def api_categories_delete(cat_id: str, deck_id: Optional[str] = None):
    deck, rid, err = await asyncio.to_thread(_cat_deck, deck_id)
    if err:
        return err

    def _run():
        cat = categories_mod.delete(r, cat_id, rid)
        tag = categories_mod.CAT_TAG_PREFIX + cat_id
        if cat["scope"] == "deck":
            _cat_strip_tag(rid, tag)
        else:
            _cat_strip_everywhere(tag)  # cards keep every other tag
        return cat

    try:
        await asyncio.to_thread(_run)
    except categories_mod.CategoryNotFound:
        return _cat_not_found(cat_id)
    except categories_mod.CategoryError as exc:
        return _cat_error(exc)
    return {"success": True, "version": categories_mod.get_version(r)}


@app.post("/api/categories/{cat_id}/promote")
async def api_categories_promote(cat_id: str, deck_id: Optional[str] = None):
    """Deck category -> global. This deck's members move over; other decks never had any."""
    deck, rid, err = await asyncio.to_thread(_cat_deck, deck_id)
    if err:
        return err

    def _run():
        new, old_id = categories_mod.promote(r, cat_id, rid)
        old_tag, new_tag = categories_mod.CAT_TAG_PREFIX + old_id, categories_mod.CAT_TAG_PREFIX + new["id"]
        for name in categories_mod.member_names(tag_store.get_deck_card_tags(r, rid), old_id):
            tag_store.add_card_tag_to_deck(r, rid, name, new_tag, source="user")
        _cat_strip_tag(rid, old_tag)
        return new

    try:
        new = await asyncio.to_thread(_run)
    except categories_mod.CategoryNotFound:
        return _cat_not_found(cat_id)
    except categories_mod.CategoryError as exc:
        return _cat_error(exc)
    return {"success": True, "category": new, "version": categories_mod.get_version(r)}


@app.get("/api/categories/{cat_id}/members")
async def api_categories_members(cat_id: str, deck_id: str):
    """Member cards (with flag names) of one category in one deck, and, for a custom category with a rule,
    the cards the rule would suggest (never added automatically)."""
    deck, rid, err = await asyncio.to_thread(_cat_deck, deck_id)
    if err:
        return err

    def _run():
        texts, card_tags, _dt, _tpl = _cat_load(deck)
        if cat_id in categories_mod.CORE_BY_ID:
            names, suggested = categories_mod.core_members(deck, texts, card_tags, cat_id), []
        else:
            cat, _k, _s = categories_mod.find(r, cat_id, rid)
            names = categories_mod.member_names(card_tags, cat_id, exclude=deck.get("commanders") or [])
            suggested = categories_mod.suggest_members(deck, texts, card_tags, cat)
        row = lambda n: {"name": n, "flags": categories_mod.flags_for(texts.get(n))}  # noqa: E731
        return {"success": True, "members": [row(n) for n in names], "suggested": [row(n) for n in suggested]}

    try:
        return await asyncio.to_thread(_run)
    except categories_mod.CategoryNotFound:
        return _cat_not_found(cat_id)
    except categories_mod.CategoryError as exc:
        return _cat_error(exc)


@app.post("/api/categories/{cat_id}/members")
async def api_categories_add_member(cat_id: str, request: Request):
    """Accept one card into a custom category. Body: {deck_id, card}. Core membership is auto-tag's."""
    body = await _json_body(request)
    body = body if isinstance(body, dict) else {}
    deck, rid, err = await asyncio.to_thread(_cat_deck, body.get("deck_id") if isinstance(body.get("deck_id"), str) else None)
    if err:
        return err
    card = body.get("card")

    def _run():
        if deck is None:
            raise categories_mod.CategoryError("deck_id is required.", 400)
        categories_mod.find(r, cat_id, rid)
        if not isinstance(card, str) or card not in categories_mod.deck_card_names(deck):
            raise categories_mod.CategoryError("That card is not in this deck (the commander can't be a member).", 422)
        tag_store.add_card_tag_to_deck(r, rid, card, categories_mod.CAT_TAG_PREFIX + cat_id, source="user")

    try:
        await asyncio.to_thread(_run)
    except categories_mod.CategoryNotFound:
        return _cat_not_found(cat_id)
    except categories_mod.CategoryError as exc:
        return _cat_error(exc)
    return {"success": True}


@app.delete("/api/categories/{cat_id}/members")
async def api_categories_remove_member(cat_id: str, deck_id: str, card: str):
    deck, rid, err = await asyncio.to_thread(_cat_deck, deck_id)
    if err:
        return err

    def _run():
        categories_mod.find(r, cat_id, rid)
        return tag_store.remove_card_tag_from_deck(r, rid, card, categories_mod.CAT_TAG_PREFIX + cat_id)

    try:
        removed = await asyncio.to_thread(_run)
    except categories_mod.CategoryNotFound:
        return _cat_not_found(cat_id)
    except categories_mod.CategoryError as exc:
        return _cat_error(exc)
    return {"success": True, "removed": removed}


def _cat_target_row(core_id: str):
    c = categories_mod.CORE_BY_ID.get(core_id)
    if not c:
        return None, JSONResponse(status_code=404, content={"success": False, "error": f"Core category '{core_id}' not found"})
    if not c["template_row"] or c["shared"]:
        return None, JSONResponse(status_code=422, content={"success": False, "error": f"{c['name']} has no target to set."})
    return c["template_row"], None


@app.put("/api/decks/{deck_id}/category-targets/{core_id}")
async def api_category_target_set(deck_id: str, core_id: str, request: Request):
    """Per-deck target for a core category (stored as the composition template override)."""
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    row, err = _cat_target_row(core_id)
    if err:
        return err
    body = await _json_body(request)
    value = body.get("target") if isinstance(body, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or not (0 <= value <= categories_mod.TARGET_MAX):
        return _bad_request(categories_mod.CategoryError(f"target must be a whole number 0-{categories_mod.TARGET_MAX}."), 422)
    try:
        tpl = await asyncio.to_thread(composition_mod.set_template_override, r, deck, {row: value})
    except composition_mod.CompositionError as exc:
        return _bad_request(exc, 422)
    return {"success": True, "target": tpl[row], "default": tpl["default"][row]}


@app.delete("/api/decks/{deck_id}/category-targets/{core_id}")
async def api_category_target_reset(deck_id: str, core_id: str):
    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    row, err = _cat_target_row(core_id)
    if err:
        return err
    tpl = await asyncio.to_thread(composition_mod.reset_row, r, deck, row)
    return {"success": True, "target": tpl[row], "default": tpl["default"][row]}


@app.get("/api/deck-writeup/primer-form")
async def api_primer_form():
    """Field definitions for the guided primer form."""
    from analytics.deck_writeup import PRIMER_FIELDS

    return {"success": True, "fields": PRIMER_FIELDS}


def _writeup_known_cards(deck: dict, meta: list) -> list:
    return sorted({c["name"] for c in meta if c.get("name")} | {n for n in deck.get("commanders") or [] if n})


def _resolve_bracket(deck: dict, spirit: dict, explicit=None) -> Optional[str]:
    """Bracket target for the prompt: the form's, else the Journal's target_bracket, else the worker's estimate."""
    for cand in (explicit, (spirit or {}).get("target_bracket")):
        if cand not in (None, "", 0):
            return str(cand)
    try:
        rec = bracket_worker.load_result(r, registry.registry_id_of(deck))
        est = ((rec or {}).get("result") or {}).get("estimated_bracket")
        return str(est) if est else None
    except Exception:  # noqa: BLE001 - a missing estimate just leaves the target unspecified
        return None


def _writeup_context(deck: dict, meta: list, explicit_bracket=None) -> dict:
    """Identity, bracket target, Journal/Identity spirit and deterministic table facts for the prompt packet.
    Never raises: Redis or Spellbook trouble degrades to an emptier packet."""
    from analytics.deck_writeup import service as writeup_service

    try:
        spirit = writeup_service.spirit_for_deck(r, deck)
    except Exception:  # noqa: BLE001
        spirit = None
    try:
        facts = writeup_service.table_facts_for_deck(r, deck, meta)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("commander_lab.app").warning("table facts unavailable: %s", exc)
        facts = None
    return {"identity": deck.get("color") or "C", "bracket": _resolve_bracket(deck, spirit or {}, explicit_bracket),
            "spirit": spirit, "table_facts": facts}


def _section_view(sec: dict, answers: dict, known: list) -> dict:
    """One generated section as the builder renders it: id, title, sanitized html, markdown, inferred fields."""
    from analytics.deck_writeup import sanitize_primer_html
    from analytics.deck_writeup.service import inferred_fields

    html_text = sec.get("html") or sanitize_primer_html(sec.get("markdown") or "", known)
    return {"id": sec["id"], "title": sec["title"], "html": sanitize_primer_html(html_text, known),
            "markdown": sec.get("markdown") or "", "inferred": [] if sec.get("kept") else inferred_fields(answers, sec["id"])}


async def _oracle_call(fn):
    """Run a blocking oracle-backed `fn(ask)` in a thread; map oracle trouble to the standard responses.
    Returns (result, None) or (None, JSONResponse)."""
    from analytics import deck_writeup
    from analytics.oracle_client import OracleAuthError, OracleError, OracleUnavailable

    def _run():
        client = _oracle_client()
        client.timeout = GENRE_AUTOFILL_TIMEOUT_S
        return fn(lambda system, user: client.ask(user, system=system))

    try:
        return await asyncio.to_thread(_run), None
    except deck_writeup.WriteupError as exc:
        return None, JSONResponse(status_code=422, content={"success": False, "error": str(exc), "error_kind": exc.kind})
    except OracleUnavailable as exc:
        return None, _oracle_offline(exc.detail)
    except OracleAuthError:
        return None, _oracle_offline("oracle rejected the API key")
    except OracleError as exc:
        return None, JSONResponse(status_code=502, content={"success": False, "error": str(exc)[:300]})


async def _deck_writeup(deck_id: str, request: Request, kind: str):
    """Shared handler for description / primer generation. Body is the legacy flat form
    ({theme, game_plan, ...}) or the builder's {answers, options, keep}. Nothing is saved except the
    description staged for the Description panel when options.description is true."""
    from analytics import deck_writeup
    from analytics.deck_writeup import service as writeup_service, store as deck_store

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request)
    body = body if isinstance(body, dict) else {}
    wizard = isinstance(body.get("answers"), dict)
    opts = body.get("options") if isinstance(body.get("options"), dict) else {}
    answers = body["answers"] if wizard else {}
    if wizard:
        form, whys, fact_ids, bracket_hint = writeup_service.answers_to_form(answers)
    else:
        form, whys, fact_ids, bracket_hint = body, None, None, body.get("bracket")
    meta = await asyncio.to_thread(writeup_service.card_meta_for_deck, r, deck)
    if kind == "primer" and not wizard:
        missing = deck_writeup.missing_required(deck_writeup.clean_form(form)) if _form_ok(form) else []
        if missing:
            return _bad_request(ValueError(f"Fill in: {', '.join(missing)}."), 422)
    offline = await asyncio.to_thread(_oracle_preflight)
    if offline is not None:
        return offline
    ctx = await asyncio.to_thread(_writeup_context, deck, meta, bracket_hint)
    common = dict(identity=ctx["identity"], bracket=ctx["bracket"], spirit=ctx["spirit"],
                  table_facts=ctx["table_facts"], voice=opts.get("voice"), key_whys=whys)
    known = _writeup_known_cards(deck, meta)

    if kind == "description":
        out, err = await _oracle_call(lambda ask: deck_writeup.generate_description(deck, meta, ask, form, **common))
        if err:
            return err
        return {"success": True, "description": out["text"], "suggestions": [out["text"]], "warnings": out["warnings"]}

    keep = {sid: deck_writeup.sanitize_primer_html(h, known) for sid, h in (body.get("keep") or {}).items()
            if isinstance(h, str)} if isinstance(body.get("keep"), dict) else {}
    secs = opts.get("sections") if isinstance(opts.get("sections"), list) else None
    out, err = await _oracle_call(lambda ask: deck_writeup.generate_primer(
        deck, meta, ask, form, fact_ids=fact_ids, wizard=wizard, length=opts.get("length"), sections=secs,
        keep=keep, **common))
    if err:
        return err
    resp = {"success": True, "primer": out["text"], "warnings": list(out["warnings"]),
            "sections": [_section_view(s, answers, known) for s in out["sections"]]}
    if wizard and opts.get("description"):
        pitch_form = dict(form, theme=form.get("theme") or "")
        desc, derr = await _oracle_call(lambda ask: deck_writeup.generate_description(deck, meta, ask, pitch_form, **common))
        if derr:
            resp["warnings"].append("The short description could not be drafted; the primer is unaffected.")
        else:
            resp["description"] = desc["text"]
            resp["warnings"] += [w for w in desc["warnings"] if w not in resp["warnings"]]
            await asyncio.to_thread(deck_store.stage_description, r, registry.registry_id_of(deck), desc["text"])
    return resp


def _form_ok(form: dict) -> bool:
    from analytics.deck_writeup import FormError, clean_form

    try:
        clean_form(form)
        return True
    except FormError:
        return False


@app.post("/api/decks/{deck_id}/description/generate")
@app.post("/api/decks/{deck_id}/description/suggest")
async def api_deck_description_generate(deck_id: str, request: Request):
    return await _deck_writeup(deck_id, request, "description")


@app.post("/api/decks/{deck_id}/primer/generate")
async def api_deck_primer_generate(deck_id: str, request: Request):
    return await _deck_writeup(deck_id, request, "primer")


# ---- Guided primer builder: page, draft autosave, suggestions, saved primer, description ----------------

def _storage_error(exc: Exception):
    from analytics.deck_writeup import WriteupError

    if isinstance(exc, WriteupError):
        status = 404 if exc.kind == "not_found" else 413 if exc.kind == "too_large" else 422
        return JSONResponse(status_code=status, content={"success": False, "error": str(exc), "error_kind": exc.kind})
    logging.getLogger("commander_lab.app").warning("primer storage failed: %s", exc)
    return JSONResponse(status_code=503, content={"success": False, "error": "Storage is unavailable.",
                                                  "error_kind": "storage"})


def _primer_build_context(deck_name: str) -> dict:
    """Bootstrap JSON for templates/deck_primer_build.html (read-only; no model calls)."""
    from analytics.deck_writeup import service as writeup_service, store, suggest

    deck = next((d for d in registry.list_decks(r) if d.get("name") == deck_name), None) or {}
    rid = registry.registry_id_of(deck) if deck else ""
    meta = writeup_service.card_meta_for_deck(r, deck)
    spirit = writeup_service.spirit_for_deck(r, deck)
    try:
        facts = writeup_service.table_facts_for_deck(r, deck, meta)
    except Exception:  # noqa: BLE001
        facts = {"facts": []}
    draft, primer = store.load_draft(r, rid), store.load_primer(r, rid)
    # Rebuild with guide starts from the saved answers; a newer autosaved draft wins over them.
    answers = (draft or {}).get("answers") if draft and (not primer or draft["updated_at"] >= primer["updated_at"]) \
        else (primer or {}).get("answers")
    genre = spirit.get("genre") or {}
    tier = ((journal_mod.public(journal_mod.load_journal(r, deck) or {}).get("target_power_level") or {}).get("tier")
            if spirit.get("confirmed") else None)
    boot = {"deck": {"id": rid, "name": deck_name, "cmdr": ", ".join(deck.get("commanders") or []),
                     "ci": [] if deck.get("color") in (None, "", "C") else list(deck["color"]),
                     "archetype": [deck_genre.label_of("archetype", a, r) for a in genre.get("archetype") or []],
                     "themes": [deck_genre.label_of("theme", t, r) for t in genre.get("theme") or []],
                     "kindred": [deck_genre.label_of("kindred", k, r) for k in genre.get("kindred") or []],
                     "bracket": _resolve_bracket(deck, spirit), "ttw": spirit.get("target_turn_win"),
                     "tone": {"casual": "casual", "competitive": "competitive"}.get(tier or "", "focused"),
                     "cards": sum(int(c.get("quantity") or 1) for c in deck.get("cards") or [])},
            "win": suggest.win_candidates(spirit, meta, facts),
            "cards": suggest.key_card_candidates(deck, meta, spirit),
            "facts": facts.get("facts") or [], "answers": answers or {}, "has_primer": bool(primer),
            "has_draft": bool(draft), "journal_url": f"/deck/{urllib.parse.quote(deck_name)}/journal"}
    return {"primer_bootstrap": boot}


def _composition_page_context(deck_name: str) -> dict:
    """Read-only resolved composition preset for the deck composition page (no AI, no writes)."""
    deck = _find_deck_any(deck_name)
    if not deck:
        return {"selection": None, "preset": None, "slots": []}
    sel = comp_svc.resolve_for_deck(r, deck, registry.registry_id_of(deck))
    preset = sel.get("preset") or {}
    return {"selection": sel, "preset": preset, "slots": preset.get("slots") or [],
            "lands": (preset.get("structure") or {}).get("lands")}


@app.get("/deck/{deck_name}/composition", response_class=HTMLResponse)
async def deck_composition_view(request: Request, deck_name: str):
    """Deck composition: the resolved preset (name, category targets), read-only."""
    return await _deck_tab(request, deck_name, "deck_composition.html", "composition",
                           extra=_composition_page_context)


@app.get("/deck/{deck_name}/primer/build", response_class=HTMLResponse)
async def deck_primer_build_view(request: Request, deck_name: str):
    """Guided Primer Builder: 5-step wizard (docs: SPEC PF). Answers autosave as a server-side draft."""
    return await _deck_tab(request, deck_name, "deck_primer_build.html", "overview", extra=_primer_build_context)


@app.get("/api/decks/{deck_id}/primer/draft")
async def api_primer_draft_get(deck_id: str):
    from analytics.deck_writeup import store

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    rid = registry.registry_id_of(deck)
    try:
        rec = await asyncio.to_thread(store.load_draft, r, rid)
    except Exception as exc:  # noqa: BLE001
        return _storage_error(exc)
    if rec is None:
        return JSONResponse(status_code=404, content={"success": False, "error": "No draft saved."})
    return {"success": True, "answers": rec["answers"], "updated_at": rec["updated_at"]}


@app.put("/api/decks/{deck_id}/primer/draft")
async def api_primer_draft_put(deck_id: str, request: Request):
    from analytics.deck_writeup import store
    from fastapi import Response

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request)
    answers = body.get("answers") if isinstance(body, dict) else None
    try:
        await asyncio.to_thread(store.save_draft, r, registry.registry_id_of(deck), answers)
    except Exception as exc:  # noqa: BLE001
        return _storage_error(exc)
    return Response(status_code=204)


@app.post("/api/decks/{deck_id}/primer/suggest")
async def api_primer_suggest(deck_id: str, request: Request):
    """Field proposals ({pitch, early, mid, late, mull, mist, weak}: [text, ...]) and per-card `why` lines for
    blank key cards. Deterministic and grounded in the deck, so it never waits on the oracle."""
    from analytics.deck_writeup import service as writeup_service, suggest

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request)
    body = body if isinstance(body, dict) else {}
    answers = body.get("answers") if isinstance(body.get("answers"), dict) else {}

    def _build():
        meta = writeup_service.card_meta_for_deck(r, deck)
        spirit = writeup_service.spirit_for_deck(r, deck)
        try:
            facts = writeup_service.table_facts_for_deck(r, deck, meta)
        except Exception:  # noqa: BLE001
            facts = None
        by_name = {c["name"]: c for c in meta}
        cands = {c["name"]: c for c in suggest.key_card_candidates(deck, meta, spirit)}
        why = {}
        for c in answers.get("cards") or []:
            name = c.get("name") if isinstance(c, dict) else None
            if name and c.get("on", True) and not str(c.get("why") or "").strip() and name in by_name:
                why[name] = suggest.why_for(by_name[name], (cands.get(name) or {}).get("roles") or [])
        for name, cand in cands.items():
            if name not in why and not any(isinstance(c, dict) and c.get("name") == name for c in answers.get("cards") or []):
                why[name] = suggest.why_for(by_name[name], cand["roles"])
        field = body.get("field")
        sug = suggest.suggest_fields(deck, meta, spirit, facts)
        if field in suggest.FIELDS:
            sug = {field: sug[field]}
        return {"success": True, "suggestions": sug, "why": why, "facts": (facts or {}).get("facts") or []}

    try:
        return await asyncio.to_thread(_build)
    except Exception as exc:  # noqa: BLE001
        return _storage_error(exc)


@app.get("/api/decks/{deck_id}/primer")
async def api_primer_get(deck_id: str):
    from analytics.deck_writeup import store

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    rid = registry.registry_id_of(deck)

    def _load():
        return store.load_primer(r, rid), store.load_staged(r, rid), store.has_undo(r, rid)

    try:
        primer, staged, undo = await asyncio.to_thread(_load)
    except Exception as exc:  # noqa: BLE001
        return _storage_error(exc)
    return {"success": True, "primer": primer, "has_undo": undo,
            "staged_description": (staged or {}).get("description")}


@app.post("/api/decks/{deck_id}/primer")
async def api_primer_save(deck_id: str, request: Request):
    from analytics.deck_writeup import service as writeup_service, store

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request)
    if not isinstance(body, dict):
        return _bad_request(ValueError("expected a JSON object"))
    rid = registry.registry_id_of(deck)

    def _save():
        meta = writeup_service.card_meta_for_deck(r, deck)
        rec = store.save_primer(r, rid, body.get("html"), _writeup_known_cards(deck, meta), body.get("answers"),
                                body.get("markdown") or "", body.get("sections"))
        return rec, store.has_undo(r, rid)

    try:
        rec, undo = await asyncio.to_thread(_save)
    except Exception as exc:  # noqa: BLE001
        return _storage_error(exc)
    return {"success": True, "version": rec["version"], "html": rec["html"], "has_undo": undo}


@app.post("/api/decks/{deck_id}/primer/undo")
async def api_primer_undo(deck_id: str):
    from analytics.deck_writeup import store

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    try:
        rec = await asyncio.to_thread(store.undo_primer, r, registry.registry_id_of(deck))
    except Exception as exc:  # noqa: BLE001
        return _storage_error(exc)
    if rec is None:
        return JSONResponse(status_code=404, content={"success": False, "error": "Nothing to undo."})
    return {"success": True, "version": rec["version"], "html": rec["html"], "has_undo": False}


@app.put("/api/decks/{deck_id}/description")
async def api_deck_description_put(deck_id: str, request: Request):
    """Save the short deck description (<= 280 chars) with its provenance ('draft' | 'yours')."""
    from analytics.deck_writeup import store

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request)
    body = body if isinstance(body, dict) else {}
    try:
        out = await asyncio.to_thread(store.save_description, r, registry.registry_id_of(deck),
                                      body.get("description"), body.get("src") or "yours")
    except Exception as exc:  # noqa: BLE001
        return _storage_error(exc)
    return {"success": True, **out}


@app.post("/api/decks/{deck_id}/description/undo")
async def api_deck_description_undo(deck_id: str):
    from analytics.deck_writeup import store

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    try:
        out = await asyncio.to_thread(store.undo_description, r, registry.registry_id_of(deck))
    except Exception as exc:  # noqa: BLE001
        return _storage_error(exc)
    if out is None:
        return JSONResponse(status_code=404, content={"success": False, "error": "Nothing to undo."})
    return {"success": True, **out}


@app.post("/api/genres")
async def api_genres_create(request: Request):
    from analytics import genres as genres_mod

    try:
        genre, version = await asyncio.to_thread(genres_mod.create_genre, r, await _json_body(request))
    except genres_mod.GenreError as exc:
        return _bad_request(exc)
    return JSONResponse(status_code=201, content={"success": True, "genre": genre, "version": version})


@app.get("/api/genres/{genre_id}")
async def api_genres_get(genre_id: str):
    from analytics import genres as genres_mod

    try:
        genre = await asyncio.to_thread(genres_mod.get_genre, r, genre_id)
    except genres_mod.GenreNotFound:
        return JSONResponse(status_code=404, content={"success": False, "error": f"Genre '{genre_id}' not found"})
    return _genre_payload(genre=genre)


@app.put("/api/genres/{genre_id}")
async def api_genres_update(genre_id: str, request: Request):
    from analytics import genres as genres_mod

    try:
        genre, version = await asyncio.to_thread(genres_mod.update_genre, r, genre_id, await _json_body(request))
    except genres_mod.GenreNotFound:
        return JSONResponse(status_code=404, content={"success": False, "error": f"Genre '{genre_id}' not found"})
    except genres_mod.GenreError as exc:
        return _bad_request(exc)
    return {"success": True, "genre": genre, "version": version}


@app.delete("/api/genres/{genre_id}")
async def api_genres_delete(genre_id: str):
    from analytics import genres as genres_mod

    try:
        version = await asyncio.to_thread(genres_mod.delete_genre, r, genre_id)
    except genres_mod.GenreNotFound:
        return JSONResponse(status_code=404, content={"success": False, "error": f"Genre '{genre_id}' not found"})
    return {"success": True, "version": version}


@app.get("/api/decks/{deck_id}/genres")
async def api_deck_genres(deck_id: str):
    """Deterministic genre ranking for a deck: role coverage from its interaction tags."""
    from analytics import genres as genres_mod

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    graph = await asyncio.to_thread(interactions_service.get_deck_graph, r, deck)
    genres = await asyncio.to_thread(genres_mod.list_genres, r)
    qty: dict = {}
    for c in deck.get("cards") or []:
        if c.get("name"):
            try:
                qty[c["name"]] = qty.get(c["name"], 0) + max(int(c.get("quantity") or 1), 1)
            except (TypeError, ValueError):
                qty[c["name"]] = qty.get(c["name"], 0) + 1
    return _genre_payload(deck=deck.get("name"), untagged=(graph.get("counts") or {}).get("untagged", 0),
                          matches=genres_mod.genre_match(genres_mod.deck_from_graph(graph, qty), genres))


# ---------------------------------------------------------------------------
# Archetype registry (vocab PR3) -- see analytics/archetypes.py
# ---------------------------------------------------------------------------

def _archetype_payload(**extra):
    from analytics import archetypes as archetypes_mod

    return {"success": True, "version": archetypes_mod.get_version(r), **extra}


@app.get("/api/archetypes")
async def api_archetypes_list():
    from analytics import archetypes as archetypes_mod

    archetypes = await asyncio.to_thread(archetypes_mod.list_archetypes, r)
    return _archetype_payload(archetypes=archetypes, vocabulary=archetypes_mod.role_vocabulary())


@app.post("/api/archetypes")
async def api_archetypes_create(request: Request):
    from analytics import archetypes as archetypes_mod

    try:
        archetype, version = await asyncio.to_thread(archetypes_mod.create_archetype, r, await _json_body(request))
    except archetypes_mod.ArchetypeError as exc:
        return _bad_request(exc)
    return JSONResponse(status_code=201, content={"success": True, "archetype": archetype, "version": version})


@app.get("/api/archetypes/{archetype_id}")
async def api_archetypes_get(archetype_id: str):
    from analytics import archetypes as archetypes_mod

    try:
        archetype = await asyncio.to_thread(archetypes_mod.get_archetype, r, archetype_id)
    except archetypes_mod.ArchetypeNotFound:
        return JSONResponse(status_code=404, content={"success": False, "error": f"Archetype '{archetype_id}' not found"})
    return _archetype_payload(archetype=archetype)


@app.put("/api/archetypes/{archetype_id}")
async def api_archetypes_update(archetype_id: str, request: Request):
    from analytics import archetypes as archetypes_mod

    try:
        archetype, version = await asyncio.to_thread(archetypes_mod.update_archetype, r, archetype_id, await _json_body(request))
    except archetypes_mod.ArchetypeNotFound:
        return JSONResponse(status_code=404, content={"success": False, "error": f"Archetype '{archetype_id}' not found"})
    except archetypes_mod.ArchetypeError as exc:
        return _bad_request(exc)
    return {"success": True, "archetype": archetype, "version": version}


@app.delete("/api/archetypes/{archetype_id}")
async def api_archetypes_delete(archetype_id: str):
    from analytics import archetypes as archetypes_mod

    try:
        version = await asyncio.to_thread(archetypes_mod.delete_archetype, r, archetype_id)
    except archetypes_mod.ArchetypeNotFound:
        return JSONResponse(status_code=404, content={"success": False, "error": f"Archetype '{archetype_id}' not found"})
    return {"success": True, "version": version}


# ---------------------------------------------------------------------------
# Staple theme registry (vocab PR5) -- see analytics/staple_theme_registry.py
# ---------------------------------------------------------------------------

@app.get("/api/staple-themes")
async def api_staple_themes_list():
    from analytics import staple_theme_registry as st_mod

    entries = await asyncio.to_thread(st_mod.list_entries, r)
    return {"success": True, "entries": entries}


@app.post("/api/staple-themes")
async def api_staple_themes_create(request: Request):
    from analytics import staple_theme_registry as st_mod

    body = await _json_body(request)
    try:
        entry = await asyncio.to_thread(st_mod.set_entry, r, body.get("card"), body.get("themes"))
    except st_mod.StapleThemeError as exc:
        return _bad_request(exc)
    engine_mod.load_staple_theme_vocab(r)
    return JSONResponse(status_code=201, content={"success": True, "entry": entry})


@app.put("/api/staple-themes/{card_name}")
async def api_staple_themes_update(card_name: str, request: Request):
    from analytics import staple_theme_registry as st_mod

    body = await _json_body(request)
    try:
        entry = await asyncio.to_thread(st_mod.set_entry, r, card_name, body.get("themes"))
    except st_mod.StapleThemeError as exc:
        return _bad_request(exc)
    engine_mod.load_staple_theme_vocab(r)
    return {"success": True, "entry": entry}


@app.delete("/api/staple-themes/{card_name}")
async def api_staple_themes_delete(card_name: str):
    from analytics import staple_theme_registry as st_mod

    try:
        entry = await asyncio.to_thread(st_mod.delete_entry, r, card_name)
    except st_mod.StapleThemeNotFound:
        return JSONResponse(status_code=404, content={"success": False, "error": f"No staple theme entry for '{card_name}'"})
    engine_mod.load_staple_theme_vocab(r)
    return {"success": True, "entry": entry}


def _archetype_deck_qty(deck: dict) -> dict:
    qty: dict = {}
    for c in deck.get("cards") or []:
        if c.get("name"):
            try:
                qty[c["name"]] = qty.get(c["name"], 0) + max(int(c.get("quantity") or 1), 1)
            except (TypeError, ValueError):
                qty[c["name"]] = qty.get(c["name"], 0) + 1
    return qty


@app.get("/api/decks/{deck_id}/archetype")
async def api_deck_archetype(deck_id: str):
    """Resolved archetype for this deck: a manual override, else the best whole-deck derived
    match (role coverage from the deck's interaction tags), plus the full ranking."""
    from analytics import archetypes as archetypes_mod

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    rid = registry.registry_id_of(deck)
    graph = await asyncio.to_thread(interactions_service.get_deck_graph, r, deck)
    qty = _archetype_deck_qty(deck)
    out = await asyncio.to_thread(archetypes_mod.resolve_for_deck, r, rid, graph, qty)
    return {"success": True, "deck": deck.get("name"), **out}


@app.put("/api/decks/{deck_id}/archetype")
async def api_set_deck_archetype(deck_id: str, request: Request):
    """Body {mode:'auto'} or {mode:'manual', archetype_id}."""
    from analytics import archetypes as archetypes_mod

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    body = await _json_body(request)
    if not isinstance(body, dict):
        return _bad_request(ValueError("JSON object body required"))
    rid = registry.registry_id_of(deck)
    try:
        await asyncio.to_thread(archetypes_mod.set_selection, r, rid, body)
    except archetypes_mod.ArchetypeError as exc:
        return _bad_request(exc)
    except archetypes_mod.ArchetypeNotFound as exc:
        return JSONResponse(status_code=404, content={"success": False, "error": f"Archetype '{exc}' not found"})
    graph = await asyncio.to_thread(interactions_service.get_deck_graph, r, deck)
    out = await asyncio.to_thread(archetypes_mod.resolve_for_deck, r, rid, graph, _archetype_deck_qty(deck))
    return {"success": True, "deck": deck.get("name"), **out}


@app.delete("/api/decks/{deck_id}/archetype")
async def api_reset_deck_archetype(deck_id: str):
    from analytics import archetypes as archetypes_mod

    deck, err = _deck_or_404(deck_id)
    if err:
        return err
    rid = registry.registry_id_of(deck)
    await asyncio.to_thread(archetypes_mod.reset_selection, r, rid)
    graph = await asyncio.to_thread(interactions_service.get_deck_graph, r, deck)
    out = await asyncio.to_thread(archetypes_mod.resolve_for_deck, r, rid, graph, _archetype_deck_qty(deck))
    return {"success": True, "deck": deck.get("name"), **out}


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

