"""End-to-end runner: scrape -> classify -> persist -> publish RSS."""
from __future__ import annotations

import argparse
import logging
import sys

from src import config
from src.analyzer import analyze_headlines
from src.database import (
    fetch_latest,
    filter_unseen_headlines,
    init_db,
    insert_updates,
)
from src.rss_generator import write_feed
from src.scraper import scrape_all


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def run(dry_run: bool = False) -> int:
    log = logging.getLogger("bd-markets")

    init_db(config.DB_PATH)

    log.info("== Stage 1: scrape ==")
    headlines = scrape_all()
    log.info("Collected %d unique headlines", len(headlines))
    if not headlines:
        log.warning("No headlines scraped; aborting before AI call.")
        return 1

    log.info("== Stage 2: dedupe against previously-seen headlines ==")
    fresh = filter_unseen_headlines(config.DB_PATH, headlines)
    if not fresh:
        log.info("Nothing new since last run; refreshing feed only.")

    log.info("== Stage 3: classify with Gemini ==")
    if dry_run or not fresh:
        if dry_run:
            log.info("Dry-run: skipping Gemini call.")
        classified = []
    else:
        classified = analyze_headlines(fresh)

    log.info("== Stage 4: persist to SQLite ==")
    inserted = insert_updates(config.DB_PATH, classified)
    log.info("Stored %d new classified updates", inserted)

    log.info("== Stage 5: regenerate RSS feed ==")
    latest = fetch_latest(config.DB_PATH, limit=config.MAX_FEED_ITEMS)
    write_feed(latest, config.FEED_PATH)

    log.info("Pipeline complete: %d total items in feed", len(latest))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="BD market news aggregator")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape and store from cache only; skip Gemini",
    )
    args = parser.parse_args()
    configure_logging(args.verbose)
    try:
        return run(dry_run=args.dry_run)
    except Exception:
        logging.exception("Fatal error during pipeline run")
        return 2


if __name__ == "__main__":
    sys.exit(main())
