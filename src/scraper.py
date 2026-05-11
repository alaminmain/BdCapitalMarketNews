"""Headline scrapers for DSE and Bangladesh financial news outlets.

Each scraper returns a list of dicts with a stable shape so that downstream
consumers (analyzer, deduper) do not need to know which source produced an
item:

    {
        "source":  "DSE - Latest News",
        "title":   "<headline text>",
        "url":     "<absolute URL or empty string>",
        "scraped_at": "<ISO-8601 UTC timestamp>",
    }
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Iterable, List, Dict, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .config import (
    DSE_HOMEPAGE_URL,
    DSE_RECENT_MARKET_URL,
    FE_ECONOMY_URL,
    FE_STOCK_URL,
    REQUEST_TIMEOUT,
    USER_AGENT,
)

log = logging.getLogger(__name__)

Headline = Dict[str, str]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fetch(url: str) -> Optional[str]:
    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;"
                    "q=0.9,image/avif,image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9,bn;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        log.warning("Fetch failed for %s: %s", url, exc)
        return None


_FE_CHROME_PATTERNS = (
    "year of publication",
    "anniversary issue",
    "editor@",
    "advertise with us",
    "subscribe",
    "newsletter",
    "contact us",
    "about us",
    "privacy policy",
)


def _looks_like_chrome(text: str) -> bool:
    low = text.lower()
    return any(p in low for p in _FE_CHROME_PATTERNS) or "@" in text


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _dedupe(items: Iterable[Headline]) -> List[Headline]:
    seen = set()
    out: List[Headline] = []
    for item in items:
        key = (item.get("source", ""), item["title"].lower())
        if not item["title"] or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Dhaka Stock Exchange
# ---------------------------------------------------------------------------

_DSE_GENERIC_PATTERNS = (
    "greetings message",
    "awareness message",
    "good morning",
    "good evening",
)


def scrape_dse_latest_news() -> List[Headline]:
    """Corporate disclosures from the DSE homepage news ticker.

    The DSE landing page renders ~50 disclosures inside a <marquee> block,
    each one as two consecutive ``display_news.php`` anchors:
    a header (``DBH: Dividend Declaration``) followed by a body
    (``The Board of Directors has recommended 15% Cash Dividend...``).
    We pair them and drop standing notices.
    """
    html = _fetch(DSE_HOMEPAGE_URL)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    # The news ticker sits in a <marquee>; we fall back to the whole
    # document if DSE ever drops the marquee element.
    container = None
    for m in soup.find_all("marquee"):
        if m.find("a", href=lambda h: h and "display_news.php" in h):
            container = m
            break
    if container is None:
        container = soup

    anchors = [
        a for a in container.find_all("a", href=True)
        if "display_news.php" in a["href"]
    ]
    texts = [_clean(a.get_text(" ", strip=True)) for a in anchors]
    texts = [t for t in texts if t]
    log.info("DSE: found %d display_news anchors", len(texts))

    items: List[Headline] = []
    i = 0
    while i < len(texts):
        title = texts[i]
        body = texts[i + 1] if i + 1 < len(texts) else ""
        i += 2 if body and not _looks_like_dse_title(body) else 1

        low = title.lower()
        if any(p in low for p in _DSE_GENERIC_PATTERNS):
            continue
        combined = f"{title} | {body}".strip(" |")
        if len(combined) < 20:
            continue
        items.append(
            {
                "source": "DSE - Corporate Disclosures",
                "title": combined[:500],
                "url": DSE_HOMEPAGE_URL,
                "scraped_at": _now_iso(),
            }
        )

    return items


def _looks_like_dse_title(text: str) -> bool:
    """A DSE 'title' anchor looks like ``TICKER: Topic`` or ``DSE NEWS:``."""
    if not text:
        return False
    head = text.split(":", 1)[0].strip()
    return bool(head) and head == head.upper() and len(head) <= 20


def scrape_dse_recent_market() -> List[Headline]:
    """Recent market information feed (suspensions, spot trading, etc.)."""
    html = _fetch(DSE_RECENT_MARKET_URL)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: List[Headline] = []

    for row in soup.select("table tr"):
        cells = [_clean(c.get_text(" ", strip=True)) for c in row.find_all("td")]
        cells = [c for c in cells if c]
        if len(cells) < 2:
            continue
        text = " | ".join(cells)
        if len(text) < 25:
            continue
        items.append(
            {
                "source": "DSE - Market Information",
                "title": text[:400],
                "url": DSE_RECENT_MARKET_URL,
                "scraped_at": _now_iso(),
            }
        )

    return items[:40]


# ---------------------------------------------------------------------------
# The Financial Express (Bangladesh)
# ---------------------------------------------------------------------------

def _scrape_fe_section(url: str, label: str) -> List[Headline]:
    html = _fetch(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    items: List[Headline] = []

    for a in soup.select("a"):
        href = a.get("href", "")
        title = _clean(a.get_text(" ", strip=True))
        if not title or len(title) < 25 or len(title) > 220:
            continue
        if not href or href.startswith("#") or "javascript:" in href:
            continue
        absolute = urljoin(url, href)
        # Only keep article-like links from the same domain.
        if "thefinancialexpress.com.bd" not in absolute:
            continue
        if any(skip in absolute for skip in ("/tag/", "/author/", "/page/")):
            continue
        if _looks_like_chrome(title):
            continue
        items.append(
            {
                "source": f"The Financial Express - {label}",
                "title": title,
                "url": absolute,
                "scraped_at": _now_iso(),
            }
        )
    return items[:30]


def scrape_financial_express() -> List[Headline]:
    return (
        _scrape_fe_section(FE_ECONOMY_URL, "Economy")
        + _scrape_fe_section(FE_STOCK_URL, "Stock")
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def scrape_all() -> List[Headline]:
    """Run every scraper and return a deduplicated list of headlines."""
    collected: List[Headline] = []
    for fn in (
        scrape_dse_latest_news,
        scrape_dse_recent_market,
        scrape_financial_express,
    ):
        try:
            results = fn()
            log.info("%s -> %d items", fn.__name__, len(results))
            collected.extend(results)
        except Exception as exc:  # one bad source must not kill the run
            log.exception("Scraper %s crashed: %s", fn.__name__, exc)

    return _dedupe(collected)
