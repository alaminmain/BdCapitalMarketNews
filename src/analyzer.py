"""Gemini-powered Good / Bad / Ugly classifier for BD market headlines."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import List, Dict

from google import genai
from google.genai import errors as genai_errors
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
        "Write everything in your own words (do not copy headline text "
        "verbatim) so the output is suitable for redistribution.\n\n"
        f"HEADLINES:\n{body}\n\n"
        "Return ONLY a JSON array. Each object MUST contain these keys:\n"
        "  source_id      : integer matching the headline number above\n"
        "  category       : exactly one of \"Good\", \"Bad\", or \"Ugly\"\n"
        "  summary        : short English headline-style line, under 120 chars\n"
        "  summary_bn     : the same headline rewritten in fluent natural\n"
        "                   Bengali (Bangla) for a Bangladeshi reader\n"
        "  description    : 2-3 sentences in plain, easy-to-read English a "
        "                   non-expert investor can follow. Spell out\n"
        "                   acronyms (e.g. EPS, NOCFPS, BSEC) on first use\n"
        "                   and explain *what happened* and *why it matters*\n"
        "  description_bn : the same description in fluent natural Bengali\n"
        "                   (Bangla). Use widely-understood vocabulary;\n"
        "                   transliterate technical terms (BSEC, EPS, NOCFPS)\n"
        "                   when no clean Bengali equivalent exists\n"
        "  reason         : one short English sentence justifying the category\n"
        "  reason_bn      : the same reason translated into Bengali (Bangla)\n"
        "Bengali fields MUST be present and non-empty. No prose, no markdown fences."
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


def _normalize(
    items: List[Dict], headlines: List[Dict[str, str]]
) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category", "")).strip().capitalize()
        summary = str(item.get("summary", "")).strip()
        summary_bn = str(item.get("summary_bn", "")).strip()
        description = str(item.get("description", "")).strip()
        description_bn = str(item.get("description_bn", "")).strip()
        reason = str(item.get("reason", "")).strip()
        reason_bn = str(item.get("reason_bn", "")).strip()
        if category not in ALLOWED_CATEGORIES or not summary or not reason:
            continue

        source_url = ""
        try:
            sid = int(item.get("source_id", 0)) - 1
            if 0 <= sid < len(headlines):
                source_url = headlines[sid].get("url", "")
        except (TypeError, ValueError):
            pass

        out.append(
            {
                "category": category,
                "summary": summary,
                "summary_bn": summary_bn,
                "description": description or reason,
                "description_bn": description_bn,
                "reason": reason,
                "reason_bn": reason_bn,
                "source_url": source_url,
            }
        )
    return out


def _generate_with_retry(client, prompt: str, config, max_attempts: int = 5):
    """Wrap generate_content with backoff for transient 5xx and 429s.

    Gemini periodically returns 503 UNAVAILABLE under load and the SDK's
    built-in retry budget is small. Retrying with exponential backoff
    has been more reliable than a single shot.
    """
    delay = 4
    for attempt in range(1, max_attempts + 1):
        try:
            return client.models.generate_content(
                model=GEMINI_MODEL, contents=prompt, config=config
            )
        except genai_errors.ServerError as exc:  # 5xx
            if attempt == max_attempts:
                raise
            log.warning(
                "Gemini %s on attempt %d/%d; sleeping %ds",
                exc.code, attempt, max_attempts, delay,
            )
        except genai_errors.ClientError as exc:  # 4xx
            if exc.code != 429 or attempt == max_attempts:
                raise
            log.warning(
                "Gemini 429 rate-limited on attempt %d/%d; sleeping %ds",
                attempt, max_attempts, delay,
            )
        time.sleep(delay)
        delay = min(delay * 2, 60)


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
    config = types.GenerateContentConfig(
        system_instruction=ANALYST_PERSONA,
        temperature=0.3,
        response_mime_type="application/json",
    )
    log.info("Calling Gemini (%s) with %d headlines", GEMINI_MODEL, len(headlines))
    response = _generate_with_retry(client, prompt, config)

    raw = getattr(response, "text", "") or ""
    parsed = _extract_json_array(raw)
    classified = _normalize(parsed, headlines)
    log.info("Gemini returned %d valid classified items", len(classified))
    return classified
