"""Scrape the CWS Marketing upcoming-auctions calendar.

https://cwsmarketing.com/auctions/upcoming-auctions/

CWS runs most of the Treasury forfeiture sales and publishes its own calendar,
which carries more than the Treasury page does: a thumbnail, a description
listing what is actually in the sale, and private-seller auctions Treasury never
mentions. WordPress markup, served plainly — no bot challenge. (The *bidding*
site, bid.cwsmarketing.com, is a different matter: it answers HTTP 202 with a
challenge, which is why lot-level data is deferred.)

Each listing is a <div class="custom-card"> holding, in order: a <p> with the
date range, an <h3> title, and a <p> description.
"""
from __future__ import annotations

import html as _html
import re
import sys

from curl_cffi import requests as cr

from .common import (
    Auction,
    ScraperFailure,
    categories_from,
    operator_from,
    parse_date_range,
    save_raw,
)

SOURCE = "cws"
URL = "https://cwsmarketing.com/auctions/upcoming-auctions/"

_CARD_RE = re.compile(
    r'<div class="custom-card".*?(?=<div class="custom-card"|\Z)', re.S)
_DATE_P_RE = re.compile(r'<p[^>]*>.*?calendar-icon.*?</p>', re.S | re.I)
_TITLE_RE = re.compile(r'<h3[^>]*>(.*?)</h3>', re.S | re.I)
_DESC_RE = re.compile(r'</h3>\s*<p[^>]*>(.*?)</p>', re.S | re.I)
_IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"[^>]*class="card-image"', re.I)
_LINK_RE = re.compile(r'href="(https://bid\.cwsmarketing\.com[^"]+)"', re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(fragment: str) -> str:
    text = _html.unescape(_TAG_RE.sub(" ", fragment or ""))
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def scrape() -> list[Auction]:
    try:
        r = cr.get(URL, impersonate="chrome", timeout=45)
    except Exception as e:
        raise ScraperFailure(f"cws: fetch failed — {e}") from e
    if r.status_code != 200:
        raise ScraperFailure(f"cws: HTTP {r.status_code}")

    save_raw(SOURCE, r.text)
    cards = _CARD_RE.findall(r.text)
    if not cards:
        raise ScraperFailure("cws: no .custom-card blocks — page layout changed?")

    auctions: list[Auction] = []
    undated = 0

    for card in cards:
        title_m = _TITLE_RE.search(card)
        if not title_m:
            continue
        title = _clean(title_m.group(1))
        if not title:
            continue

        date_m = _DATE_P_RE.search(card)
        start, end = parse_date_range(_clean(date_m.group(0)) if date_m else "")
        if start is None:
            # CWS sometimes lists a sale before dating it. Keep it — an
            # undated Treasury vessel sale is still worth knowing about.
            undated += 1

        desc = _clean(_DESC_RE.search(card).group(1)) if _DESC_RE.search(card) else None
        link_m = _LINK_RE.search(card)
        img_m = _IMG_RE.search(card)
        catalog_url = link_m.group(1) if link_m else None

        auctions.append(
            Auction(
                source=SOURCE,
                title=title,
                start_date=start or "",
                end_date=end,
                # Read the description too: the title may say "Nationwide" while
                # the body is what actually names boats, aircraft or vehicles.
                categories=categories_from(f"{title} {desc or ''}"),
                operator=operator_from(catalog_url, "cws"),
                catalog_url=catalog_url,
                status=("open" if catalog_url else "announced") if start else "tbd",
                detail_url=URL,
                image_url=img_m.group(1) if img_m else None,
                description=(desc or "")[:600] or None,
            )
        )

    if undated:
        print(f"  [{SOURCE}] {undated} listing(s) had no readable date", file=sys.stderr)
    return auctions
