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
from datetime import date, timedelta
from pathlib import Path

from scrapers.common import ScraperFailure

DATA_DIR = Path(__file__).resolve().parent / "data"
CSV_PATH = DATA_DIR / "auctions.csv"

FIELDS = [
    "source", "title", "start_date", "end_date", "categories", "operator",
    "catalog_url", "location", "status", "detail_url", "image_url",
    "description", "first_seen", "last_seen",
]

SOURCES = ["scrapers.treasury", "scrapers.cws"]

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

    new_count = sum(1 for r in live if r["first_seen"] == today_iso and previous)
    print(f"Done. {len(live)} upcoming auctions"
          f"{f', {new_count} new' if new_count else ''}"
          f"{f', {dropped} finished' if dropped else ''}"
          f" -> {CSV_PATH.name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
