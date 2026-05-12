"""SQLite persistence layer for market updates."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_updates (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    date           TEXT    NOT NULL,
    category       TEXT    NOT NULL CHECK (category IN ('Good','Bad','Ugly')),
    summary        TEXT    NOT NULL,
    summary_bn     TEXT,
    description    TEXT,
    description_bn TEXT,
    reason         TEXT    NOT NULL,
    reason_bn      TEXT,
    source_url     TEXT,
    tags           TEXT,
    hash           TEXT    NOT NULL UNIQUE,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_market_updates_date ON market_updates(date);
CREATE INDEX IF NOT EXISTS idx_market_updates_category ON market_updates(category);

CREATE TABLE IF NOT EXISTS seen_headlines (
    hash       TEXT PRIMARY KEY,
    source     TEXT NOT NULL,
    title      TEXT NOT NULL,
    first_seen TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Backfill columns onto pre-existing databases. SQLite has no
# IF NOT EXISTS for ADD COLUMN, so we swallow the duplicate error.
_MIGRATIONS = (
    "ALTER TABLE market_updates ADD COLUMN description TEXT",
    "ALTER TABLE market_updates ADD COLUMN source_url TEXT",
    "ALTER TABLE market_updates ADD COLUMN summary_bn TEXT",
    "ALTER TABLE market_updates ADD COLUMN description_bn TEXT",
    "ALTER TABLE market_updates ADD COLUMN reason_bn TEXT",
    "ALTER TABLE market_updates ADD COLUMN tags TEXT",
)


@contextmanager
def _connect(db_path: Path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already present


def _hash_item(date: str, summary: str) -> str:
    return hashlib.sha256(f"{date}|{summary.lower()}".encode("utf-8")).hexdigest()


def insert_updates(
    db_path: Path, items: Iterable[Dict[str, str]]
) -> int:
    """Insert classified items, skipping duplicates. Returns insert count."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    inserted = 0
    with _connect(db_path) as conn:
        for item in items:
            digest = _hash_item(today, item["summary"])
            tags_json = json.dumps(item.get("tags") or [], ensure_ascii=False)
            try:
                conn.execute(
                    "INSERT INTO market_updates "
                    "(date, category, summary, summary_bn, description, "
                    " description_bn, reason, reason_bn, source_url, tags, hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        today,
                        item["category"],
                        item["summary"],
                        item.get("summary_bn", ""),
                        item.get("description", ""),
                        item.get("description_bn", ""),
                        item["reason"],
                        item.get("reason_bn", ""),
                        item.get("source_url", ""),
                        tags_json,
                        digest,
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                log.debug("Duplicate skipped: %s", item["summary"][:60])
    log.info("Inserted %d new rows into %s", inserted, db_path.name)
    return inserted


def _headline_hash(h: Dict[str, str]) -> str:
    return hashlib.sha256(
        f"{h.get('source','')}|{h['title'].strip().lower()}".encode("utf-8")
    ).hexdigest()


def find_unseen_headlines(
    db_path: Path, headlines: Iterable[Dict[str, str]]
) -> List[Dict[str, str]]:
    """Return headlines not previously seen. Read-only — does NOT mark them.

    Pair with ``mark_headlines_seen`` *after* downstream processing
    succeeds so a Gemini failure does not silently swallow the day's
    data.
    """
    fresh: List[Dict[str, str]] = []
    total = 0
    with _connect(db_path) as conn:
        for h in headlines:
            total += 1
            row = conn.execute(
                "SELECT 1 FROM seen_headlines WHERE hash = ?",
                (_headline_hash(h),),
            ).fetchone()
            if not row:
                fresh.append(h)
    log.info("Headline dedup: %d new of %d scraped", len(fresh), total)
    return fresh


def mark_headlines_seen(
    db_path: Path, headlines: Iterable[Dict[str, str]]
) -> None:
    """Record headlines so future scrapes skip them. Idempotent."""
    with _connect(db_path) as conn:
        for h in headlines:
            conn.execute(
                "INSERT OR IGNORE INTO seen_headlines (hash, source, title) "
                "VALUES (?, ?, ?)",
                (_headline_hash(h), h.get("source", ""), h["title"]),
            )


def reset_today_seen(db_path: Path) -> int:
    """Delete today's (UTC) seen_headlines rows so they re-enter dedup.

    Useful after a failed run that marked headlines but never classified
    them, or when you want a manual workflow trigger to re-process the
    full day's batch.
    """
    with _connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM seen_headlines WHERE date(first_seen) = date('now')"
        )
        deleted = cur.rowcount
    log.info("Reset cleared %d seen_headlines from today", deleted)
    return deleted


def _row_to_dict(row: sqlite3.Row) -> Dict:
    """Convert a sqlite3.Row into a dict, decoding the tags JSON blob."""
    d = dict(row)
    raw = d.get("tags")
    if raw:
        try:
            decoded = json.loads(raw)
            d["tags"] = decoded if isinstance(decoded, list) else []
        except (json.JSONDecodeError, TypeError):
            d["tags"] = []
    else:
        d["tags"] = []
    return d


def fetch_latest(db_path: Path, limit: int = 50) -> List[Dict[str, str]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, date, category, summary, summary_bn, description, "
            "       description_bn, reason, reason_bn, source_url, tags, "
            "       created_at "
            "FROM market_updates ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def fetch_all(db_path: Path) -> List[Dict[str, str]]:
    """Return every row in the DB. Used to bake history.json for the page."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, date, category, summary, summary_bn, description, "
            "       description_bn, reason, reason_bn, source_url, tags, "
            "       created_at "
            "FROM market_updates ORDER BY date DESC, id DESC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]
