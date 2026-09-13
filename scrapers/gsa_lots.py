"""Individual lots from GSA Auctions, via its JSON API.

gsaauctions.gov is a single-page app; the data behind it is a public JSON
gateway at https://www.ppms.gov/gw/… . Two things make this non-obvious:

  * The search endpoint is a **POST** to /api/v1/auctions. A GET to the same
    path returns 401 "Token expired or invalid", which reads like the whole API
    needs credentials. It does not — the public browse endpoints are open.
  * Categories are numeric codes, listed at /api/v1/auction-categories.

Images are deliberately not collected. The SPA resolves them through
/storage/presigned-urls, and those URLs carry an AWS token that expires after
one hour — on a page rebuilt once a day they would be broken almost always.
"""
from __future__ import annotations

import sys

from curl_cffi import requests as cr

from .common import Lot, ScraperFailure, browser_post_json

SOURCE = "gsa"
SEARCH_URL = "https://www.ppms.gov/gw/auction/ppms/api/v1/auctions"
LOT_URL = "https://www.gsaauctions.gov/auctions/auction-details/{auction_id}"
PAGE_SIZE = 100

# GSA category code -> this project's category vocabulary. Only the asset types
# the site tracks; the other ~20 codes (furniture, lab equipment…) are ignored.
CATEGORIES = {
    20: "aircraft",
    40: "vessel",
    300: "vehicle",
    310: "vehicle",
    320: "vehicle",
    170: "vehicle",
}


def _search(codes: list[int], page: int, status: str = "active") -> dict:
    params = {"page": page, "size": PAGE_SIZE, "sort": "auctionEndDate,ASC"}
    body = {
        "advancedSearchText": "", "auctionSearchTypeAdvanced": "ALL_WORDS",
        "auctionStatus": status, "categoryCodeList": codes,
        "unCheckedCategoryList": [], "states": [], "zipCode": "", "radius": "",
        "auctionType": "", "minPrice": "", "maxPrice": "", "bidDeposit": None,
        "saleNumber": "", "auctionEndDateFrom": "", "auctionEndDateTo": "",
        "params": params,
    }
    r = cr.post(SEARCH_URL, params=params, json=body, impersonate="chrome",
                timeout=60, headers={"Accept": "application/json"})
    if r.status_code == 200:
        return r.json()

    # Refused as a plain client. This happens from CI runners (403 block page)
    # while the same request from a browser is fine, so reissue it from inside
    # a real page on gsaauctions.gov.
    qs = f"?page={page}&size={PAGE_SIZE}&sort=auctionEndDate,ASC"
    data = browser_post_json("https://www.gsaauctions.gov/auctions/home",
                             SEARCH_URL, body, qs)
    if isinstance(data, dict) and "__error" not in data:
        return data
    raise ScraperFailure(
        f"gsa: HTTP {r.status_code} direct, and the browser fallback failed too")


def _money(v) -> str | None:
    try:
        return f"${float(v):,.0f}" if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def scrape() -> list[Lot]:
    lots: list[Lot] = []
    seen: set[str] = set()

    for code, category in CATEGORIES.items():
        page, pages = 1, 1
        while page <= pages:
            try:
                data = _search([code], page)
            except ScraperFailure:
                raise
            except Exception as e:
                raise ScraperFailure(f"gsa: category {code} failed — {e}") from e

            pages = max(1, int(data.get("totalPages") or 1))
            for it in data.get("auctionDTOList") or []:
                lot_id = str(it.get("auctionId") or it.get("lotId") or "")
                if not lot_id or lot_id in seen:
                    continue
                seen.add(lot_id)

                loc = it.get("location") or {}
                city, state = loc.get("city"), loc.get("state")
                location = ", ".join(x.title() if x and x.isupper() else x
                                     for x in (city, state) if x) or None

                lots.append(Lot(
                    source=SOURCE,
                    lot_id=lot_id,
                    title=(it.get("lotName") or "").strip(),
                    sale_ref=str(it.get("salesNumber") or ""),
                    sale_title="GSA Auctions",
                    lot_number=str(it.get("lotNumber") or ""),
                    category=category,
                    end_date=(it.get("endDate") or "")[:10],
                    current_bid=_money(it.get("currentBid")),
                    min_bid=_money(it.get("minBid")),
                    location=location,
                    url=LOT_URL.format(auction_id=lot_id),
                ))
            page += 1

    if not lots:
        # GSA always has hundreds of active lots; zero means the API changed.
        raise ScraperFailure("gsa: search returned no lots at all")
    print(f"  gsa: {len(lots)} lots", file=sys.stderr)
    return lots
