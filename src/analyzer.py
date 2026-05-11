"""Gemini-powered Good / Bad / Ugly classifier for BD market headlines."""
from __future__ import annotations

import json
import logging
import re
from typing import List, Dict

from google import genai
from google.genai import types

from .config import GEMINI_API_KEY, GEMINI_MODEL

log = logging.getLogger(__name__)

ANALYST_PERSONA = (
    "You are a senior financial analyst specializing in the Bangladesh "
    "Capital (DSE) and Money Markets. Categorize the most significant "
    "developments as Good (e.g., high dividends, structural reforms), "
    "Bad (e.g., inflation, index drops), or Ugly (e.g., systemic bank "
    "failure, energy shocks). For each item, provide a concise Headline "
    "Summary and a one-sentence Reason. Output the result as a "
    "JSON-formatted list of objects with the keys: category, summary, "
    "and reason."
)

ALLOWED_CATEGORIES = {"Good", "Bad", "Ugly"}


def _build_prompt(headlines: List[Dict[str, str]]) -> str:
    lines = []
    for idx, h in enumerate(headlines, start=1):
        src = h.get("source", "Unknown")
        title = h.get("title", "").strip()
        lines.append(f"{idx}. [{src}] {title}")
    body = "\n".join(lines)
    return (
        "Analyze the following Bangladesh capital and money market headlines "
        "scraped today. Select only the materially significant items. "
        "Write summaries in your own words (do not copy headline text "
        "verbatim) so the output is suitable for redistribution.\n\n"
        f"HEADLINES:\n{body}\n\n"
        "Return ONLY a JSON array. No prose, no markdown fences."
    )


def _extract_json_array(text: str) -> List[Dict]:
    if not text:
        return []
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        log.warning("Failed to parse Gemini JSON: %s", exc)
        return []
    return parsed if isinstance(parsed, list) else []


def _normalize(items: List[Dict]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category", "")).strip().capitalize()
        summary = str(item.get("summary", "")).strip()
        reason = str(item.get("reason", "")).strip()
        if category not in ALLOWED_CATEGORIES or not summary or not reason:
            continue
        out.append({"category": category, "summary": summary, "reason": reason})
    return out


def analyze_headlines(headlines: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Send headlines to Gemini and return classified entries."""
    if not headlines:
        log.info("No headlines to analyze.")
        return []
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Export it or store it as a "
            "GitHub Actions secret before running the analyzer."
        )

    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = _build_prompt(headlines)
    log.info("Calling Gemini (%s) with %d headlines", GEMINI_MODEL, len(headlines))

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=ANALYST_PERSONA,
            temperature=0.3,
            response_mime_type="application/json",
        ),
    )

    raw = getattr(response, "text", "") or ""
    parsed = _extract_json_array(raw)
    classified = _normalize(parsed)
    log.info("Gemini returned %d valid classified items", len(classified))
    return classified
