"""Dump every market_updates row into docs/history.json.

Browser-friendly view of the full DB. The page loads this on demand
(when the user activates a filter) so users can search/filter across
all history without a server.

Schema kept stable so the page JS can evolve independently of the DB
schema. ``ticker`` is extracted up front so the filter UI doesn't need
to repeat the regex on every keystroke.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .database import fetch_all
from .tags import tag_vocabulary

log = logging.getLogger(__name__)

# Mirrors html_generator._TICKER_RE — kept duplicated rather than imported
# to avoid a circular dep through the html module's heavy template.
_TICKER_RE = re.compile(r"^([A-Z0-9][A-Z0-9 .&-]{1,18}):\s*(.+)$")


def _ticker(summary: str) -> str:
    m = _TICKER_RE.match((summary or "").strip())
    return m.group(1).strip() if m else ""


def _trim(row: Dict) -> Dict:
    """Pick only the fields the page actually needs."""
    summary = row.get("summary") or ""
    return {
        "id":             row.get("id"),
        "date":           row.get("date") or "",
        "category":       row.get("category") or "",
        "summary":        summary,
        "summary_bn":     row.get("summary_bn") or "",
        "description":    row.get("description") or "",
        "description_bn": row.get("description_bn") or "",
        "reason":         row.get("reason") or "",
        "reason_bn":      row.get("reason_bn") or "",
        "source_url":     row.get("source_url") or "",
        "tags":           row.get("tags") or [],
        "ticker":         _ticker(summary),
    }


def write_history(db_path: Path, output_path: Path) -> Path:
    rows = fetch_all(db_path)
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "tag_vocabulary": tag_vocabulary(),
        "rows": [_trim(r) for r in rows],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    log.info(
        "history.json written to %s (%d rows)",
        output_path, len(payload["rows"]),
    )
    return output_path


def unique_tickers(rows: List[Dict]) -> List[str]:
    seen = set()
    out: List[str] = []
    for r in rows:
        t = r.get("ticker") or ""
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return sorted(out)
