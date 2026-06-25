# Dividend Tracker (NSE, personal use)

Finds NSE-listed stocks paying a dividend of ₹5 or more, on a given date,
along with previous day's volume and price, and publishes a simple webpage
you can open on your phone.

**Important caveat:** this uses NSE's website JSON endpoints, which are not
an official published API. They can change or block access at any time.
This is built for personal, once-a-day use only — not for heavy or
commercial use.

## How it works

1. `dividend_tracker.py` connects to nseindia.com, pulls the corporate
   actions list, filters for dividends ≥ ₹5 paying tomorrow, fetches each
   stock's previous close / volume, and writes `docs/index.html`.
2. A GitHub Actions workflow (`.github/workflows/daily-update.yml`) runs
   this script automatically once a day and commits the result.
3. GitHub Pages serves `docs/index.html` as a normal webpage — this is the
   link you open on your phone.

## One-time setup (do this from a laptop/desktop browser, ~10 minutes)

1. **Create a free GitHub account** at github.com if you don't have one.
2. **Create a new repository**:
   - Click "New repository"
   - Name it anything, e.g. `dividend-tracker`
   - Set it to **Public** (required for free Actions minutes; the code
     itself contains no personal data, just the script)
   - Click "Create repository"
3. **Upload these files** to the repo. The `.github` folder (which holds
   the automation) sometimes doesn't survive drag-and-drop uploads well, so
   do it in two steps:
   - On the repo page, click "Add file" → "Upload files", then drag in
     `dividend_tracker.py`, `README.md`, and the `docs` folder (with
     `index.html` inside it). Commit to `main`.
   - Then, still on GitHub, click "Add file" → "Create new file". In the
     filename box type exactly: `.github/workflows/daily-update.yml`
     (GitHub will auto-create the folders). Open `daily-update.yml` from
     the zip on your computer in any text editor, copy all its contents,
     and paste into GitHub's editor box. Commit directly to `main`.
4. **Enable GitHub Pages**:
   - Go to repo **Settings** → **Pages** (left sidebar)
   - Under "Build and deployment" → "Source", choose **Deploy from a branch**
   - Branch: `main`, folder: `/docs` → Save
   - GitHub will show you a URL like `https://yourusername.github.io/dividend-tracker/`
   - **This is the link you'll open on your phone.** Bookmark it / add to
     home screen.
5. **Run it once manually to test**:
   - Go to the **Actions** tab → click "Update dividend tracker" workflow
     → click "Run workflow" → "Run workflow" (green button)
   - Wait ~30-60 seconds, refresh the Actions tab, check it finished with a
     green checkmark (not red ✗)
   - If it succeeded, open your Pages URL — it should show today's data
   - If it failed, click into the failed run to see the error log (NSE
     may have blocked the request — see Troubleshooting below)

After this, it runs automatically every day at the scheduled time (default:
5:00 AM IST) with no further action from you.

## Opening it on your phone

Just open the GitHub Pages URL in any mobile browser — Chrome, etc.
- Tap the browser menu → "Add to Home screen" to make it feel like an app.
- No installation, no background process, no battery drain. It's a static
  webpage that updates once a day behind the scenes.

## Changing settings

- **Minimum dividend amount**: edit `MIN_DIVIDEND = 5.0` in
  `dividend_tracker.py`.
- **Time of day it runs**: edit the `cron:` line in
  `.github/workflows/daily-update.yml`. GitHub Actions cron is always in
  UTC — IST is UTC+5:30, so for example 5:00 AM IST = 23:30 UTC the
  previous day, written as `cron: "30 23 * * *"`.

## Troubleshooting

- **Workflow fails with a 403 / connection error**: NSE is blocking the
  request. This can happen if NSE changes its bot-detection. Try re-running
  manually a few minutes later; if it persists consistently, the script's
  headers/cookie handling may need updating.
- **Page shows "No stocks found"**: this can be correct (no big dividends
  ex-dividend tomorrow) — cross-check manually on NSE's corporate actions
  page or Screener before assuming it's broken.
- **Workflow stopped running on its own**: GitHub disables scheduled
  workflows after 60 days with no repository activity. Just go to the
  Actions tab and click "Run workflow" once to re-enable it, or make any
  small commit.
- **Important data caveat**: this script currently treats "ex-date" as the
  trigger date. For dividends, the ex-date and the actual cash pay date are
  usually *different* (pay date is typically some days after ex-date). NSE's
  corporate-actions feed doesn't always include a clean separate "pay date"
  field — once you see real output, you may want to adjust
  `dividend_tracker.py` to match what you actually want to track (ex-date
  vs. pay date).
