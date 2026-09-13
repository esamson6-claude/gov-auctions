"""Collect upcoming US government auction events into data/auctions.csv.

Run order: fetch every source → drop sales that have finished → dedupe →
merge with what we already knew → write the CSV.

Modelled on ~/Projects/backcountry-aircraft/scrape.py, which solves the same
shape of problem. Two conventions carried over deliberately:

  * A source that cannot be read raises ScraperFailure and its previous rows are
    carried forward. Returning an empty list would read as "this source has no
    auctions" and silently delete everything it owns.
  * first_seen / last_seen per row, so the site can badge new sales and a
    listing that disappears upstream drops out on the next run by itself.
"""
from __future__ import annotations

import csv
import importlib
import sys
import re
from datetime import date, timedelta
from pathlib import Path

from scrapers.common import ScraperFailure

DATA_DIR = Path(__file__).resolve().parent / "data"
CSV_PATH = DATA_DIR / "auctions.csv"
LOTS_PATH = DATA_DIR / "lots.csv"

FIELDS = [
    "source", "title", "start_date", "end_date", "categories", "operator",
    "catalog_url", "location", "status", "detail_url", "image_url",
    "description", "first_seen", "last_seen",
]

SOURCES = ["scrapers.treasury", "scrapers.cws"]

LOT_FIELDS = [
    "source", "lot_id", "title", "sale_ref", "sale_title", "lot_number",
    "category", "end_date", "current_bid", "min_bid", "location", "url",
    "image_url", "description", "first_seen", "last_seen",
]

# Only open CWS catalogs whose sale is about something the site tracks get
# opened for lots — each one costs a headed browser session of ~15 seconds.
LOT_CATEGORIES = {"aircraft", "vessel", "vehicle"}

# A sale stays listed for a week after it ends — useful for "did that Cessna
# actually sell?" — then drops off. Sales with no date (TBD) never expire.
KEEP_DAYS_AFTER_END = 7


def _finished(row: dict, today: date) -> bool:
    end = row.get("end_date") or row.get("start_date")
    if not end:
        return False                      # TBD sales have no end to be past
    try:
        return date.fromisoformat(end) < today - timedelta(days=KEEP_DAYS_AFTER_END)
    except ValueError:
        return False


def _key(row: dict) -> tuple:
    """Identity of a sale across sources.

    The catalog URL is the real identifier — Treasury and CWS both list the same
    Treasury sales, and both link to the same catalog. Without a catalog link,
    fall back to the date plus a loose title.
    """
    if row.get("catalog_url"):
        return ("url", row["catalog_url"].rstrip("/").lower())
    title = "".join(c for c in (row.get("title") or "").lower() if c.isalnum())
    return ("date-title", row.get("start_date") or "", title[:40])


def _richer(a: dict, b: dict) -> dict:
    """Prefer whichever record carries more for the reader."""
    score = lambda r: sum(bool(r.get(f)) for f in
                          ("image_url", "description", "end_date", "location", "operator"))
    return a if score(a) >= score(b) else b


def load_previous() -> dict[tuple, dict]:
    if not CSV_PATH.exists():
        return {}
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        return {_key(r): r for r in csv.DictReader(f)}


def run_all() -> tuple[list[dict], set[str]]:
    rows: list[dict] = []
    failed: set[str] = set()
    for module_name in SOURCES:
        label = module_name.split(".")[-1]
        try:
            mod = importlib.import_module(module_name)
            found = [a.as_row() for a in mod.scrape()]
        except ScraperFailure as e:
            failed.add(label)
            print(f"  {label}: HARD FAIL — {e} (keeping previous rows)", file=sys.stderr)
            continue
        except Exception as e:  # noqa: BLE001 — one broken parser shouldn't stop the rest
            failed.add(label)
            print(f"  {label}: FAILED — {type(e).__name__}: {e}", file=sys.stderr)
            continue
        print(f"  {label}: {len(found)} auctions", file=sys.stderr)
        rows.extend(found)
    return rows, failed


def collect_lots(events: list[dict]) -> list[dict]:
    """Individual items, from GSA's API and from each open CWS catalog."""
    lots: list[dict] = []

    try:
        from scrapers import gsa_lots
        lots.extend(l.as_row() for l in gsa_lots.scrape())
    except ScraperFailure as e:
        print(f"  gsa_lots: HARD FAIL — {e}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"  gsa_lots: FAILED — {type(e).__name__}: {e}", file=sys.stderr)

    try:
        from scrapers import cws_lots
    except Exception as e:  # noqa: BLE001
        print(f"  cws_lots: unavailable — {e}", file=sys.stderr)
        return lots

    seen_catalogs: set[str] = set()
    for ev in events:
        url = ev.get("catalog_url") or ""
        m = re.search(r"bid\.cwsmarketing\.com/auctions/catalog/id/(\d+)", url)
        if not m:
            continue
        cats = set((ev.get("categories") or "").split("|"))
        if not (cats & LOT_CATEGORIES):
            continue
        cid = m.group(1)
        if cid in seen_catalogs:
            continue
        seen_catalogs.add(cid)
        # The sale's own category is the fallback for each lot inside it — a
        # lot title says "Hawker 800A", not "aircraft".
        sale_cat = next((c for c in ("aircraft", "vessel", "vehicle") if c in cats), "")
        try:
            found = cws_lots.scrape_catalog(
                cid, ev.get("title") or "", ev.get("end_date") or ev.get("start_date") or "",
                sale_cat)
            lots.extend(l.as_row() for l in found)
        except ScraperFailure as e:
            print(f"  cws_lots[{cid}]: {e}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"  cws_lots[{cid}]: FAILED — {type(e).__name__}: {e}", file=sys.stderr)

    return lots


def write_lots(lots: list[dict], today_iso: str) -> int:
    previous: dict[str, dict] = {}
    if LOTS_PATH.exists():
        with LOTS_PATH.open(newline="", encoding="utf-8") as f:
            previous = {r["lot_id"]: r for r in csv.DictReader(f)}

    rows = []
    for lot in {l["lot_id"]: l for l in lots}.values():
        rec = {f: (lot.get(f) or "") for f in LOT_FIELDS}
        rec["first_seen"] = previous.get(lot["lot_id"], {}).get("first_seen") or today_iso
        rec["last_seen"] = today_iso
        rows.append(rec)
    rows.sort(key=lambda r: (r["end_date"] or "9999", r["category"], r["title"]))

    with LOTS_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOT_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def main() -> int:
    today = date.today()
    today_iso = today.isoformat()
    print("Collecting auctions…", file=sys.stderr)

    previous = load_previous()
    scraped, failed = run_all()

    current: dict[tuple, dict] = {}
    for row in scraped:
        k = _key(row)
        current[k] = _richer(current[k], row) if k in current else row

    # Carry forward anything a failed source used to own, so an outage never
    # empties the calendar.
    if failed:
        carried = 0
        for k, row in previous.items():
            if row.get("source") in failed and k not in current:
                current[k] = row
                carried += 1
        if carried:
            print(f"  carried over {carried} row(s) from failed sources", file=sys.stderr)

    merged = []
    for k, row in current.items():
        prev = previous.get(k)
        rec = {f: (row.get(f) or "") for f in FIELDS}
        rec["first_seen"] = (prev or {}).get("first_seen") or today_iso
        rec["last_seen"] = today_iso
        merged.append(rec)

    live = [r for r in merged if not _finished(r, today)]
    dropped = len(merged) - len(live)

    # Soonest first: for an auction the deadline is the whole point. Undated
    # (TBD) sales sort to the end rather than the beginning.
    live.sort(key=lambda r: (not r["start_date"], r["start_date"], r["title"]))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(live)

    lot_count = write_lots(collect_lots(live), today_iso)

    new_count = sum(1 for r in live if r["first_seen"] == today_iso and previous)
    print(f"Done. {len(live)} upcoming auctions, {lot_count} lots"
          f"{f', {new_count} new' if new_count else ''}"
          f"{f', {dropped} finished' if dropped else ''}"
          f" -> {CSV_PATH.name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
