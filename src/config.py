"""Runtime configuration for the news aggregator."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
DB_PATH = ROOT / "market_updates.db"
FEED_PATH = DOCS_DIR / "feed.xml"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview")

REQUEST_TIMEOUT = 20
# Many BD news sites (DSE included) reject obvious-bot UAs with 403.
# Use a recent desktop Chrome string; we still throttle to one request per page.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DSE_HOMEPAGE_URL = "https://www.dsebd.org/"
DSE_RECENT_MARKET_URL = "https://www.dsebd.org/recent_market_information.php"
FE_ECONOMY_URL = "https://thefinancialexpress.com.bd/economy"
FE_STOCK_URL = "https://thefinancialexpress.com.bd/stock"

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
