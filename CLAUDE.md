# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Local setup (Windows / PowerShell paths shown; venv lives at .venv/)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Full pipeline (requires GEMINI_API_KEY)
python main.py --verbose

# Scrape + dedupe + RSS regen, no Gemini call (free, fast)
python main.py --dry-run --verbose

# Override the model via env var; default is gemini-2.5-flash
$env:GEMINI_MODEL = "gemini-flash-latest"
```

There is **no test framework**. Ad-hoc verification is done with throwaway `_*.py` scripts at the repo root — they are gitignored (see `.gitignore`). When debugging a scraper, write `_probe.py`, run it, delete it.

## Architecture: things that took digging to figure out

### DSE scraping is fragile and quirky
- `dsebd.org/latest_news.php` is **404** — gone. Don't try to use it.
- `dsebd.org/recent_market_information.php` returns 200 but contains *price/trade* data, not corporate disclosures.
- The actual corporate disclosures (dividends, EPS, AGMs, TREC actions) are rendered inline on the **homepage** inside a `<marquee>` element, as ~100 consecutive `display_news.php` anchors. There are multiple `div.panel.panel-dse` on the page; you must search for the marquee that contains `display_news.php` anchors specifically.
- Each disclosure is **two consecutive anchors**: a title (`DBH: Dividend Declaration`) followed by a body (`The Board of Directors has recommended 15% Cash Dividend...`). `_looks_like_dse_title()` distinguishes them by checking for an all-caps prefix before `:`.
- DSE does **not deep-link** individual disclosures — every anchor's href is just `display_news.php` (the news ID is set client-side via JS). That's why `source_url` for DSE items is just the homepage URL.

### User-Agent is load-bearing
DSE returns **403** to anything that looks like a bot, including the obvious `BD-Markets-NewsBot/1.0` string. The UA in `src/config.py` is a stock Chrome desktop string and must stay one. Same for `Accept-Language`/`Accept-Encoding` headers in `_fetch()`.

### Two-tier deduplication
- `seen_headlines` table (keyed on `sha256(source|lower(title))`) — applied to **scraper output before Gemini**. Saves API quota when the same disclosure lingers on the DSE marquee across days.
- `market_updates.hash` (keyed on `sha256(date|lower(summary))`) — applied to **Gemini output before insert**. Makes same-day re-runs of `main.py` idempotent.

Wiping `seen_headlines` forces re-classification of everything currently on the page.

### Gemini free tier only supports Flash models
`gemini-2.0-flash`, `gemini-3.1-pro-preview`, and other Pro/Preview models return `429 RESOURCE_EXHAUSTED` with `limit: 0` on free-tier keys — they require a paid billing account. The default in `config.py` is `gemini-2.5-flash` because that is the most capable model that works without billing. The Gemini SDK is `google-genai` (the `google-generativeai` package is deprecated).

### The persona is verbatim from the original spec
`ANALYST_PERSONA` in `src/analyzer.py` is the exact text the user provided in the project brief. **Do not edit it.** When new fields are needed in the JSON output (we added `description`, `source_id`), add them to the user-prompt portion in `_build_prompt()` instead. The `source_id` -> `source_url` mapping happens in `_normalize()` by indexing back into the original headline list.

### Schema migrations are inline ALTER TABLE
SQLite has no `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. New columns are added via `_MIGRATIONS` in `src/database.py`, each wrapped in `try/except sqlite3.OperationalError`. The CI database is committed to the repo, so any schema change must be backwards-compatible (nullable columns, no required values for old rows).

### Data files are intentionally committed
`market_updates.db` and `docs/feed.xml` are tracked in git — not gitignored. The daily cron writes them in CI and pushes them back to `main`. The DB is the persistent history; `feed.xml` is the artifact GitHub Pages serves. Don't add them to `.gitignore`.

### CI workflow merges scrape and Pages deploy into one file
`daily-scrape.yml` has two jobs: `run` (scrape + commit) and `deploy-pages` (deploys `docs/`). They are in the same workflow because **`GITHUB_TOKEN` commits do not trigger downstream `on: push` workflows** (GitHub anti-loop guarantee). A separate `pages.yml` listening on push to `docs/**` would silently never fire after a cron commit.

`GEMINI_API_KEY` must be a **repository secret**, not an environment secret — the workflow's `run` job has no `environment:` declaration so environment-scoped secrets are invisible to it.

## Cron schedule

`0 3 * * *` UTC = **09:00 Asia/Dhaka** (UTC+6, no DST). Bangladesh markets open 10:00 local; this catches overnight earnings releases and morning corporate disclosures before the trading session.
