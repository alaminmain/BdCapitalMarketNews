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

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Iterable, List, Dict, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .config import (
    DSE_HOMEPAGE_URL,
    DSE_RECENT_MARKET_URL,
    REQUEST_TIMEOUT,
    SOURCES_PATH,
    USER_AGENT,
)

log = logging.getLogger(__name__)

Headline = Dict[str, str]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fetch(url: str, *, verify_tls: bool = True) -> Optional[str]:
    # NOTE: we intentionally do NOT advertise `br` in Accept-Encoding.
    # `requests` only decodes the encodings it has installed; if the
    # server picks brotli and we don't have the `brotli`/`brotlicffi`
    # package, response.text returns undecoded bytes and BeautifulSoup
    # parses garbage (silent 0-item scrape). gzip/deflate are built in;
    # urllib3 2.x adds zstd transparently. TBS/Dhaka Tribune/Daily Star
    # all serve brotli by default — this was the cause of those sources
    # producing zero items since they were added.
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
                "Accept-Encoding": "gzip, deflate",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
            timeout=REQUEST_TIMEOUT,
            verify=verify_tls,
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
# Generic config-driven scrapers (sources.json)
# ---------------------------------------------------------------------------

_SUPPORTED_TYPES = ("article_list",)


def _load_sources_config() -> List[Dict[str, Any]]:
    if not SOURCES_PATH.exists():
        log.info("sources.json not found at %s; skipping config sources", SOURCES_PATH)
        return []
    try:
        data = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error("Failed to read %s: %s", SOURCES_PATH, exc)
        return []
    sources = data.get("sources") if isinstance(data, dict) else None
    if not isinstance(sources, list):
        log.error("%s: top-level 'sources' must be a list", SOURCES_PATH)
        return []
    return [s for s in sources if isinstance(s, dict)]


def _scrape_article_list(cfg: Dict[str, Any]) -> List[Headline]:
    """Generic article-list scraper driven by a JSON entry.

    Recognised fields (all optional except name + url):

    - name (str):                source label used in feed + dedupe
    - url (str):                 page to fetch
    - selector (str):            CSS selector for anchors; default "a"
    - require_domain (str):      substring an absolute URL must contain
    - exclude_url_patterns (list[str]): substrings that disqualify a URL
    - min_title_length (int):    default 25
    - max_title_length (int):    default 220
    - filter_chrome (bool):      apply _looks_like_chrome filter; default true
    - max_items (int):           cap; default 30
    """
    name = cfg.get("name")
    url = cfg.get("url")
    if not name or not url:
        log.warning("Skipping source with missing name/url: %r", cfg)
        return []

    verify_tls = not bool(cfg.get("insecure_tls", False))
    if not verify_tls:
        # urllib3 emits a noisy InsecureRequestWarning every fetch otherwise.
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    html = _fetch(url, verify_tls=verify_tls)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")

    selector = cfg.get("selector", "a")
    require_domain = cfg.get("require_domain", "")
    exclude_patterns = cfg.get("exclude_url_patterns", []) or []
    min_len = int(cfg.get("min_title_length", 25))
    max_len = int(cfg.get("max_title_length", 220))
    filter_chrome = bool(cfg.get("filter_chrome", True))
    max_items = int(cfg.get("max_items", 30))

    items: List[Headline] = []
    for a in soup.select(selector):
        href = a.get("href", "")
        title = _clean(a.get_text(" ", strip=True))
        if not title or len(title) < min_len or len(title) > max_len:
            continue
        if not href or href.startswith("#") or "javascript:" in href:
            continue
        absolute = urljoin(url, href)
        if require_domain and require_domain not in absolute:
            continue
        if any(skip in absolute for skip in exclude_patterns):
            continue
        if filter_chrome and _looks_like_chrome(title):
            continue
        items.append(
            {
                "source": name,
                "title": title,
                "url": absolute,
                "scraped_at": _now_iso(),
            }
        )
    return items[:max_items]


def scrape_configured_sources() -> List[Headline]:
    """Run every source declared in sources.json."""
    collected: List[Headline] = []
    for cfg in _load_sources_config():
        stype = cfg.get("type", "article_list")
        if stype not in _SUPPORTED_TYPES:
            log.warning("Unknown source type %r for %r; skipping", stype, cfg.get("name"))
            continue
        try:
            results = _scrape_article_list(cfg)
            log.info("config:%s -> %d items", cfg.get("name"), len(results))
            collected.extend(results)
        except Exception as exc:  # one bad source must not kill the run
            log.exception("Configured source %r crashed: %s", cfg.get("name"), exc)
    return collected


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def scrape_all() -> List[Headline]:
    """Run every scraper and return a deduplicated list of headlines."""
    collected: List[Headline] = []
    for fn in (
        scrape_dse_latest_news,
        scrape_dse_recent_market,
        scrape_configured_sources,
    ):
        try:
            results = fn()
            log.info("%s -> %d items", fn.__name__, len(results))
            collected.extend(results)
        except Exception as exc:  # one bad source must not kill the run
            log.exception("Scraper %s crashed: %s", fn.__name__, exc)

    return _dedupe(collected)
