#!/usr/bin/env python3
"""
dividend_tracker.py

Fetches upcoming NSE corporate actions (dividends) for TODAY and TOMORROW
(IST), enriches each with previous day's closing price and volume, and
writes a static HTML page with interactive tabs (Today / Tomorrow) and
dividend-amount filter buttons (>0, >5, >10, >20).

All matching stocks (any dividend amount > 0) are fetched and embedded in
the page; filtering by amount happens in the browser via JavaScript, so no
re-run is needed to see different thresholds.

This uses NSE India's public website JSON endpoints (the same ones the
nseindia.com site itself uses). These are not an official/published API,
so NSE could change them at any time. This script is intended for
personal, low-frequency use (once a day) only.

Run:
    python3 dividend_tracker.py

Output:
    docs/index.html   (open this file in a browser, or publish via GitHub Pages)
    data/latest.json  (raw data, for debugging / reuse)
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MIN_DIVIDEND_TO_FETCH = 0.01   # fetch anything with a real dividend amount;
                               # the four filter buttons on the page do the
                               # actual >0 / >5 / >10 / >20 filtering live
OUTPUT_DIR = "docs"
DATA_DIR = "data"
TIMEOUT = 15

NSE_BASE = "https://www.nseindia.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-actions",
}


class NseSession:
    """Handles the cookie handshake NSE requires before its API endpoints
    will respond (visiting the HTML site first establishes session cookies;
    calling the API directly without them returns 403). Uses httpx with
    HTTP/2 enabled, since NSE's server-environment IP blocking has been
    documented to behave differently with HTTP/2 vs plain HTTP/1.1
    clients (e.g. the 'requests' library, which is HTTP/1.1 only)."""

    def __init__(self):
        self.client = httpx.Client(
            headers=HEADERS,
            http2=True,
            timeout=TIMEOUT,
            follow_redirects=True,
        )
        self._warm_up()

    def _warm_up(self):
        # A normal browser hits the homepage (and usually the relevant
        # section page) before any API call. We do the same.
        self.client.get(NSE_BASE)
        time.sleep(1)
        self.client.get(f"{NSE_BASE}/companies-listing/corporate-filings-actions")
        time.sleep(1)

    def get_json(self, path, params=None, retries=1):
        url = f"{NSE_BASE}{path}"
        last_err = None
        for attempt in range(retries + 1):
            try:
                resp = self.client.get(url, params=params)
                if resp.status_code == 200:
                    return resp.json()
                # A clean HTTP error response (403/404/etc) means we did
                # reach NSE and it said no -- retrying immediately with the
                # same session is unlikely to help, so don't burn time
                # re-warming up; just fail this one call.
                last_err = f"HTTP {resp.status_code} for {url}"
                break
            except Exception as e:  # noqa: BLE001
                # A connection-level error (timeout, reset, etc) might
                # genuinely be fixed by a fresh session, so it's worth one
                # retry with re-warm-up here -- but only for this case.
                last_err = str(e)
                if attempt < retries:
                    time.sleep(2)
                    self._warm_up()
        raise RuntimeError(f"Failed to fetch {url}: {last_err}")


def fetch_corporate_actions(nse: NseSession):
    """Returns the list of forthcoming corporate actions for equities."""
    data = nse.get_json(
        "/api/corporates-corporateActions",
        params={"index": "equities"},
    )
    # The endpoint returns a flat list of dicts with keys like:
    # 'symbol', 'subject', 'exDate', 'recDate', 'series', 'faceVal', ...
    # Dividend amount itself is usually embedded inside 'subject' text,
    # e.g. "Dividend - Rs 7.50 Per Share" -- it is not always a clean field.
    if isinstance(data, dict):
        data = data.get("data", [])
    return data or []


def fetch_quote(nse: NseSession, symbol: str):
    """Returns last traded price and previous day's traded volume for a
    symbol, from NSE's GetQuoteApi (NextApi) endpoint -- confirmed working
    via direct inspection of the live site's network requests. Note: this
    endpoint's response does not include a clean 'previous close' or
    'change %' field (checked directly), so those are not returned here."""
    try:
        data = nse.get_json(
            "/api/NextApi/apiClient/GetQuoteApi",
            params={
                "functionName": "getSymbolData",
                "marketType": "N",
                "series": "EQ",
                "symbol": symbol,
            },
        )
        rows = data.get("equityResponse", [])
        if not rows:
            return {}
        trade_info = rows[0].get("tradeInfo", {}) or {}

        return {
            "lastPrice": trade_info.get("lastPrice"),
            "previousDayVolume": trade_info.get("totalTradedVolume"),
        }
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] quote fetch failed for {symbol}: {e}", file=sys.stderr)
        return {}


def parse_dividend_amount(subject: str):
    """Extracts a rupee amount from the free-text 'subject' field NSE
    provides, e.g. 'Annual General Meeting/Dividend - Rs 7.50 Per Share'.
    NSE varies the wording for small amounts -- 'Re' instead of 'Rs' for
    sub-rupee values, and 'Per Sh' as a shortened form of 'Per Share' --
    so both are matched here. Returns None if no dividend amount could be
    parsed."""
    import re

    if not subject:
        return None
    if "dividend" not in subject.lower():
        return None

    match = re.search(
        r"R[se]\.?\s*([\d,]+\.?\d*)\s*Per\s*Sh(?:are)?",
        subject,
        re.IGNORECASE,
    )
    if match:
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def parse_nse_date(date_str: str):
    """NSE date strings are typically 'DD-Mon-YYYY', e.g. '25-Jun-2026'."""
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def build_dataset(min_dividend: float):
    today = datetime.now(IST).date()
    target_dates = [today, today + timedelta(days=1), today + timedelta(days=2)]
    start_time = time.monotonic()
    MAX_SECONDS = 180  # hard ceiling on enrichment phase; safety net so a
                        # run-away condition can't silently eat the whole job

    print("Connecting to NSE...")
    nse = NseSession()

    print("Fetching corporate actions...")
    actions = fetch_corporate_actions(nse)
    print(f"  {len(actions)} total corporate actions returned")

    quote_cache = {}  # symbol -> enrichment dict, avoids re-fetching same stock twice

    def enrich(symbol):
        if symbol not in quote_cache:
            if time.monotonic() - start_time > MAX_SECONDS:
                print(f"  [warn] time budget exceeded, skipping enrichment for {symbol}", file=sys.stderr)
                quote_cache[symbol] = {"lastPrice": None, "previousDayVolume": None}
                return quote_cache[symbol]
            quote = fetch_quote(nse, symbol)
            time.sleep(0.4)
            quote_cache[symbol] = {
                "lastPrice": quote.get("lastPrice"),
                "previousDayVolume": quote.get("previousDayVolume"),
            }
        return quote_cache[symbol]

    # results_by_date[date] -> list of matching stock records
    results_by_date = {d: [] for d in target_dates}

    for action in actions:
        subject = action.get("subject", "")
        amount = parse_dividend_amount(subject)
        if amount is None or amount < min_dividend:
            continue

        # NSE's corporate-actions feed gives 'exDate' (ex-dividend date) and
        # 'recDate' (record date), but no separate cash "pay date" field.
        # Ex-date is the practically meaningful date for tracking purposes
        # (it's when the stock starts trading without the dividend value
        # attached), so that's what each tab date refers to here.
        ex_date = parse_nse_date(action.get("exDate", ""))
        if ex_date not in results_by_date:
            continue

        symbol = action.get("symbol")
        company = action.get("comp") or action.get("companyName") or symbol

        print(f"  Match: {symbol} - Rs {amount} - ex-date {ex_date}")
        enrichment = enrich(symbol)

        record = {
            "symbol": symbol,
            "company": company,
            "dividend": amount,
            "exDate": ex_date.isoformat() if ex_date else None,
            "subject": subject,
            **enrichment,
        }

        results_by_date[ex_date].append(record)

    for d in target_dates:
        results_by_date[d].sort(key=lambda r: r["dividend"], reverse=True)

    day_keys = ["today", "tomorrow", "dayAfter"]
    return {
        "generatedAt": datetime.now(IST).strftime("%d-%b-%Y %I:%M %p IST"),
        **{
            key: {"date": d.isoformat(), "stocks": results_by_date[d]}
            for key, d in zip(day_keys, target_dates)
        },
    }


def tradingview_link(symbol: str) -> str:
    return f"https://www.tradingview.com/chart/?symbol=NSE:{symbol}"



def render_html(dataset: dict) -> str:
    generated_at = dataset["generatedAt"]
    day_keys = ["today", "tomorrow", "dayAfter"]

    # Pre-compute TradingView links and embed everything as JSON for the
    # browser to render/filter client-side (no page regeneration needed
    # just to change the dividend threshold or switch days).
    payload = {
        "generatedAt": generated_at,
        **{
            key: {
                "date": dataset[key]["date"],
                "stocks": [
                    {**s, "chartUrl": tradingview_link(s["symbol"])}
                    for s in dataset[key]["stocks"]
                ],
            }
            for key in day_keys
        },
    }
    payload_json = json.dumps(payload)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dividend Tracker — NSE</title>
<style>
  :root{{
    --bg:#0b0d10; --panel:#11151a; --border:#1f2730;
    --ink:#e8edf2; --dim:#8b97a3; --faint:#5c6773;
    --accent:#ffb454; --green:#3ddc84; --red:#ff5c5c;
  }}
  *{{box-sizing:border-box;}}
  body{{margin:0;background:var(--bg);color:var(--ink);
       font-family:-apple-system,Segoe UI,sans-serif;padding:18px;
       max-width:980px;margin:0 auto;}}
  h1{{font-size:18px;margin:0 0 4px;}}
  .meta{{color:var(--dim);font-size:12px;margin-bottom:18px;}}

  .tabs{{display:flex;gap:8px;margin-bottom:14px;}}
  .tab{{flex:1;text-align:center;padding:10px 8px;border-radius:8px;
        background:var(--panel);border:1px solid var(--border);
        color:var(--dim);font-size:13.5px;font-weight:600;cursor:pointer;
        font-family:monospace;}}
  .tab.active{{background:var(--accent);color:#1a1206;border-color:var(--accent);}}
  .tab .count{{display:block;font-size:10.5px;font-weight:400;margin-top:2px;
               opacity:.85;}}

  .filter-row{{display:flex;gap:7px;margin-bottom:16px;flex-wrap:wrap;}}
  .filter-chip{{font-family:monospace;font-size:11.5px;color:var(--dim);
      background:var(--panel);border:1px solid var(--border);
      padding:6px 13px;border-radius:20px;cursor:pointer;}}
  .filter-chip.active{{background:var(--accent);color:#1a1206;
      border-color:var(--accent);font-weight:600;}}

  table{{width:100%;border-collapse:collapse;font-size:13.5px;}}
  th{{text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;
      color:var(--dim);padding:8px 6px;border-bottom:1px solid var(--border);
      white-space:nowrap;}}
  td{{padding:10px 6px;border-bottom:1px solid #161b21;font-family:monospace;}}
  td.name{{font-family:-apple-system,sans-serif;font-weight:600;}}
  td.name .tk{{display:block;font-family:monospace;color:var(--dim);font-size:11px;
               font-weight:400;}}
  td.empty{{text-align:center;color:var(--dim);padding:40px 0;
            font-family:-apple-system,sans-serif;}}
  td.up{{color:var(--green);}}
  td.down{{color:var(--red);}}
  .chart-link{{color:var(--accent);text-decoration:none;
      border:1px solid rgba(255,180,84,.3);padding:4px 8px;border-radius:5px;
      font-size:11.5px;white-space:nowrap;}}
  .div-amt{{color:var(--green);font-weight:700;}}

  footer{{margin-top:30px;font-size:11px;color:var(--faint);line-height:1.6;}}

  @media (max-width:640px){{
    table{{display:block;overflow-x:auto;white-space:nowrap;}}
    .tab{{font-size:12px;}}
  }}
</style>
</head>
<body>
  <h1>Dividend Tracker — NSE</h1>
  <div class="meta">Generated {generated_at} · ex-date based · dividend amount parsed from NSE filing text</div>

  <div class="tabs" id="tabs"></div>
  <div class="filter-row" id="filters"></div>
  <table>
    <thead>
      <tr>
        <th>Stock</th><th>Dividend</th><th>Ex-date</th>
        <th>Prev. day volume</th><th>Last price</th><th>Chart</th>
      </tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>

  <footer>
    Data source: NSE corporate-actions filings (unofficial endpoint) ·
    "Ex-date" is shown, not the cash payout date — NSE's feed doesn't
    publish a separate pay date · Dividend amount is parsed from each
    filing's free-text subject line and may occasionally fail to parse for
    unusually worded filings.
  </footer>

<script>
const DATA = {payload_json};

const DAY_LABELS = {{today: 'Today', tomorrow: 'Tomorrow', dayAfter: 'Day After'}};

let activeDay = DATA.today.stocks.length > 0 ? 'today'
  : (DATA.tomorrow.stocks.length > 0 ? 'tomorrow' : 'dayAfter');
let activeMin = 5;

const dayLabel = (iso) => {{
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-IN', {{weekday:'short', day:'2-digit', month:'short'}});
}};

function renderTabs(){{
  const tabs = document.getElementById('tabs');
  tabs.innerHTML = Object.keys(DAY_LABELS).map(day => {{
    const d = DATA[day];
    const label = DAY_LABELS[day];
    return `<div class="tab ${{day===activeDay?'active':''}}" data-day="${{day}}">
      ${{label}} — ${{dayLabel(d.date)}}
      <span class="count">${{d.stocks.length}} dividend${{d.stocks.length===1?'':'s'}} announced</span>
    </div>`;
  }}).join('');
  tabs.querySelectorAll('.tab').forEach(t => {{
    t.addEventListener('click', () => {{ activeDay = t.dataset.day; renderAll(); }});
  }});
}}

function renderFilters(){{
  const filters = document.getElementById('filters');
  const opts = [[0,'All (>₹0)'],[5,'≥ ₹5'],[10,'≥ ₹10'],[20,'≥ ₹20']];
  filters.innerHTML = opts.map(([min,label]) =>
    `<div class="filter-chip ${{min===activeMin?'active':''}}" data-min="${{min}}">${{label}}</div>`
  ).join('');
  filters.querySelectorAll('.filter-chip').forEach(c => {{
    c.addEventListener('click', () => {{ activeMin = Number(c.dataset.min); renderAll(); }});
  }});
}}

function fmtVol(v){{
  if (v === null || v === undefined) return '—';
  if (v >= 1e7) return (v/1e7).toFixed(2) + ' Cr';
  if (v >= 1e5) return (v/1e5).toFixed(2) + ' L';
  if (v >= 1e3) return (v/1e3).toFixed(1) + 'K';
  return String(v);
}}

function renderRows(){{
  const stocks = DATA[activeDay].stocks.filter(s => s.dividend >= activeMin);
  const tbody = document.getElementById('rows');

  if (stocks.length === 0){{
    tbody.innerHTML = `<tr><td colspan="6" class="empty">No stocks at this threshold for ${{DAY_LABELS[activeDay].toLowerCase()}}.</td></tr>`;
    return;
  }}

  tbody.innerHTML = stocks.map(s => {{
    const price = (s.lastPrice !== null && s.lastPrice !== undefined)
      ? '₹' + Number(s.lastPrice).toLocaleString('en-IN', {{minimumFractionDigits:2}})
      : '—';
    return `
    <tr>
      <td class="name">${{s.company}}<span class="tk">${{s.symbol}}</span></td>
      <td class="div-amt">₹${{Number(s.dividend).toFixed(2)}}</td>
      <td>${{s.exDate || '—'}}</td>
      <td>${{fmtVol(s.previousDayVolume)}}</td>
      <td>${{price}}</td>
      <td><a class="chart-link" href="${{s.chartUrl}}" target="_blank" rel="noopener">Chart →</a></td>
    </tr>`;
  }}).join('');
}}

function renderAll(){{
  renderTabs();
  renderFilters();
  renderRows();
}}

renderAll();
</script>
</body>
</html>"""


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

    dataset = build_dataset(MIN_DIVIDEND_TO_FETCH)

    json_path = os.path.join(DATA_DIR, "latest.json")
    with open(json_path, "w") as f:
        json.dump(dataset, f, indent=2)
    print(f"Wrote {json_path}")

    html_path = os.path.join(OUTPUT_DIR, "index.html")
    with open(html_path, "w") as f:
        f.write(render_html(dataset))
    print(f"Wrote {html_path}")

    print()
    for key, label in [("today", "Today"), ("tomorrow", "Tomorrow"), ("dayAfter", "Day after tomorrow")]:
        count = len(dataset[key]["stocks"])
        print(f"{label} ({dataset[key]['date']}): {count} dividend-paying stock(s)")


if __name__ == "__main__":
    main()
