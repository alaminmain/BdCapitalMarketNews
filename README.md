# BD Markets Daily Pulse

Automated news aggregator and AI analyzer for the **Bangladesh Capital
(DSE) and Money Markets**. Scrapes daily corporate disclosures and broader
financial headlines, asks Gemini to label them as **Good / Bad / Ugly** for
market liquidity and investor sentiment, stores everything in SQLite, and
publishes a public RSS feed via GitHub Pages.

```
 sources                pipeline                      output
 ────────              ──────────                    ─────────
 DSE latest news ─┐                                ┌─ market_updates.db
 DSE recent info  ├─► scrape ─► Gemini ─► SQLite ──┤
 FE economy/stock ┘                                └─ docs/feed.xml ─► CDN
```

## Project layout

```
NewsScrapper/
├── main.py                    # entry point — runs the full pipeline
├── requirements.txt
├── src/
│   ├── config.py              # env-driven settings
│   ├── scraper.py             # DSE + Financial Express
│   ├── analyzer.py            # Gemini classification
│   ├── database.py            # SQLite persistence
│   └── rss_generator.py       # RSS 2.0 feed
├── docs/
│   ├── index.html             # GitHub Pages landing page
│   └── feed.xml               # generated each run
├── .github/workflows/
│   ├── daily-scrape.yml       # cron: scrape + commit
│   └── pages.yml              # publish docs/ to GitHub Pages
└── market_updates.db          # SQLite history (committed)
```

## Local quickstart

```bash
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Get a key from https://aistudio.google.com/app/apikey
export GEMINI_API_KEY=...

python main.py --verbose
```

Outputs:
- `market_updates.db` — append-only history (deduped on date+summary hash).
- `docs/feed.xml` — standards-compliant RSS 2.0 feed of the latest 50 items.

Use `python main.py --dry-run` to scrape and refresh the feed from the
existing database without spending Gemini quota.

## How the AI persona works

`src/analyzer.py` uses this exact persona as the Gemini system instruction:

> *You are a senior financial analyst specializing in the Bangladesh
> Capital (DSE) and Money Markets. Categorize the most significant
> developments as Good (e.g., high dividends, structural reforms), Bad
> (e.g., inflation, index drops), or Ugly (e.g., systemic bank failure,
> energy shocks). For each item, provide a concise Headline Summary and
> a one-sentence Reason. Output the result as a JSON-formatted list of
> objects with the keys: category, summary, and reason.*

Responses are forced into JSON (`response_mime_type=application/json`) and
parsed defensively — fenced code blocks, leading/trailing prose, and
unknown categories are all tolerated.

## Database schema

```sql
CREATE TABLE market_updates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT    NOT NULL,         -- YYYY-MM-DD (UTC)
    category   TEXT    NOT NULL CHECK (category IN ('Good','Bad','Ugly')),
    summary    TEXT    NOT NULL,         -- written by Gemini, original wording
    reason     TEXT    NOT NULL,         -- one-sentence rationale
    hash       TEXT    NOT NULL UNIQUE,  -- sha256(date|lower(summary))
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

The `hash` column makes daily runs idempotent: re-running the script
multiple times in one day will not duplicate rows.

---

# Implementation plan: free hosting & automation

## 1. Hosting — GitHub Pages (free global CDN)

1. Push this repo to GitHub.
2. **Settings → Pages**:
   - **Source:** *GitHub Actions* (the included `pages.yml` workflow
     publishes the `docs/` folder).
   - Alternative: *Deploy from branch → `main` → `/docs`* — also works,
     but the Actions deployment gives proper cache headers and a build URL.
3. After the first run, your feed is served from:

   ```
   https://alaminmain.github.io/BdCapitalMarketNews/feed.xml
   ```

4. Set this URL as a repository **variable** named `FEED_PUBLIC_URL`
   (Settings → Secrets and variables → Actions → Variables) so the RSS
   `<link rel="self">` element points to the canonical location.

GitHub Pages already fronts content with Fastly's edge cache, so you get a
global CDN at no cost.

## 2. Automation — GitHub Actions (daily run + commit)

1. Add a repository **secret** `GEMINI_API_KEY`
   (Settings → Secrets and variables → Actions → Secrets).
2. The bundled `daily-scrape.yml` runs at **03:30 UTC (09:30 Asia/Dhaka)**
   every day, after market opens and morning disclosures are posted.
3. The workflow:
   - installs deps,
   - runs `python main.py --verbose`,
   - commits any changes to `docs/feed.xml` and `market_updates.db`
     back to `main` using the built-in `GITHUB_TOKEN`
     (`permissions: contents: write`).
4. The push to `main` triggers `pages.yml`, which redeploys
   `docs/` to GitHub Pages — your subscribers' RSS readers will pick up
   the change on their next poll.

You can also fire the workflow manually from **Actions →
Daily BD Markets News Run → Run workflow**.

### Cron tweaks

| Goal | Cron |
| --- | --- |
| Twice daily (open + close) | `30 3,11 * * *` |
| Skip weekends (DSE closed Fri/Sat) | `30 3 * * 0-4` |
| Hourly during trading hours | `0 4-10 * * 0-4` |

## 3. Consumption — embedding the feed on a website

The feed lives at a permanent public URL, so any RSS-aware client works:

**WordPress** — paste the URL into the built-in *RSS* block.

**Static site (vanilla JS)** using
[`rss-parser`](https://github.com/rbren/rss-parser) via CDN:

```html
<div id="bd-markets"></div>
<script type="module">
  import Parser from "https://esm.sh/rss-parser@3";
  const FEED = "https://alaminmain.github.io/BdCapitalMarketNews/feed.xml";
  const proxy = "https://api.allorigins.win/raw?url=";  // CORS shim
  const parser = new Parser();
  const feed = await parser.parseURL(proxy + encodeURIComponent(FEED));
  document.getElementById("bd-markets").innerHTML = feed.items
    .slice(0, 10)
    .map(i => `<article><h3>${i.title}</h3>${i.contentSnippet}</article>`)
    .join("");
</script>
```

**React** — `npm i rss-parser` and call `parser.parseURL` server-side
(Next.js route, Astro endpoint, etc.) to avoid the CORS proxy.

**Notion / Slack / Discord** — paste the URL into the built-in
RSS integration. No code required.

**Feed reader apps** (Feedly, Inoreader, NetNewsWire) — add the URL as
a new subscription.

## 4. Compliance notes

- All summaries are produced by Gemini in original wording — the prompt
  explicitly tells the model not to copy headline text verbatim.
- The scraper hits public, robots-friendly endpoints with a single
  request per run and a descriptive `User-Agent`.
- No personally identifiable information is stored.
- Source URL and source name are kept in the analyzer prompt (so the
  model has context) but not republished — only AI-generated summaries
  end up in the feed and database.

## 5. Operational checklist

- [ ] Push repo to GitHub.
- [ ] Add `GEMINI_API_KEY` secret.
- [ ] Add `FEED_PUBLIC_URL` repo variable (after first Pages deploy).
- [ ] Enable GitHub Pages (Source: GitHub Actions).
- [ ] Trigger `Daily BD Markets News Run` once manually to seed
      `market_updates.db` and `docs/feed.xml`.
- [ ] Verify the feed validates at
      [validator.w3.org/feed](https://validator.w3.org/feed/).
