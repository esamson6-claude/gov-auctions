"""Scrape realestatesales.gov — GSA's real property (real estate) sales.

Each property is its own dated auction, so these are modelled as Auction
events, exactly like the individual Treasury real-estate sales CWS lists. They
are not lots: there is no parent sale containing them.

The listing page carries everything needed in plain server-rendered HTML — no
API, no bot protection, no browser required. Dates come from `data-start-date`
/ `data-end-date` attributes rather than prose, which is a gift.

Volume is genuinely low (a handful at a time); GSA does not sell real property
often. A small number is correct, not a sign of a broken parser — but zero is
not, hence the ScraperFailure below.
"""
from __future__ import annotations

import html as _html
import re

from curl_cffi import requests as cr

from .common import Auction, ScraperFailure, save_raw

SOURCE = "realestatesales"
URL = "https://realestatesales.gov/our-listing/"
DETAIL_URL = "https://realestatesales.gov/asset-details/?property_id={pid}"

# Each card opens with its own asset-details link; split there and take
# everything up to the next one.
_CARD_RE = re.compile(
    r'<a[^>]+href="/asset-details/\?property_id=(\d+)".*?(?=<a[^>]+href="/asset-details/\?property_id=|\Z)',
    re.S | re.I,
)
_TITLE_RE = re.compile(r"<h2>\s*(.*?)\s*</h2>", re.S | re.I)
_ADDR_RE = re.compile(r"<h5[^>]*>(.*?)</h5>", re.S | re.I)
_PRICE_RE = re.compile(r'class="property-price">(.*?)</h4>', re.S | re.I)
_IMG_RE = re.compile(r'class="slide-img"[^>]+src="([^"]+)"', re.I)
_STRIPE_RE = re.compile(r'class="stripe">\s*([^<]+)', re.I)
_DATES_RE = re.compile(
    r'data-start-date="([^"]*)"[^>]*data-end-date="([^"]*)"', re.I)
_TAG_RE = re.compile(r"<[^>]+>")
# The <h5> holds the street inside a <span> and the city line AFTER it:
#   <h5 title="..."><span>Off County Road 31 (41.94...<br/></span>Decatur, NE 68020</h5>
# Taking the text after </span> gives a clean "City, ST ZIP" instead of the
# truncated street with its ellipsis.
_CITY_AFTER_SPAN_RE = re.compile(r"</span>(.*?)$", re.S | re.I)


def _clean(fragment: str) -> str:
    text = _html.unescape(_TAG_RE.sub(" ", fragment or ""))
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def scrape() -> list[Auction]:
    try:
        r = cr.get(URL, impersonate="chrome", timeout=45)
    except Exception as e:
        raise ScraperFailure(f"{SOURCE}: fetch failed — {e}") from e
    if r.status_code != 200:
        raise ScraperFailure(f"{SOURCE}: HTTP {r.status_code}")

    save_raw(SOURCE, r.text)
    cards = _CARD_RE.findall(r.text)
    blocks = _CARD_RE.finditer(r.text)

    auctions: list[Auction] = []
    for m in blocks:
        pid, block = m.group(1), m.group(0)
        title_m = _TITLE_RE.search(block)
        if not title_m:
            continue
        title = _clean(title_m.group(1))
        if not title:
            continue

        addr_m = _ADDR_RE.search(block)
        addr_html = addr_m.group(1) if addr_m else ""
        addr = _clean(addr_html)
        city_m = _CITY_AFTER_SPAN_RE.search(addr_html)
        city = _clean(city_m.group(1)) if city_m else ""
        price = _clean(_PRICE_RE.search(block).group(1)) if _PRICE_RE.search(block) else ""
        # "Current Bid $44,000" -> "$44,000"
        bid = re.search(r"\$[\d,]+", price)
        stripe = _clean(_STRIPE_RE.search(block).group(1)) if _STRIPE_RE.search(block) else ""

        dates = _DATES_RE.search(block)
        start = (dates.group(1) or "")[:10] if dates else ""
        end = (dates.group(2) or "")[:10] if dates else ""

        # "Coming Soon" is GSA's own word for a sale that is announced but not
        # yet open for bidding — the same state Treasury calls "Details coming!".
        status = "announced" if "coming soon" in stripe.lower() else "open"
        if not start:
            status = "tbd"

        # Use the clean city, not the raw <h5> text — that still carries the
        # page's truncated street ("Off County Road 31 (41.94...").
        desc_bits = [b for b in (city or addr, price, stripe) if b]
        auctions.append(Auction(
            source=SOURCE,
            title=title,
            start_date=start,
            end_date=end or None,
            categories="real-estate",
            operator="GSA Real Property",
            catalog_url=DETAIL_URL.format(pid=pid),
            location=city or (addr[:60] or None),
            status=status,
            detail_url=URL,
            image_url=_IMG_RE.search(block).group(1) if _IMG_RE.search(block) else None,
            description=" · ".join(desc_bits)[:400] or None,
        ))

    if not auctions:
        # The page always carries at least one property; none means the markup
        # changed and silence would read as "GSA is selling nothing".
        raise ScraperFailure(f"{SOURCE}: parsed 0 properties from {len(cards)} cards")
    return auctions
