"""Scrape the US Treasury (TEOAF) general property auction calendar.

https://www.treasury.gov/auctions/treasury/gp/

The page is a plain <ul>, one <li> per sale, in one of three shapes:

    <li>September 16-23, 2026: <a href="...458">US Treasury Aircraft Auction</a></li>
    <li>October 21, 2026: US Treasury Vessel &amp; Aircraft Auction - Details coming!</li>
    <li>TBD: US Treasury Live/Simulcast Auction in Riverside, CA - Details coming!</li>

So a sale can be catalogued, announced-without-a-catalog, or announced without
even a date. All three are worth showing — knowing a vessel sale is coming in
January is the point of the site — so they are kept and distinguished by
`status` rather than filtered out.
"""
from __future__ import annotations

import html as _html
import re

from curl_cffi import requests as cr

from .common import (
    Auction,
    ScraperFailure,
    categories_from,
    operator_from,
    parse_date_range,
    save_raw,
)

SOURCE = "treasury"
URL = "https://www.treasury.gov/auctions/treasury/gp/"

_LI_RE = re.compile(r"<li>(.*?)</li>", re.S | re.I)
_ANCHOR_RE = re.compile(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
# "… Auction in Riverside, CA - Details coming!" → "Riverside, CA"
_LOCATION_RE = re.compile(r"\bin\s+([A-Z][A-Za-z .]+,\s*[A-Z]{2})\b")


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(_TAG_RE.sub(" ", fragment))).strip()


def scrape() -> list[Auction]:
    try:
        r = cr.get(URL, impersonate="chrome", timeout=40)
    except Exception as e:  # network, TLS, DNS…
        raise ScraperFailure(f"treasury: fetch failed — {e}") from e
    if r.status_code != 200:
        raise ScraperFailure(f"treasury: HTTP {r.status_code}")

    save_raw(SOURCE, r.text)
    items = _LI_RE.findall(r.text)
    if not items:
        # The calendar is the whole point of this source; no <li> at all means
        # the page changed shape, which must not read as "no auctions".
        raise ScraperFailure("treasury: no <li> items found — page layout changed?")

    auctions: list[Auction] = []
    unparsed: list[str] = []

    for raw in items:
        text = _clean(raw)
        if not text or ":" not in text:
            continue
        # Every calendar row starts "<date or TBD>: <title>"
        date_part, _, title_part = text.partition(":")
        date_part, title_part = date_part.strip(), title_part.strip()
        if not title_part or len(date_part) > 40:
            continue

        anchor = _ANCHOR_RE.search(raw)
        catalog_url = anchor.group(1).strip() if anchor else None
        title = re.sub(r"\s*-\s*Details coming!?\s*$", "", title_part, flags=re.I).strip()
        if not title:
            continue

        start, end = parse_date_range(date_part)
        if start is None:
            if re.fullmatch(r"TBD\.?", date_part, re.I):
                status = "tbd"          # announced with no date yet
            else:
                unparsed.append(text[:80])
                continue
        else:
            status = "open" if catalog_url else "announced"

        loc = _LOCATION_RE.search(title)
        auctions.append(
            Auction(
                source=SOURCE,
                title=title,
                start_date=start or "",
                end_date=end,
                categories=categories_from(title),
                operator=operator_from(catalog_url, title),
                catalog_url=catalog_url,
                location=loc.group(1) if loc else None,
                status=status,
                detail_url=URL,
            )
        )

    if unparsed:
        # Loudly, but without failing the run — one odd row shouldn't cost the
        # other thirteen. A missed sale is the failure this site exists to avoid.
        import sys
        print(f"  [{SOURCE}] {len(unparsed)} row(s) had an unreadable date:",
              file=sys.stderr)
        for u in unparsed:
            print(f"      {u}", file=sys.stderr)

    return auctions
