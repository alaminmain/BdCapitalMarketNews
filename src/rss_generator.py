"""RSS 2.0 feed generation from stored market updates."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from feedgen.feed import FeedGenerator

from .config import (
    FEED_AUTHOR,
    FEED_DESCRIPTION,
    FEED_LANGUAGE,
    FEED_LINK,
    FEED_TITLE,
)

log = logging.getLogger(__name__)

CATEGORY_EMOJI = {"Good": "[GOOD]", "Bad": "[BAD]", "Ugly": "[UGLY]"}


def _parse_pub_date(row: Dict[str, str]) -> datetime:
    raw = row.get("created_at") or f"{row.get('date', '')} 00:00:00"
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return datetime.now(timezone.utc)


def write_feed(items: List[Dict[str, str]], output_path: Path) -> Path:
    fg = FeedGenerator()
    fg.title(FEED_TITLE)
    fg.link(href=FEED_LINK, rel="self")
    fg.link(href=FEED_LINK.rsplit("/", 1)[0] + "/", rel="alternate")
    fg.description(FEED_DESCRIPTION)
    fg.language(FEED_LANGUAGE)
    fg.author({"name": FEED_AUTHOR})
    fg.lastBuildDate(datetime.now(timezone.utc))
    fg.generator("BD-Markets-NewsBot")

    for row in items:
        category = row.get("category", "")
        tag = CATEGORY_EMOJI.get(category, "")
        title = f"{tag} {row.get('summary', '').strip()}".strip()

        description = (row.get("description") or row.get("reason") or "").strip()
        source_url = (row.get("source_url") or "").strip()
        item_link = source_url or FEED_LINK

        body_parts = [
            f"<p><strong>Category:</strong> {category}</p>",
            f"<p>{description}</p>",
            f"<p><strong>Why this matters:</strong> {row.get('reason', '')}</p>",
        ]
        if source_url:
            body_parts.append(
                f'<p><a href="{source_url}">Read original source</a></p>'
            )
        body_parts.append(f"<p><em>Recorded: {row.get('date', '')}</em></p>")
        body = "".join(body_parts)

        fe = fg.add_entry()
        guid = f"bd-markets-{row.get('id', row.get('date', ''))}-{row.get('date', '')}"
        fe.id(guid)
        fe.guid(guid, permalink=False)
        fe.title(title[:300] or "Market Update")
        fe.link(href=item_link)
        fe.description(body)
        fe.category({"term": category})
        fe.pubDate(_parse_pub_date(row))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fg.rss_file(str(output_path), pretty=True)
    log.info("RSS feed written to %s (%d items)", output_path, len(items))
    return output_path
