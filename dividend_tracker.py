#!/usr/bin/env python3
"""
dividend_tracker.py

Fetches upcoming NSE corporate actions (dividends), filters for stocks
paying a dividend >= MIN_DIVIDEND tomorrow, enriches each with previous
day's closing price and volume, and writes a static HTML page.

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
from datetime import datetime, timedelta

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MIN_DIVIDEND = 5.0          # rupees per share
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
    calling the API directly without them returns 403)."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._warm_up()

    def _warm_up(self):
        # A normal browser hits the homepage (and usually the relevant
        # section page) before any API call. We do the same.
        self.session.get(NSE_BASE, timeout=TIMEOUT)
        time.sleep(1)
        self.session.get(
            f"{NSE_BASE}/companies-listing/corporate-filings-actions",
            timeout=TIMEOUT,
        )
        time.sleep(1)

    def get_json(self, path, params=None, retries=2):
        url = f"{NSE_BASE}{path}"
        last_err = None
        for attempt in range(retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=TIMEOUT)
                if resp.status_code == 200:
                    return resp.json()
                last_err = f"HTTP {resp.status_code} for {url}"
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
            # session may have gone stale; re-warm and retry once
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
    """Returns live quote info for a symbol, including previous close and
    previous day's traded volume."""
    try:
        data = nse.get_json(
            "/api/quote-equity",
            params={"symbol": symbol},
        )
        price_info = data.get("priceInfo", {})
        trade_info = data.get("preOpenMarket", {}) or {}
        return {
            "lastPrice": price_info.get("lastPrice"),
            "previousClose": price_info.get("previousClose"),
            "change": price_info.get("change"),
            "pChange": price_info.get("pChange"),
        }
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] quote fetch failed for {symbol}: {e}", file=sys.stderr)
        return {}


def fetch_volume(nse: NseSession, symbol: str):
    """Returns previous trading day's volume from the trade info endpoint."""
    try:
        data = nse.get_json(
            "/api/quote-equity",
            params={"symbol": symbol, "section": "trade_info"},
        )
        market_deets = data.get("marketDeptOrderBook", {})
        trade_info = market_deets.get("tradeInfo", {})
        return trade_info.get("totalTradedVolume")
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] volume fetch failed for {symbol}: {e}", file=sys.stderr)
        return None


def parse_dividend_amount(subject: str):
    """Extracts a rupee amount from the free-text 'subject' field NSE
    provides, e.g. 'Annual General Meeting/Dividend - Rs 7.50 Per Share'.
    Returns None if no dividend amount could be parsed."""
    import re

    if not subject:
        return None
    if "dividend" not in subject.lower():
        return None

    match = re.search(r"Rs\.?\s*([\d,]+\.?\d*)\s*Per\s*Share", subject, re.IGNORECASE)
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
    today = datetime.now().date()
    tomorrow = today + timedelta(days=1)

    print("Connecting to NSE...")
    nse = NseSession()

    print("Fetching corporate actions...")
    actions = fetch_corporate_actions(nse)
    print(f"  {len(actions)} total corporate actions returned")

    results = []
    for action in actions:
        subject = action.get("subject", "")
        amount = parse_dividend_amount(subject)
        if amount is None or amount < min_dividend:
            continue

        # NSE's corporate-actions feed mixes ex-date and other dates
        # depending on action type; for dividends, 'exDate' is the date
        # the stock trades ex-dividend, and the actual cash payout date
        # is usually some days later and not always provided in this feed.
        # We treat exDate as the relevant trigger date here; you may need
        # to adjust this once you see real output, since "exDate" and
        # "pay date" are not the same thing for dividends.
        ex_date = parse_nse_date(action.get("exDate", ""))
        if ex_date != tomorrow:
            continue

        symbol = action.get("symbol")
        company = action.get("comp") or action.get("companyName") or symbol

        print(f"  Match: {symbol} - Rs {amount} - ex-date {ex_date}")

        quote = fetch_quote(nse, symbol)
        time.sleep(0.5)
        volume = fetch_volume(nse, symbol)
        time.sleep(0.5)

        results.append({
            "symbol": symbol,
            "company": company,
            "dividend": amount,
            "exDate": ex_date.isoformat() if ex_date else None,
            "subject": subject,
            "previousClose": quote.get("previousClose"),
            "lastPrice": quote.get("lastPrice"),
            "pChange": quote.get("pChange"),
            "previousDayVolume": volume,
        })

    return {
        "generatedAt": datetime.now().isoformat(),
        "asOfDate": today.isoformat(),
        "targetDate": tomorrow.isoformat(),
        "minDividend": min_dividend,
        "stocks": sorted(results, key=lambda r: r["dividend"], reverse=True),
    }


def tradingview_link(symbol: str) -> str:
    return f"https://www.tradingview.com/chart/?symbol=NSE:{symbol}"


def render_html(dataset: dict) -> str:
    stocks = dataset["stocks"]
    target_date = dataset["targetDate"]
    generated_at = dataset["generatedAt"]
    min_div = dataset["minDividend"]

    if not stocks:
        rows_html = (
            '<tr><td colspan="6" class="empty">'
            "No stocks found paying a dividend of this size on this date."
            "</td></tr>"
        )
    else:
        rows = []
        for s in stocks:
            vol = s["previousDayVolume"]
            vol_display = f"{vol:,}" if isinstance(vol, (int, float)) else "—"
            price = s["lastPrice"]
            price_display = f"₹{price:,.2f}" if isinstance(price, (int, float)) else "—"
            rows.append(f"""
            <tr>
              <td class="name">{s['company']}<span class="tk">{s['symbol']}</span></td>
              <td>₹{s['dividend']:.2f}</td>
              <td>{s['exDate'] or '—'}</td>
              <td>{vol_display}</td>
              <td>{price_display}</td>
              <td><a class="chart-link" href="{tradingview_link(s['symbol'])}" target="_blank" rel="noopener">Chart →</a></td>
            </tr>""")
        rows_html = "".join(rows)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ex-Div Tomorrow</title>
<style>
  :root{{
    --bg:#0b0d10; --panel:#11151a; --border:#1f2730;
    --ink:#e8edf2; --dim:#8b97a3; --accent:#ffb454; --green:#3ddc84;
  }}
  *{{box-sizing:border-box;}}
  body{{margin:0;background:var(--bg);color:var(--ink);
       font-family:-apple-system,Segoe UI,sans-serif;padding:20px;}}
  h1{{font-size:18px;margin:0 0 4px;}}
  .meta{{color:var(--dim);font-size:12.5px;margin-bottom:18px;}}
  table{{width:100%;border-collapse:collapse;font-size:13.5px;}}
  th{{text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;
      color:var(--dim);padding:8px 6px;border-bottom:1px solid var(--border);}}
  td{{padding:10px 6px;border-bottom:1px solid #161b21;font-family:monospace;}}
  td.name{{font-family:-apple-system,sans-serif;font-weight:600;}}
  td.name .tk{{display:block;font-family:monospace;color:var(--dim);font-size:11px;}}
  td.empty{{text-align:center;color:var(--dim);padding:40px 0;font-family:-apple-system,sans-serif;}}
  .chart-link{{color:var(--accent);text-decoration:none;border:1px solid rgba(255,180,84,.3);
      padding:4px 8px;border-radius:5px;font-size:11.5px;}}
  @media (max-width:600px){{ table{{display:block;overflow-x:auto;white-space:nowrap;}} }}
</style>
</head>
<body>
  <h1>Stocks paying ≥ ₹{min_div:.0f} dividend on {target_date}</h1>
  <div class="meta">Generated {generated_at} · NSE equities only</div>
  <table>
    <thead>
      <tr><th>Stock</th><th>Dividend</th><th>Ex-date</th><th>Prev. day volume</th><th>Last price</th><th>Chart</th></tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>
</body>
</html>"""


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

    dataset = build_dataset(MIN_DIVIDEND)

    json_path = os.path.join(DATA_DIR, "latest.json")
    with open(json_path, "w") as f:
        json.dump(dataset, f, indent=2)
    print(f"Wrote {json_path}")

    html_path = os.path.join(OUTPUT_DIR, "index.html")
    with open(html_path, "w") as f:
        f.write(render_html(dataset))
    print(f"Wrote {html_path}")

    print(f"\nFound {len(dataset['stocks'])} stock(s) paying >= Rs{MIN_DIVIDEND} on {dataset['targetDate']}")


if __name__ == "__main__":
    main()
