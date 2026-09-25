"""
Multi-location chain site ("Crust & Co")
========================================
One brand, one site per region, each with a store locator. Payment acceptance
varies per location, and the site-wide footer shows brand-level card logos
that are NOT a per-store claim — so location validation must scope to the
store's own content.

Pages (server-rendered, except the locator search which is client-side):
  /chain/{region}/                        home (nav + footer links)
  /chain/{region}/{privacy|terms|contact|faq}
  /chain/{region}/stores                  store locator (async search)
  /chain/{region}/stores/{store_id}       store detail + payments at this store

API:
  GET /api/v1/chain/{region}/stores?q=    locator search (ranked candidates)
"""

import asyncio
import html
import json
import os
import re
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from mock_merchant import catalog

DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "chain_stores.json")

INFO_PAGES = {
    "privacy": ("Privacy Policy",
                "We only use the personal data you share with us to serve your order and improve our "
                "restaurants. We never sell your data."),
    "terms":   ("Terms & Conditions",
                "Prices and menu items may vary by location. Offers cannot be combined unless stated."),
    "contact": ("Contact Us",
                "Questions about a restaurant? Use the store locator to find the location's phone "
                "number, or email hello@crustandco.example."),
    "faq":     ("FAQ",
                "Which payment options are accepted? Payment options vary by location. See the store "
                "page for the payment options accepted at that restaurant."),
}


@lru_cache(maxsize=1)
def data() -> dict:
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


def stores(region: str) -> list[dict]:
    return [s for s in data()["stores"] if s["region"] == region]


def get_store(store_id: str) -> dict | None:
    return next((s for s in data()["stores"] if s["store_id"] == store_id.upper()), None)


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 1 or t.isdigit()}


def search(region: str, query: str, limit: int = 5) -> list[dict]:
    """Rank stores by token overlap with the query — a crude geocoder stand-in
    that, like real locators, returns several nearby candidates."""
    q = _tokens(query)
    ranked = []
    for s in stores(region):
        score = len(q & _tokens(s["address"]))
        if score:
            ranked.append((score, s))
    ranked.sort(key=lambda x: -x[0])
    unit = data()["regions"][region]["distance_unit"]
    return [
        {"store_id": s["store_id"], "name": s["name"], "address": s["address"],
         "distance": f"{0.4 + i * 1.7:.1f} {unit}", "url": f"/chain/{region}/stores/{s['store_id']}"}
        for i, (_, s) in enumerate(ranked[:limit])
    ]


# ── HTML rendering ────────────────────────────────────────────────────────────

def _icons(methods: list[str], with_names: bool) -> str:
    cat = catalog.payment_method_catalog()
    items = []
    for code in methods:
        m = cat[code]
        name = f'<span class="method-name">{html.escape(m["display_name"])}</span>' if with_names else ""
        items.append(
            f'<li class="payment-icon payment-icon--{code.replace("_", "-")}" data-payment-method="{code}">'
            f'<img src="/static/icons/{m["icon"]}" alt="{html.escape(m["display_name"])}" width="38" height="24">'
            f'{name}</li>'
        )
    return "".join(items)


def _layout(region: str, title: str, body: str, extra_head: str = "") -> HTMLResponse:
    cfg = data()["regions"][region]
    base = f"/chain/{region}"
    brand = html.escape(data()["brand"])
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} | {brand} {html.escape(cfg["country"])}</title>
<link rel="stylesheet" href="/static/styles.css">{extra_head}</head>
<body>
<header><a href="{base}/" data-testid="brand-home">{brand}</a>
<nav aria-label="Main"><a href="{base}/stores">Find a store</a></nav></header>
<main>{body}</main>
<footer><nav aria-label="Legal">
<a href="{base}/privacy">Privacy Policy</a> · <a href="{base}/terms">Terms &amp; Conditions</a> ·
<a href="{base}/contact">Contact Us</a> · <a href="{base}/faq">FAQ</a></nav>
<ul class="footer-icons" data-testid="footer-payment-icons" aria-label="Accepted cards">{_icons(cfg["footer_methods"], False)}</ul>
</footer></body></html>"""
    return HTMLResponse(page)


def _region_or_404(region: str) -> dict:
    cfg = data()["regions"].get(region)
    if cfg is None:
        raise HTTPException(404, f"Region '{region}' not found")
    if cfg.get("bot_protected"):
        raise BotBlocked()
    return cfg


class BotBlocked(Exception):
    pass


BOT_BLOCK_PAGE = HTMLResponse(
    "<!doctype html><title>Access denied</title><h1>Access denied</h1>"
    "<p>Please verify you are human to continue.</p>",
    status_code=403,
)


# ── Router ────────────────────────────────────────────────────────────────────

def create_router(search_latency_ms: int = 600) -> APIRouter:
    router = APIRouter()

    @router.get("/chain/{region}/", include_in_schema=False)
    def home(region: str):
        _region_or_404(region)
        return _layout(region, "Home", f"""
<h1>Fresh sandwiches, baked daily</h1>
<p>Find your nearest {html.escape(data()["brand"])} and order ahead.</p>
<p><a href="/chain/{region}/stores">Find a store</a></p>""")

    @router.get("/chain/{region}/stores", include_in_schema=False)
    def locator(region: str):
        _region_or_404(region)
        return _layout(region, "Find a store", """
<h1>Find a store</h1>
<form role="search" aria-label="Store search" data-testid="store-search">
  <label for="store-query">Find a store near you</label>
  <input id="store-query" type="search" placeholder="Enter address, city or postcode" autocomplete="off">
  <button type="submit">Search</button>
</form>
<section data-testid="store-results" aria-label="Search results" aria-busy="false">
  <p role="status" data-testid="results-status"></p>
  <ol class="store-results" data-testid="store-result-list"></ol>
</section>""", extra_head='<script src="/static/locator.js" defer></script>')

    @router.get("/chain/{region}/stores/{store_id}", include_in_schema=False)
    def store_detail(region: str, store_id: str):
        _region_or_404(region)
        store = get_store(store_id)
        if store is None or store["region"] != region:
            raise HTTPException(404, f"Store '{store_id}' not found")
        closed = ('<p class="closed-banner" data-testid="store-status" role="note">'
                  'Temporarily closed</p>' if store.get("temporarily_closed") else "")
        payments = ""
        if store.get("display_payments", True):
            payments = f"""
<section class="panel" data-testid="store-payments" aria-labelledby="payments-heading">
  <h2 id="payments-heading">Payments accepted at this store</h2>
  <ul class="payment-list">{_icons(store["accepted_methods"], True)}</ul>
</section>"""
        return _layout(region, store["name"], f"""
<article data-testid="store-detail" data-store-id="{store['store_id']}">
<h1>{html.escape(store["name"])}</h1>
<address data-testid="store-address">{html.escape(store["address"])}</address>
{closed}
<section class="panel" aria-labelledby="hours-heading">
  <h2 id="hours-heading">Opening hours</h2><p>Every day 07:00 – 22:00</p>
</section>{payments}
<p><a href="/chain/{region}/stores">Back to store search</a></p>
</article>""")

    @router.get("/chain/{region}/{page}", include_in_schema=False)
    def info(region: str, page: str):
        _region_or_404(region)
        if page not in INFO_PAGES:
            raise HTTPException(404, "Page not found")
        title, text = INFO_PAGES[page]
        return _layout(region, title, f"<h1>{html.escape(title)}</h1><p>{html.escape(text)}</p>")

    @router.get("/api/v1/chain/{region}/stores")
    async def api_search(region: str, q: str = Query(..., min_length=2, max_length=200)):
        _region_or_404(region)
        if search_latency_ms:
            await asyncio.sleep(search_latency_ms / 1000)
        results = search(region, q)
        return {"region": region, "query": q, "total": len(results), "items": results}

    return router
