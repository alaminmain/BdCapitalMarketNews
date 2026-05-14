"""SQLite persistence layer for market updates."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List

log = logging.getLogger(__name__)

# Stopwords + currency-prefix tokens common in BD market headlines.
# Kept short on purpose — over-stripping risks collapsing distinct events.
_STOPWORDS = frozenset({
    "a","an","the","and","or","but","of","to","in","on","for","with","by",
    "from","at","as","is","are","was","were","be","been","being","has","have",
    "had","do","does","did","will","would","could","should","may","might","can",
    "this","that","these","those","it","its","into","over","under","after",
    "before","up","down","out","tk","cr",
})

# Match runs of Unicode letters only — drops digits, underscore, punctuation.
# Bengali (Share News 24) survives since \w is Unicode-aware in Python 3.
_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _normalize_text(text: str) -> str:
    """Reduce a headline/summary to a comparable fingerprint.

    Collapses case, drops digits and punctuation, removes filler
    stopwords, and joins what remains. Two headlines that say the same
    thing in slightly different words (different sources, reword on
    re-publish, currency suffix dropped) collapse to the same string.

    Order is preserved on purpose: "ABC buys XYZ" and "XYZ buys ABC"
    remain distinct events. We rely on the company prefix / verb / object
    pattern to keep meaning stable.
    """
    tokens = _TOKEN_RE.findall((text or "").lower())
    kept = [t for t in tokens if t not in _STOPWORDS]
    return " ".join(kept)

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
        _rehash_for_normalized_dedup(conn)


def _rehash_for_normalized_dedup(conn: sqlite3.Connection) -> None:
    """Re-hash existing rows with the normalized-text formula.

    The previous keys baked in source/date, so they cannot collide with
    hashes computed by the current ``_headline_hash`` / ``_hash_item``.
    Without this migration, the first run after deploy treats every
    DSE-marquee item as new (re-classification) and the second-pass
    Gemini outputs slip past the ``market_updates`` UNIQUE constraint.
    Idempotent: a row already at the new hash is left alone; collisions
    after rehash collapse to the row with the smallest id.
    """
    cur = conn.execute("PRAGMA user_version")
    version = cur.fetchone()[0]
    if version >= 1:
        return

    # market_updates: rehash by id, drop duplicates that now collide.
    rows = conn.execute("SELECT id, summary FROM market_updates").fetchall()
    seen: Dict[str, int] = {}
    drop_ids: List[int] = []
    for row in rows:
        new_hash = _hash_item("", row["summary"])
        keeper = seen.get(new_hash)
        if keeper is None:
            seen[new_hash] = row["id"]
        else:
            drop_ids.append(row["id"])
    if drop_ids:
        log.info("Dedup migration: dropping %d duplicate market_updates rows", len(drop_ids))
        conn.executemany(
            "DELETE FROM market_updates WHERE id = ?",
            [(i,) for i in drop_ids],
        )
    for new_hash, keeper_id in seen.items():
        conn.execute(
            "UPDATE market_updates SET hash = ? WHERE id = ?",
            (new_hash, keeper_id),
        )

    # seen_headlines: PRIMARY KEY is the hash, so use a swap-via-temp.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _seen_tmp ("
        "hash TEXT PRIMARY KEY, source TEXT NOT NULL, "
        "title TEXT NOT NULL, first_seen TEXT NOT NULL)"
    )
    headlines = conn.execute(
        "SELECT source, title, first_seen FROM seen_headlines"
    ).fetchall()
    for h in headlines:
        new_hash = _headline_hash({"title": h["title"]})
        conn.execute(
            "INSERT OR IGNORE INTO _seen_tmp (hash, source, title, first_seen) "
            "VALUES (?, ?, ?, ?)",
            (new_hash, h["source"], h["title"], h["first_seen"]),
        )
    conn.execute("DROP TABLE seen_headlines")
    conn.execute("ALTER TABLE _seen_tmp RENAME TO seen_headlines")

    conn.execute("PRAGMA user_version = 1")
    log.info("Dedup migration complete; user_version bumped to 1")


def _hash_item(date: str, summary: str) -> str:
    # Cross-date, normalized-summary hash: an event reported today and
    # again next week (slightly rephrased) collides and the second
    # insert is dropped. Date is kept out of the key on purpose; the
    # unique constraint then enforces "same event = one row, ever".
    return hashlib.sha256(_normalize_text(summary).encode("utf-8")).hexdigest()


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
    # Source is intentionally NOT in the key: the same disclosure surfaced
    # by DSE *and* The Business Standard should hash identically so only
    # one copy reaches Gemini. Normalization collapses minor reword
    # differences (currency suffix, stopwords, case).
    return hashlib.sha256(_normalize_text(h["title"]).encode("utf-8")).hexdigest()


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
