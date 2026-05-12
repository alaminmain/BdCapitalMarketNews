"""Controlled tag vocabulary for market updates.

Tags are a fixed list, not free-form, so the filter UI on the page is
deterministic (no "Dividend Declaration" vs "Dividend" vs "div" drift).
Gemini is constrained to pick from these slugs; anything outside is
discarded in ``analyzer._normalize``.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

# (canonical_slug, english_label, bengali_label, group)
# Slug is what Gemini emits and what gets stored in the DB; labels are
# only for rendering chips on the page.
_TAG_ROWS: Tuple[Tuple[str, str, str, str], ...] = (
    # Sectors
    ("Bank",          "Bank",          "ব্যাংক",            "Sector"),
    ("NBFI",          "NBFI",          "নন-ব্যাংক আর্থিক",   "Sector"),
    ("Insurance",     "Insurance",     "বীমা",              "Sector"),
    ("Energy",        "Energy",        "জ্বালানি",          "Sector"),
    ("Telecom",       "Telecom",       "টেলিকম",            "Sector"),
    ("Pharma",        "Pharma",        "ফার্মা",            "Sector"),
    ("Textile",       "Textile",       "টেক্সটাইল",         "Sector"),
    ("Cement",        "Cement",        "সিমেন্ট",           "Sector"),
    ("IT",            "IT",            "আইটি",              "Sector"),
    ("Food",          "Food",          "খাদ্য",             "Sector"),
    # Corporate events
    ("Dividend",      "Dividend",      "লভ্যাংশ",           "Event"),
    ("Earnings",      "Earnings",      "আয়",                "Event"),
    ("AGM",           "AGM",           "এজিএম",             "Event"),
    ("IPO",           "IPO",           "আইপিও",             "Event"),
    ("Bond",          "Bond",          "বন্ড",              "Event"),
    ("Rights",        "Rights",        "রাইট শেয়ার",        "Event"),
    ("TradingHalt",   "Trading Halt",  "ট্রেডিং হল্ট",      "Event"),
    ("Regulation",    "Regulation",    "নিয়ন্ত্রণ",         "Event"),
    # Macro
    ("Economy",       "Economy",       "অর্থনীতি",          "Macro"),
    ("Inflation",     "Inflation",     "মূল্যস্ফীতি",       "Macro"),
    ("FX",            "FX",            "বৈদেশিক মুদ্রা",    "Macro"),
    ("Index",         "Index",         "সূচক",              "Macro"),
)

ALLOWED_TAGS: Tuple[str, ...] = tuple(row[0] for row in _TAG_ROWS)
ALLOWED_TAG_SET = set(ALLOWED_TAGS)


def tag_vocabulary() -> List[Dict[str, str]]:
    """Return [{slug, en, bn, group}, ...] for the page's chip UI."""
    return [
        {"slug": slug, "en": en, "bn": bn, "group": group}
        for (slug, en, bn, group) in _TAG_ROWS
    ]


def normalize_tags(raw) -> List[str]:
    """Filter incoming tag values to the allowed slug list.

    Accepts list[str] (preferred) or comma-delimited string. Unknown
    slugs are dropped; order is preserved; duplicates removed.
    """
    if not raw:
        return []
    if isinstance(raw, str):
        candidates = [s.strip() for s in raw.split(",")]
    elif isinstance(raw, (list, tuple)):
        candidates = [str(s).strip() for s in raw]
    else:
        return []
    seen: set = set()
    out: List[str] = []
    for c in candidates:
        if c in ALLOWED_TAG_SET and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def prompt_tag_list() -> str:
    """Pretty-printed allowed-tag list for the Gemini prompt."""
    groups: Dict[str, List[str]] = {}
    for slug, _en, _bn, group in _TAG_ROWS:
        groups.setdefault(group, []).append(slug)
    lines = []
    for group, slugs in groups.items():
        lines.append(f"  {group}: {', '.join(slugs)}")
    return "\n".join(lines)
