"""Individual lots from a CWS Marketing bid catalog.

https://bid.cwsmarketing.com/auctions/catalog/id/<N>

This is where the actual aircraft and vessels in a Treasury sale live — the
Treasury calendar only names the sale.

**Headed Chromium is required, and that is not a preference.** The site sits
behind CloudFront with a bot rule that returns:

  * HTTP 202 and a stub to plain HTTP clients (curl_cffi, requests)
  * HTTP 403 "Request blocked" to *headless* Chromium

Headed Chromium with a throwaway profile gets the real page. On a machine with
no display (WSL, CI) it still works, but CI needs an X server — the workflow
wraps this in xvfb-run. A persistent context is used because `new_page()` is
unreliable with one; reuse `context.pages[0]`.

Each lot is an `<a id="lot{number}">` whose sibling link points at
/lot-details/index/catalog/{catalog}/lot/{lotId}/{slug}.
"""
from __future__ import annotations

import re
import sys
import tempfile

from .common import Lot, ScraperFailure, lot_category

SOURCE = "cws"
CATALOG_URL = "https://bid.cwsmarketing.com/auctions/catalog/id/{cid}?ipp=100"

# Pulled out of the page in one evaluate() — walking the DOM in JS is far more
# robust here than regexing server HTML, because the list is rendered client side.
# Pulled out of the page in one evaluate(). The lot list is rendered client
# side, so walking the DOM in JS beats regexing server HTML.
#
# The TITLE comes from the URL slug, not the link text: the first
# /lot-details/ anchor on a row is the lot number ("Lot #100"), while the href
# carries the real description
# (/lot/17057/2005-Aviat-Husky-A1-B-Aircraft-...).
_EXTRACT_JS = """() => {
  const byLot = new Map();
  document.querySelectorAll('a[href*="/lot-details/"]').forEach(link => {
    const href = link.href.split('?')[0];
    const m = href.match(/\\/catalog\\/(\\d+)\\/lot\\/(\\d+)(?:\\/([^/]+))?/);
    if (!m) return;
    const lotId = m[2];
    const slug = m[3] || '';
    const prev = byLot.get(lotId);
    // Keep whichever anchor for this lot carries a slug (the descriptive one).
    if (prev && (!slug || prev.slug.length >= slug.length)) return;

    const row = link.closest('tr, li, .auc-lot-cell, .auc-lot-container') || link.parentElement;
    const text = row ? (row.innerText || '').replace(/\\s+/g, ' ').trim() : '';
    const img = row ? row.querySelector('img[src*="http"]') : null;
    const bid = text.match(/(?:Current Bid|Starting)\\s*\\$?\\s*([\\d,]+)/i);
    const num = text.match(/Lot\\s*#\\s*(\\w+)/i);

    byLot.set(lotId, {
      lotId, slug, catalogId: m[1], url: href,
      lotNumber: num ? num[1] : '',
      linkText: (link.innerText || '').replace(/\\s+/g, ' ').trim(),
      image: img ? img.src : null,
      bid: bid ? bid[1] : null,
      text: text.slice(0, 300)
    });
  });
  return [...byLot.values()];
}"""


def _fallback_title(slug: str) -> str:
    """'2005-Aviat-Husky-A1-B-Aircraft-2026910050031101-007-0000' -> readable."""
    if not slug:
        return ""
    text = re.sub(r"[-_]+", " ", slug).strip()
    # Trailing asset-control numbers are noise on a card.
    text = re.sub(r"\s+\d{10,}(?:\s+[\d-]+)*$", "", text)
    return text.strip()


def scrape_catalog(catalog_id: str, sale_title: str = "", end_date: str = "",
                   sale_category: str = "") -> list[Lot]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ScraperFailure("cws_lots: playwright not installed") from e

    url = CATALOG_URL.format(cid=catalog_id)
    rows = []
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                tempfile.mkdtemp(),          # fresh profile each run avoids flagging
                headless=False,              # see module docstring — not optional
                viewport={"width": 1500, "height": 1100},
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            try:
                page = ctx.pages[0]          # persistent context: do not new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(9000)  # the lot list renders client side
                title = page.title() or ""
                if "ERROR" in title.upper() or "could not be satisfied" in title:
                    raise ScraperFailure(
                        f"cws_lots: catalog {catalog_id} blocked by CloudFront")
                rows = page.evaluate(_EXTRACT_JS)
            finally:
                ctx.close()
    except ScraperFailure:
        raise
    except Exception as e:
        raise ScraperFailure(f"cws_lots: catalog {catalog_id} failed — {e}") from e

    lots: list[Lot] = []
    seen: set[str] = set()
    for r in rows:
        lot_id = r.get("lotId") or ""
        if not lot_id or lot_id in seen:
            continue
        seen.add(lot_id)
        # Slug first — "2005-Aviat-Husky-A1-B-Aircraft-..." — then link text.
        title = _fallback_title(r.get("slug") or "") or r.get("linkText") or ""
        if re.fullmatch(r"Lot\s*#?\s*\w*", title, re.I):
            title = ""            # the lot-number anchor is not a description
        if not title:
            continue
        bid = r.get("bid")
        lots.append(Lot(
            source=SOURCE,
            lot_id=f"{catalog_id}-{lot_id}",
            title=title,
            sale_ref=str(catalog_id),
            sale_title=sale_title,
            lot_number=r.get("lotNumber") or "",
            category=lot_category(title, sale_category),
            end_date=end_date,
            min_bid=f"${bid}" if bid else None,
            url=r.get("url") or url,
            image_url=r.get("image") or None,
        ))

    if not lots:
        raise ScraperFailure(f"cws_lots: catalog {catalog_id} parsed 0 lots")
    print(f"  cws_lots[{catalog_id}]: {len(lots)} lots", file=sys.stderr)
    return lots
