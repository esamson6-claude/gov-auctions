"""Shared pieces for the auction scrapers.

Lifted and trimmed from the backcountry-aircraft project, which solves the same
shape of problem. Only what this project actually needs is kept — no ScrapingBee
yet, because both current sources serve plain HTML. When a bot-challenged source
is added (bid.cwsmarketing.com answers HTTP 202), bring the credit-budgeted
fetcher over rather than writing a new one.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Optional

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


class ScraperFailure(Exception):
    """A source could not be reached or read.

    Raised rather than returning an empty list, because empty reads as "this
    source has no auctions" and would silently delete every row it owns.
    """


@dataclass
class Auction:
    """One auction event — a sale, not an individual lot."""

    source: str
    title: str
    start_date: str                      # ISO, always present
    end_date: Optional[str] = None       # ISO; None for single-day sales
    categories: str = ""                 # pipe-separated: "aircraft|vessel"
    operator: Optional[str] = None       # CWS Marketing, HiBid, Amentum…
    catalog_url: Optional[str] = None    # None while a sale is only announced
    location: Optional[str] = None
    status: str = "open"                 # open | announced | tbd
    detail_url: Optional[str] = None     # the page we found it on
    image_url: Optional[str] = None      # catalog thumbnail, where offered
    description: Optional[str] = None    # what is in the sale, in the seller's words

    def as_row(self) -> dict:
        return asdict(self)


@dataclass
class Lot:
    """One item inside an auction — a specific aircraft, boat, vehicle…

    Separate from Auction on purpose: a sale is a date on a calendar, a lot is a
    thing you bid on. They have different lifetimes and different fields.
    """

    source: str
    lot_id: str                          # unique within the source
    title: str
    sale_ref: str = ""                   # sale/catalog this lot belongs to
    sale_title: str = ""
    lot_number: str = ""
    category: str = ""                   # aircraft | vessel | vehicle | …
    end_date: str = ""                   # ISO date the lot closes
    current_bid: Optional[str] = None
    min_bid: Optional[str] = None
    location: Optional[str] = None
    url: str = ""
    image_url: Optional[str] = None
    description: Optional[str] = None

    def as_row(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"], start=1)
}
_MONTHS.update({m[:3].lower(): i for m, i in list(_MONTHS.items())})

# "September 16-23, 2026" / "September 16 - 23, 2026"
_RANGE_RE = re.compile(
    r"([A-Za-z]+)\s+(\d{1,2})\s*[-–—]\s*(\d{1,2}),?\s*(\d{4})")
# "September 1-8 2026" with no comma is the same shape; handled above.
# "Sep 9 2026 - Sep 16 2026" — CWS names the month on both sides. Must be tried
# BEFORE _SINGLE_RE, which would otherwise match only the first date and throw
# the end of the sale away.
_TWO_DATES_RE = re.compile(
    r"([A-Za-z]+)\s+(\d{1,2}),?\s*(\d{4})\s*[-–—]\s*"
    r"([A-Za-z]+)\s+(\d{1,2}),?\s*(\d{4})")
# "October 21, 2026" / "October 21 2026"
_SINGLE_RE = re.compile(r"([A-Za-z]+)\s+(\d{1,2}),?\s*(\d{4})")

# An auction calendar spans roughly now to a couple of years out. Anything
# outside that is a parse error, not a date — the aircraft project learned this
# the hard way when a bare (19|20)\d{2} turned phone numbers into model years.
_YEAR_MIN = 2020
_YEAR_MAX = 2100


def _mk(year: int, month: int, day: int) -> Optional[str]:
    if not (_YEAR_MIN <= year <= _YEAR_MAX and 1 <= month <= 12 and 1 <= day <= 31):
        return None
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_date_range(text: str) -> tuple[Optional[str], Optional[str]]:
    """Parse an auction date from free text → (start ISO, end ISO or None).

    Returns (None, None) when nothing parses, so the caller can complain loudly.
    A silently skipped auction is worse than a noisy failure: the whole point of
    the site is not missing a sale.
    """
    if not text:
        return None, None

    m = _RANGE_RE.search(text)
    if m:
        mon, d1, d2, yr = m.groups()
        month = _MONTHS.get(mon.lower())
        if month:
            start = _mk(int(yr), month, int(d1))
            end = _mk(int(yr), month, int(d2))
            if start:
                return start, end

    m = _TWO_DATES_RE.search(text)
    if m:
        mon1, d1, y1, mon2, d2, y2 = m.groups()
        month1, month2 = _MONTHS.get(mon1.lower()), _MONTHS.get(mon2.lower())
        if month1 and month2:
            start = _mk(int(y1), month1, int(d1))
            end = _mk(int(y2), month2, int(d2))
            if start:
                return start, end

    m = _SINGLE_RE.search(text)
    if m:
        mon, d1, yr = m.groups()
        month = _MONTHS.get(mon.lower())
        if month:
            start = _mk(int(yr), month, int(d1))
            if start:
                return start, None

    return None, None


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

# A title can name several asset types — "Vessel & Aircraft Auction" is both —
# so this returns every match rather than the first.
_CATEGORY_PATTERNS = [
    ("aircraft", r"AIRCRAFT|AIRPLANE|\bPLANE\b|HELICOPTER|\bJET\b"),
    # "BOAT" must not match a house's "boat dock" — real-estate descriptions
    # sell waterfront features, and a marina property is not a vessel auction.
    ("vessel",
     r"VESSELS?|\bBOATS?\b(?!\s*(?:DOCK|SLIP|HOUSE|LIFT|RAMP))"
     r"|YACHTS?|\bMARINE\b|WATERCRAFT"),
    # "CAR" must not match "2-car garage" or "5-car carport" — the real-estate
    # descriptions are full of them, and every house was being tagged as a
    # vehicle auction. Reject a preceding digit/hyphen and a following
    # garage/port.
    ("vehicle",
     r"VEHICLE|(?<![-\d])\bCARS?\b(?!\s*(?:GARAGE|PORT))|\bAUTOS?\b"
     r"|TRUCKS?|MOTORCYCLES?|SALVAGE"),
    ("real-estate", r"REAL\s*ESTATE|REAL\s*PROPERTY|\bLAND\b|RESIDENCE"),
]


def categories_from(text: str) -> str:
    """Pipe-separated asset categories named in `text`.

    Falls back to "general" — a nationwide property sale genuinely is a mixed
    bag, and calling it that is more honest than guessing.
    """
    blob = (text or "").upper()
    found = [name for name, pat in _CATEGORY_PATTERNS if re.search(pat, blob)]
    return "|".join(found) if found else "general"


# Lots inside an aircraft or vessel sale are frequently components, not the
# whole thing — "Wingtip Position Sensor", "40 HP Yamaha Outboard Motor",
# "Float On Boat Trailer". Worth separating: someone shopping for an aeroplane
# does not want a heat exchanger.
_PART_RE = re.compile(
    r"\bPARTS?\b|\bENGINES?\b|\bMOTORS?\b|SENSOR|EXCHANGE|RECIRCULATION"
    r"|\bTRAILER\b|PROPELLERS?|WINGTIP|\bASSY\b|\bPUMPS?\b|GEARBOX"
    r"|AVIONICS|\bRADIOS?\b|\bTIRES?\b|\bWHEELS?\b|GENERATOR|COMPRESSOR",
    re.I,
)


def lot_category(title: str, sale_default: str = "") -> str:
    """Category for a single lot, using the sale it belongs to as context.

    A lot title rarely says "aircraft" or "vessel" — it says "Hawker 800A" or
    "Boston Whaler 260 Outrage". Guessing from a list of manufacturer names
    would be endless and wrong; the sale already tells us (catalog 458 IS the
    Treasury aircraft sale), so that is the fallback.

    Parts win over the sale default, so an aircraft sale's heat exchanger is not
    filed as an aircraft.
    """
    if _PART_RE.search(title or ""):
        return "parts"
    named = categories_from(title)
    if named != "general":
        return named
    return sale_default or "general"


def operator_from(url: str | None, text: str = "") -> Optional[str]:
    """Which auction house is running the sale, from its catalog link."""
    blob = f"{url or ''} {text or ''}".lower()
    if "hibid" in blob:
        return "HiBid"
    if "cwsmarketing" in blob or "cwsams" in blob:
        return "CWS Marketing"
    if "gsaauctions" in blob or "ppms.gov" in blob:
        return "GSA Auctions"
    if "amentum" in blob:
        return "Amentum"
    return None


def save_raw(name: str, html: str) -> None:
    """Keep the fetched page so a parser bug can be diagnosed without refetching."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / f"{name}.html").write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# Browser-routed fetching
# ---------------------------------------------------------------------------
#
# Some sources serve plain HTTP clients happily from a home connection but
# reject them from a CI runner: on GitHub Actions the CWS calendar returns
# HTTP 202 and the GSA API returns a 403 block page, while both are fine
# locally. Headed Chromium gets through where plain requests do not — that is
# already proven for bid.cwsmarketing.com, whose CloudFront rule blocks
# *headless* specifically.
#
# So: try plain HTTP first (fast, cheap), and fall back to driving a real
# browser. No paid proxy involved.

def _browser_context(playwright):
    import tempfile
    return playwright.chromium.launch_persistent_context(
        tempfile.mkdtemp(),
        headless=False,          # headless is the tell — see cws_lots docstring
        viewport={"width": 1400, "height": 1000},
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    )


def browser_get_text(url: str, wait_ms: int = 6000) -> Optional[str]:
    """Full HTML of `url` as a real browser sees it. None if unavailable."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as p:
            ctx = _browser_context(p)
            try:
                page = ctx.pages[0]
                page.goto(url, wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(wait_ms)
                return page.content()
            finally:
                ctx.close()
    except Exception:
        return None


def browser_post_json(origin: str, api_url: str, payload: dict,
                      params: str = "") -> Optional[dict]:
    """POST JSON from inside a page on `origin` and return the parsed response.

    Issued by the page itself rather than by Python, so it carries the browser's
    own TLS fingerprint, cookies and Origin header — which is the whole point
    when a plain client is being turned away.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as p:
            ctx = _browser_context(p)
            try:
                page = ctx.pages[0]
                page.goto(origin, wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(3000)
                return page.evaluate(
                    """async ([url, body]) => {
                        const r = await fetch(url, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json',
                                      'Accept': 'application/json'},
                            body: JSON.stringify(body)
                        });
                        if (!r.ok) return {__error: r.status};
                        return await r.json();
                    }""",
                    [api_url + params, payload],
                )
            finally:
                ctx.close()
    except Exception:
        return None
