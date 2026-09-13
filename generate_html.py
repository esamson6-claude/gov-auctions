"""Render data/auctions.csv as a self-contained filterable page (docs/index.html).

Adapted from ~/Projects/backcountry-aircraft/generate_html.py, with its hard-won
fixes carried over rather than rediscovered:

  * apply() runs once at load. Without it the default filter state is not
    applied until the first click.
  * PLACEHOLDER_IMG percent-encodes its quotes. A raw apostrophe inside
    onerror="this.src='…'" closes the JS string and the fallback never renders.
  * Every colour is a token defined on :root, redefined for dark. A colour
    declared only inside a media query renders one theme's text on the other
    theme's background.

Ordering differs from the aircraft site on purpose: an auction is sorted by
when it closes, because the deadline is the point.
"""
from __future__ import annotations

import csv
import html
import json
import re
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CSV_PATH = PROJECT_ROOT / "data" / "auctions.csv"
LOTS_PATH = PROJECT_ROOT / "data" / "lots.csv"
DOCS_DIR = PROJECT_ROOT / "docs"
OUT_PATH = DOCS_DIR / "index.html"

CATEGORY_LABELS = {
    "aircraft": "Aircraft",
    "parts": "Parts",
    "vessel": "Vessels",
    "vehicle": "Vehicles",
    "real-estate": "Real estate",
    "general": "General property",
}

# Quotes percent-encoded — see module docstring.
PLACEHOLDER_IMG = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 400 220%22>"
    "<rect width=%22400%22 height=%22220%22 fill=%22%23dfe3e8%22/>"
    "<text x=%2250%25%22 y=%2250%25%22 dominant-baseline=%22middle%22"
    " text-anchor=%22middle%22 font-family=%22sans-serif%22 font-size=%2216%22"
    " fill=%22%23808a96%22>No catalog image</text></svg>"
)


def _fmt_range(start: str, end: str) -> str:
    """'16–23 Sep 2026', '21 Oct 2026', or 'Date to be announced'."""
    if not start:
        return "Date to be announced"
    s = date.fromisoformat(start)
    if not end or end == start:
        return s.strftime("%-d %b %Y")
    e = date.fromisoformat(end)
    if (s.year, s.month) == (e.year, e.month):
        return f"{s.day}–{e.day} {s.strftime('%b %Y')}"
    return f"{s.strftime('%-d %b')} – {e.strftime('%-d %b %Y')}"


def _countdown(start: str, end: str, today: date) -> tuple[str, str]:
    """(text, urgency) — urgency drives the colour stripe."""
    if not start:
        return "Not yet scheduled", "tbd"
    s = date.fromisoformat(start)
    e = date.fromisoformat(end) if end else s
    if today > e:
        return "Closed", "closed"
    if s <= today <= e:
        left = (e - today).days
        return ("Closes today" if left == 0 else f"Open now · {left}d left"), "live"
    days = (s - today).days
    if days == 0:
        return "Opens today", "live"
    if days == 1:
        return "Opens tomorrow", "soon"
    return f"In {days} days", ("soon" if days <= 14 else "later")


def render() -> Path:
    today = date.today()
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Display order: what is open or coming, soonest first; then undated sales;
    # then anything already closed. Sorting purely by date put finished auctions
    # at the top of a page whose entire job is "what is coming up".
    def _order(r):
        _, urgency = _countdown(r["start_date"], r["end_date"], today)
        rank = {"live": 0, "soon": 0, "later": 0, "tbd": 1, "closed": 2}[urgency]
        return (rank, r["start_date"] or "9999", r["title"])

    rows.sort(key=_order)
    upcoming = sum(
        1 for r in rows
        if _countdown(r["start_date"], r["end_date"], today)[1] != "closed")

    all_categories: list[str] = []
    for r in rows:
        for c in (r["categories"] or "").split("|"):
            if c and c not in all_categories:
                all_categories.append(c)
    all_categories.sort(key=lambda c: list(CATEGORY_LABELS).index(c)
                        if c in CATEGORY_LABELS else 99)

    cards: list[str] = []
    for r in rows:
        cats = [c for c in (r["categories"] or "").split("|") if c]
        text, urgency = _countdown(r["start_date"], r["end_date"], today)
        img = html.escape(r["image_url"] or "", quote=True) or PLACEHOLDER_IMG
        title = html.escape(r["title"])
        url = html.escape(r["catalog_url"] or r["detail_url"] or "#", quote=True)
        blob = html.escape(" ".join([
            r["title"], r["description"] or "", r["location"] or "",
            r["operator"] or "", r["categories"] or "",
        ]).lower(), quote=True)

        chips = "".join(
            f'<span class="cat cat-{html.escape(c)}">'
            f'{html.escape(CATEGORY_LABELS.get(c, c))}</span>' for c in cats)
        meta = []
        if r["operator"]:
            meta.append(f'<span>{html.escape(r["operator"])}</span>')
        if r["location"]:
            meta.append(f'<span>{html.escape(r["location"])}</span>')
        desc = html.escape((r["description"] or "")[:180])
        has_cat = "1" if r["catalog_url"] else "0"
        is_closed = "1" if urgency == "closed" else "0"
        is_new = "1" if r["first_seen"] == today.isoformat() else "0"

        cards.append(f'''<a class="card" href="{url}" target="_blank" rel="noopener"
   data-cats="{html.escape('|'.join(cats), quote=True)}" data-search="{blob}"
   data-start="{html.escape(r['start_date'], quote=True)}" data-catalog="{has_cat}"
   data-status="{html.escape(r['status'], quote=True)}" data-new="{is_new}"
   data-closed="{is_closed}">
  <div class="thumb"><img loading="lazy" src="{img}" alt=""
       onerror="this.src='{PLACEHOLDER_IMG}'">
    {'<span class="badge-new">NEW</span>' if is_new == "1" else ''}</div>
  <div class="body">
    <div class="when when-{urgency}">{html.escape(text)}</div>
    <h2>{title}</h2>
    <div class="dates">{html.escape(_fmt_range(r["start_date"], r["end_date"]))}</div>
    <div class="cats">{chips}</div>
    {f'<p class="desc">{desc}…</p>' if desc else ''}
    <div class="meta">{" · ".join(meta)}</div>
    <div class="cta">{"View catalog →" if r["catalog_url"] else "Details not published yet"}</div>
  </div>
</a>''')

    # ---- individual lots ----------------------------------------------------
    lot_rows: list[dict] = []
    if LOTS_PATH.exists():
        with LOTS_PATH.open(newline="", encoding="utf-8") as f:
            lot_rows = list(csv.DictReader(f))
    lot_rows.sort(key=lambda r: (r["end_date"] or "9999", r["title"]))

    lot_cards: list[str] = []
    for r in lot_rows:
        cat = r["category"] or "general"
        if cat not in all_categories:
            all_categories.append(cat)
        end = r["end_date"]
        text, urgency = (_countdown(end, end, today) if end
                         else ("Closing date unknown", "tbd"))
        price = r["current_bid"] or r["min_bid"] or ""
        img = html.escape(r["image_url"] or "", quote=True) or PLACEHOLDER_IMG
        blob = html.escape(" ".join([r["title"], r["sale_title"] or "",
                                     r["location"] or ""]).lower(), quote=True)
        bits = []
        if r["location"]:
            bits.append(html.escape(r["location"]))
        if r["lot_number"]:
            bits.append("Lot " + html.escape(r["lot_number"]))
        lot_cards.append(f'''<a class="card lot" href="{html.escape(r["url"], quote=True)}"
   target="_blank" rel="noopener" data-cats="{html.escape(cat, quote=True)}"
   data-search="{blob}" data-closed="0" data-catalog="1">
  <div class="thumb"><img loading="lazy" src="{img}" alt=""
       onerror="this.src='{PLACEHOLDER_IMG}'"></div>
  <div class="body">
    <div class="when when-{urgency}">{html.escape(text)}</div>
    <h2>{html.escape(r["title"])}</h2>
    {f'<div class="price">{html.escape(price)}</div>' if price else ""}
    <div class="cats"><span class="cat">{html.escape(CATEGORY_LABELS.get(cat, cat))}</span></div>
    <div class="meta">{" · ".join(bits)}</div>
    <div class="cta">{html.escape((r["sale_title"] or "View lot")[:52])} →</div>
  </div>
</a>''')

    chip_html = "".join(
        f'<button class="chip" data-cat="{html.escape(c, quote=True)}">'
        f'{html.escape(CATEGORY_LABELS.get(c, c))}</button>' for c in all_categories)

    doc = _PAGE.format(
        generated=today.isoformat(),
        total=upcoming,
        closed_count=len(rows) - upcoming,
        chips=chip_html,
        cards="\n".join(cards),
        lot_cards="\n".join(lot_cards),
        lot_total=len(lot_rows),
        placeholder=PLACEHOLDER_IMG,
    )
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(doc, encoding="utf-8")
    print(f"Wrote {OUT_PATH}")
    return OUT_PATH


_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>US Government Auctions</title>
<style>
:root {{
  --bg:#f6f7f9; --card:#fff; --fg:#16191d; --muted:#646c77; --border:#dfe3e8;
  --accent:#1f5fa8; --live:#137a4b; --soon:#9a5b00; --later:#646c77; --tbd:#7a4fa8;
  --chip-on:#1f5fa8; --chip-on-fg:#fff;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg:#11141a; --card:#181c23; --fg:#e8eaee; --muted:#98a1ad; --border:#2a3039;
    --accent:#6ba6e8; --live:#4cc98a; --soon:#e0a64a; --later:#98a1ad; --tbd:#b48ce0;
    --chip-on:#6ba6e8; --chip-on-fg:#11141a;
  }}
}}
:root[data-theme="dark"] {{
  --bg:#11141a; --card:#181c23; --fg:#e8eaee; --muted:#98a1ad; --border:#2a3039;
  --accent:#6ba6e8; --live:#4cc98a; --soon:#e0a64a; --later:#98a1ad; --tbd:#b48ce0;
  --chip-on:#6ba6e8; --chip-on-fg:#11141a;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg);
  font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }}
header {{ background:var(--card); border-bottom:1px solid var(--border);
  padding:14px 20px; position:sticky; top:0; z-index:20; }}
.htop {{ display:flex; gap:12px; align-items:baseline; flex-wrap:wrap; }}
h1 {{ margin:0; font-size:18px; font-weight:650; }}
.sub {{ color:var(--muted); font-size:12px; }}
.filters {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; align-items:center; }}
.views {{ display:inline-flex; gap:6px; margin-left:auto; }}
.vbtn {{ border:1px solid var(--border); background:var(--bg); color:var(--fg);
  border-radius:6px; padding:5px 11px; font:inherit; font-size:13px; cursor:pointer; }}
.vbtn.on {{ background:var(--chip-on); color:var(--chip-on-fg); border-color:var(--chip-on); }}
.vbtn b {{ font-weight:700; opacity:.85; }}
.showing {{ max-width:1500px; margin:0 auto 10px; font-size:12.5px; color:var(--muted); }}
.price {{ font-size:14px; font-weight:650; color:var(--accent); }}
.chip {{ border:1px solid var(--border); background:var(--bg); color:var(--fg);
  border-radius:999px; padding:4px 12px; font:inherit; font-size:13px; cursor:pointer; }}
.chip.on {{ background:var(--chip-on); color:var(--chip-on-fg); border-color:var(--chip-on); }}
.chip:focus-visible, .card:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
label.tog {{ font-size:12.5px; color:var(--muted); cursor:pointer; display:inline-flex;
  gap:5px; align-items:center; }}
main {{ padding:18px 20px 60px; }}
/* Author display:grid beats the UA stylesheet's [hidden]{{display:none}}, so the
   hidden grid stayed on screen while its counter showed the other view. */
.grid[hidden] {{ display:none; }}
.grid {{ display:grid; gap:14px;
  grid-template-columns:repeat(auto-fill,minmax(290px,1fr)); max-width:1500px; margin:0 auto; }}
.card {{ background:var(--card); border:1px solid var(--border); border-radius:10px;
  overflow:hidden; text-decoration:none; color:inherit; display:flex; flex-direction:column; }}
.card:hover {{ border-color:var(--accent); }}
.card.hidden {{ display:none; }}
.thumb {{ position:relative; aspect-ratio:16/9; background:var(--bg); overflow:hidden; }}
.thumb img {{ width:100%; height:100%; object-fit:cover; display:block; }}
.badge-new {{ position:absolute; top:8px; left:8px; background:var(--live); color:#fff;
  font-size:10.5px; font-weight:700; letter-spacing:.06em; padding:2px 7px; border-radius:3px; }}
.body {{ padding:12px 14px 14px; display:flex; flex-direction:column; gap:6px; flex:1; }}
.when {{ font-size:11.5px; font-weight:700; letter-spacing:.05em; text-transform:uppercase; }}
.when-live {{ color:var(--live); }} .when-soon {{ color:var(--soon); }}
.when-later {{ color:var(--later); }} .when-tbd {{ color:var(--tbd); }}
.when-closed {{ color:var(--muted); }}
.card h2 {{ margin:0; font-size:15px; font-weight:620; line-height:1.35; }}
.dates {{ font-size:13px; color:var(--muted); font-variant-numeric:tabular-nums; }}
.cats {{ display:flex; gap:5px; flex-wrap:wrap; }}
.cat {{ font-size:11px; border:1px solid var(--border); border-radius:3px;
  padding:1px 6px; color:var(--muted); }}
.desc {{ margin:2px 0 0; font-size:12.5px; color:var(--muted); }}
.meta {{ font-size:12px; color:var(--muted); display:flex; gap:6px; flex-wrap:wrap; }}
.meta span:not(:last-child)::after {{ content:" ·"; }}
.cta {{ margin-top:auto; padding-top:6px; font-size:13px; color:var(--accent); font-weight:550; }}
#empty {{ display:none; text-align:center; color:var(--muted); padding:50px 20px; }}
</style></head><body>
<header>
  <div class="htop">
    <h1>US Government Auctions</h1>
    <span class="sub">Updated {generated} · click any card to open it</span>
    <span class="views">
      <button id="view-sales" class="vbtn on">Auctions <b>{total}</b></button>
      <button id="view-lots" class="vbtn">Items for sale <b>{lot_total}</b></button>
    </span>
  </div>
  <div class="filters">
    {chips}
    <label class="tog"><input type="checkbox" id="cat-only"> Only sales with a published catalog</label>
    <label class="tog"><input type="checkbox" id="show-closed"> Show recently closed ({closed_count})</label>
  </div>
</header>
<main>
  <div class="showing"><span id="count">{total}</span> shown</div>
  <div class="grid" id="grid">
{cards}
  </div>
  <div class="grid" id="lotgrid" hidden>
{lot_cards}
  </div>
  <div id="empty">Nothing matches these filters.</div>
</main>
<script>
(function () {{
  // Scope to the sales grid: lot cards also carry .card, and an unscoped
  // selector made the sales view count every item on the page too.
  var cards = Array.prototype.slice.call(document.querySelectorAll('#grid .card'));
  var chips = Array.prototype.slice.call(document.querySelectorAll('.chip'));
  var catOnly = document.getElementById('cat-only');
  var lotCards = Array.prototype.slice.call(document.querySelectorAll('#lotgrid .card'));
  var grid = document.getElementById('grid');
  var lotGrid = document.getElementById('lotgrid');
  var btnSales = document.getElementById('view-sales');
  var btnLots = document.getElementById('view-lots');
  var view = 'sales';
  var showClosed = document.getElementById('show-closed');
  var countEl = document.getElementById('count');
  var emptyEl = document.getElementById('empty');

  function activeCats() {{
    var on = chips.filter(function (c) {{ return c.classList.contains('on'); }});
    return on.map(function (c) {{ return c.dataset.cat; }});
  }}

  function apply() {{
    var want = activeCats();
    var shown = 0;
    var list = view === 'lots' ? lotCards : cards;
    list.forEach(function (card) {{
      var cats = (card.dataset.cats || '').split('|');
      var show = true;
      // No chip selected means "everything" — an empty selection should not
      // hide the whole page.
      if (want.length) {{
        show = want.some(function (w) {{ return cats.indexOf(w) !== -1; }});
      }}
      if (show && catOnly.checked && card.dataset.catalog !== '1') show = false;
      // A finished sale is not what this page is for; kept one toggle away so
      // "did it actually sell?" is still answerable.
      if (show && !showClosed.checked && card.dataset.closed === '1') show = false;
      card.classList.toggle('hidden', !show);
      if (show) shown++;
    }});
    countEl.textContent = shown;
    emptyEl.style.display = shown ? 'none' : 'block';
  }}

  chips.forEach(function (c) {{
    c.addEventListener('click', function () {{
      c.classList.toggle('on');
      apply();
    }});
  }});
  catOnly.addEventListener('change', apply);
  showClosed.addEventListener('change', apply);

  function setView(v) {{
    view = v;
    var lots = v === 'lots';
    grid.hidden = lots;
    lotGrid.hidden = !lots;
    btnLots.classList.toggle('on', lots);
    btnSales.classList.toggle('on', !lots);
    // These two only mean anything for sales; hide them in the items view
    // rather than leaving dead controls on screen.
    catOnly.parentElement.style.display = lots ? 'none' : '';
    showClosed.parentElement.style.display = lots ? 'none' : '';
    apply();
  }}
  btnSales.addEventListener('click', function () {{ setView('sales'); }});
  btnLots.addEventListener('click', function () {{ setView('lots'); }});

  // Run once at load, not only on interaction.
  apply();
}})();
</script>
</body></html>
"""


if __name__ == "__main__":
    render()
