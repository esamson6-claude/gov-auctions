# gov-auctions

Tracks upcoming **US government auction events** — the sales themselves, not the
individual lots inside them — and publishes them as a filterable page.

**Live site:** (set after the first Pages deploy)

## What this is and isn't

It answers "what government auctions are coming up, and which have aircraft or
boats in them?" It does **not** list the individual aircraft, cars, boats or
properties inside a sale. That distinction drives the whole data model: one row
per *sale*, with a link out to the operator's catalog.

Aircraft and vessels were the starting point; real estate arrived free, because
CWS lists Treasury property sales individually with addresses and photos.

## Layout note

The layout mirrors `~/Projects/backcountry-aircraft` (flat `scrape.py` +
`scrapers/`), **not** the `src/` convention in the parent `~/Projects/CLAUDE.md`.
That is deliberate: the two projects solve the same shape of problem, and
matching layouts is what makes code portable between them.

## Pipeline

`scrape.py` is the entry point:

1. **fetch** — each module in `SOURCES` returns `Auction` objects
2. **dedupe** — by catalog URL first (Treasury and CWS both list the same
   Treasury sales and link to the same catalog), else by date + loose title.
   When two records describe one sale, the richer one wins
3. **expire** — sales stay 7 days past their end date, then drop out
4. **merge** — `first_seen` / `last_seen` per row, so the site can badge new
   sales and a vanished listing disappears by itself
5. `generate_html.py` — writes `docs/index.html`

## Sources

| Module | Source | Notes |
|---|---|---|
| `scrapers/treasury.py` | treasury.gov TEOAF calendar | Plain `<ul>`; the authoritative schedule, including sales with no catalog yet |
| `scrapers/cws.py` | cwsmarketing.com upcoming auctions | WordPress `.custom-card`; richer — thumbnail, description, and individual real-estate sales Treasury never lists |

A sale carries one of three `status` values, and all three are shown:

- `open` — dated, with a catalog link
- `announced` — dated, Treasury's "Details coming!"; no catalog yet
- `tbd` — announced with no date at all (the Riverside CA and Pompano Beach FL
  live/simulcast sales)

### Known, deliberately deferred

- **`bid.cwsmarketing.com`** (lot level) answers **HTTP 202** — a bot challenge,
  the same shape Trade-A-Plane throws in the aircraft project. Beating it needs
  that project's ScrapingBee stealth fetcher.
- **GSA Auctions** is a JS app over a JSON API at
  `https://www.ppms.gov/gw/auction/ppms` (base URLs are in
  `gsaauctions.gov/environment.js`). Every endpoint returns
  `401 Token expired or invalid`; auth is Okta. If the token route is found this
  becomes the best source here by far — structured JSON rather than scraped HTML.
- **realestatesales.gov** — plain HTML, 52 KB, easy whenever wanted.

## Gotchas already hit

- **Asset keywords match property features.** `\bCAR\b` matched "2-car garage"
  and tagged every house as a vehicle auction; `\bBOAT\b` matched "boat dock" on
  a marina home. Both categories now use negative lookahead. **Test any
  category or filter change against `data/auctions.csv` before shipping it** —
  this bit three times in the sibling project too.
- **Date formats differ per source.** Treasury writes `September 16-23, 2026`;
  CWS writes `Sep 9 2026 - Sep 16 2026`. The two-full-dates pattern must be
  tried *before* the single-date one, or the end of the sale is thrown away.
- **An unparseable date is reported, never silently skipped.** Missing a sale is
  the one failure this site exists to prevent.
- **A source that fails raises `ScraperFailure`** and its previous rows are
  carried forward. Returning `[]` would read as "no auctions" and wipe them.
- **Closed sales sort last and are hidden by default.** Sorting purely by date
  put finished auctions at the top of a page about what's coming up.

## Automation

`.github/workflows/daily-auctions.yml` runs at `17 6 * * *` (~2:17 AM ET),
scrapes, regenerates the page and commits to `main`.

The push **rebases first** (`git pull --rebase -X theirs`, 3 attempts). This is
not optional: in the sibling project a plain `git push` lost a 91-minute scrape
when a push landed mid-run. `-X theirs` reads backwards but is correct — during
a rebase "ours" is the upstream being replayed onto and "theirs" is the commit
being replayed, i.e. this run's own data.

## Working on it

```bash
cd ~/Projects/gov-auctions
git pull
.venv/bin/python scrape.py          # ~5s, no API keys needed
.venv/bin/python generate_html.py   # writes docs/index.html
```

To view the page in a browser (WSL needs the extracted libraries — see the
sibling project's CLAUDE.md):

```bash
export LD_LIBRARY_PATH=$HOME/.local/browserlibs/extracted/usr/lib/x86_64-linux-gnu
```

Always syntax-check the generated page; one bad line silently breaks every
filter:

```bash
node --check /tmp/ga.js   # after extracting the inline <script>
```

## Current status (2026-09-13)

30 tracked sales (28 upcoming, 2 recently closed) from 2 sources: 6
aircraft/vessel sales, 15 real estate, 2 vehicle, 6 general property.
No API keys required — both sources serve plain HTML.
