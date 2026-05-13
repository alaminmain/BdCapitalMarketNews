"""Runtime configuration for the news aggregator."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
DB_PATH = ROOT / "market_updates.db"
FEED_PATH = DOCS_DIR / "feed.xml"
HTML_PATH = DOCS_DIR / "index.html"
HISTORY_PATH = DOCS_DIR / "history.json"
SOURCES_PATH = ROOT / "sources.json"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Default to a Flash model so the project works on Google AI Studio's free
# tier. Pro/Preview models (e.g. gemini-3.1-pro-preview) need a paid billing
# account; set GEMINI_MODEL as a repo variable to opt in once enabled.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# GoatCounter subdomain (e.g. "bdmarkets" for https://bdmarkets.goatcounter.com).
# When empty, the analytics script + visitor counter widget are omitted from the
# rendered page. Enable "Display public stats publicly" on the GoatCounter site
# so the /counter endpoint serves counts without auth.
GOATCOUNTER_CODE = os.environ.get("GOATCOUNTER_CODE", "").strip()

REQUEST_TIMEOUT = 20
# Many BD news sites (DSE included) reject obvious-bot UAs with 403.
# Use a recent desktop Chrome string; we still throttle to one request per page.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DSE_HOMEPAGE_URL = "https://www.dsebd.org/"
DSE_RECENT_MARKET_URL = "https://www.dsebd.org/recent_market_information.php"

FEED_TITLE = "Bangladesh Capital & Money Markets — Daily Pulse"
FEED_LINK = os.environ.get(
    "FEED_PUBLIC_URL",
    "https://alaminmain.github.io/BdCapitalMarketNews/feed.xml",
)
FEED_DESCRIPTION = (
    "AI-curated daily Good / Bad / Ugly assessment of developments in the "
    "Dhaka Stock Exchange and the broader Bangladesh money markets."
)
FEED_LANGUAGE = "en"
FEED_AUTHOR = "BD Markets News Bot"
MAX_FEED_ITEMS = 50
