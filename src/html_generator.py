"""Render the GitHub Pages dashboard (index.html) from market updates."""
from __future__ import annotations

import html
import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from .config import FEED_DESCRIPTION, FEED_TITLE, GOATCOUNTER_CODE
from .tags import tag_vocabulary

log = logging.getLogger(__name__)

CATEGORY_ORDER = ("Good", "Bad", "Ugly")

# All static UI text in both languages. Content from Gemini (summary,
# description, reason) is bilingual per-row via the BN columns.
UI = {
    "en": {
        "title":        FEED_TITLE,
        "lede":         FEED_DESCRIPTION,
        "last_updated": "Last updated:",
        "rss":          "RSS feed",
        "total":        "Total updates",
        "Good":         "Bullish",
        "Bad":          "Bearish",
        "Ugly":         "Crisis",
        "items":        "items",
        "item":         "item",
        "why":          "Why this matters",
        "source":       "Read source &rarr;",
        "footer_gen":   "Generated automatically",
        "footer_src":   "Source on GitHub",
        "empty":        "No classified items yet — check back soon.",
        "filters":      "Filters",
        "search_ph":    "Search headlines, descriptions, tickers…",
        "date_label":   "Date",
        "date_any":     "Any date",
        "ticker_ph":    "Ticker (e.g. DBH)",
        "all":          "All",
        "tags_label":   "Tags",
        "reset":        "Reset",
        "showing":      "Showing",
        "of":           "of",
        "results":      "results",
        "result":       "result",
        "no_match":     "No items match these filters.",
        "loading":      "Loading history…",
        "blurbs": {
            "Good": "High dividends, structural reforms, strong earnings.",
            "Bad":  "Inflation, weak earnings, index drops, regulatory hits.",
            "Ugly": "Systemic shocks: bank failures, energy crises, defaults.",
        },
    },
    "bn": {
        "title":        "বাংলাদেশ পুঁজিবাজার ও মুদ্রাবাজার — দৈনিক পাল্স",
        "lede":         "ঢাকা স্টক এক্সচেঞ্জ ও বাংলাদেশের মুদ্রাবাজারের অগ্রগতির এআই-নির্বাচিত দৈনিক ভালো / খারাপ / ভয়াবহ মূল্যায়ন।",
        "last_updated": "সর্বশেষ আপডেট:",
        "rss":          "RSS ফিড",
        "total":        "মোট আপডেট",
        "Good":         "তেজি",
        "Bad":          "মন্দা",
        "Ugly":         "সংকট",
        "items":        "টি",
        "item":         "টি",
        "why":          "কেন এটি গুরুত্বপূর্ণ",
        "source":       "মূল উৎস &rarr;",
        "footer_gen":   "স্বয়ংক্রিয়ভাবে তৈরি",
        "footer_src":   "GitHub-এ সোর্স",
        "empty":        "এখনও কোনো শ্রেণিবদ্ধ আইটেম নেই — শীঘ্রই আবার দেখুন।",
        "blurbs": {
            "Good": "উচ্চ লভ্যাংশ, কাঠামোগত সংস্কার, শক্তিশালী আয়।",
            "Bad":  "মূল্যস্ফীতি, দুর্বল আয়, সূচকের পতন, নিয়ন্ত্রক আঘাত।",
            "Ugly": "ব্যবস্থাগত সংকট: ব্যাংক ব্যর্থতা, শক্তি সংকট, খেলাপি।",
        },
        "filters":      "ফিল্টার",
        "search_ph":    "শিরোনাম, বর্ণনা, টিকার খুঁজুন…",
        "date_label":   "তারিখ",
        "date_any":     "যেকোনো তারিখ",
        "ticker_ph":    "টিকার (যেমন DBH)",
        "all":          "সব",
        "tags_label":   "ট্যাগ",
        "reset":        "রিসেট",
        "showing":      "দেখানো হচ্ছে",
        "of":           "এর মধ্যে",
        "results":      "টি ফলাফল",
        "result":       "টি ফলাফল",
        "no_match":     "এই ফিল্টারে কোনো আইটেম মেলেনি।",
        "loading":      "ইতিহাস লোড হচ্ছে…",
    },
}

# DSE-style "TICKER: rest of headline". Allow short names with spaces/dots.
_TICKER_RE = re.compile(r"^([A-Z0-9][A-Z0-9 .&-]{1,18}):\s*(.+)$")


def _split_company(summary: str) -> Tuple[str, str]:
    m = _TICKER_RE.match(summary.strip())
    if not m:
        return "", summary.strip()
    return m.group(1).strip(), m.group(2).strip()


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _goatcounter_snippets(code: str) -> Tuple[str, str]:
    """Build the `<head>` tracking script and the top-bar visitor-count widget.

    Returns ("", "") when no GoatCounter code is configured, so the page
    renders identically to the pre-analytics version.
    """
    code = (code or "").strip()
    if not code:
        return "", ""
    safe = _esc(code)
    head = (
        f'<script data-goatcounter="https://{safe}.goatcounter.com/count" '
        f'async src="//gc.zgo.at/count.js"></script>'
    )
    # The widget is hidden until the fetcher succeeds, so a flaky network or
    # disabled public stats leaves the layout clean instead of showing dashes.
    widget = (
        '<div class="visitor-stats" id="visitor-stats" hidden>'
        '<span class="vs-cell"><strong id="vs-today">—</strong> '
        '<span data-lang="en">today</span><span data-lang="bn">আজ</span></span>'
        '<span class="vs-sep">·</span>'
        '<span class="vs-cell"><strong id="vs-total">—</strong> '
        '<span data-lang="en">total</span><span data-lang="bn">মোট</span></span>'
        '</div>'
        '<script>'
        '(function(){'
        f'var GC="{safe}";'
        'var base="https://"+GC+".goatcounter.com/counter";'
        'var today=new Date().toISOString().slice(0,10);'
        'Promise.all(['
        'fetch(base+"/TOTAL.json"),'
        'fetch(base+"/TOTAL.json?start="+today+"&end="+today)'
        ']).then(function(rs){'
        'if(!rs[0].ok||!rs[1].ok)throw 0;'
        'return Promise.all(rs.map(function(r){return r.json();}));'
        '}).then(function(d){'
        'document.getElementById("vs-total").textContent='
        'd[0].count_unique||d[0].count||"—";'
        'document.getElementById("vs-today").textContent='
        'd[1].count_unique||d[1].count||"0";'
        'document.getElementById("visitor-stats").hidden=false;'
        '}).catch(function(){});'
        '})();'
        '</script>'
    )
    return head, widget


def _bilingual(en: str, bn: str, tag: str = "span", cls: str = "") -> str:
    """Render the same field in both languages.

    The CSS rule on `body[data-lang=...]` hides whichever language is not
    active. If BN is missing we emit a single tag with `data-lang="any"`
    so it stays visible in both modes (graceful degradation for the
    pre-bilingual rows already in the DB).
    """
    en_clean = (en or "").strip()
    bn_clean = (bn or "").strip()
    cls_attr = f' class="{cls}"' if cls else ""
    if not bn_clean:
        return f'<{tag}{cls_attr} data-lang="any">{_esc(en_clean)}</{tag}>'
    return (
        f'<{tag}{cls_attr} data-lang="en">{_esc(en_clean)}</{tag}>'
        f'<{tag}{cls_attr} data-lang="bn">{_esc(bn_clean)}</{tag}>'
    )


def _ui_label(key: str) -> str:
    """Render a static UI string in both languages (inline spans)."""
    en = UI["en"][key]
    bn = UI["bn"][key]
    # UI strings are already trusted (we author them); skip _esc to keep
    # entities like &rarr; intact.
    return (
        f'<span data-lang="en">{en}</span>'
        f'<span data-lang="bn">{bn}</span>'
    )


def _count_label(n: int) -> str:
    """'5 items' / '5 টি' kind of bilingual count phrase."""
    en_word = UI["en"]["item" if n == 1 else "items"]
    bn_word = UI["bn"]["item" if n == 1 else "items"]
    return (
        f'<span data-lang="en">{n} {en_word}</span>'
        f'<span data-lang="bn">{n} {bn_word}</span>'
    )


def _render_tag_pills(tags: List[str]) -> str:
    """Render the per-card tag pills. Each is clickable to toggle the filter."""
    if not tags:
        return ""
    label_map = {row["slug"]: row for row in tag_vocabulary()}
    chips = []
    for slug in tags:
        meta = label_map.get(slug)
        if not meta:
            continue
        en = _esc(meta["en"])
        bn = _esc(meta["bn"])
        chips.append(
            f'<button type="button" class="tag-pill" data-tag="{_esc(slug)}">'
            f'<span data-lang="en">{en}</span>'
            f'<span data-lang="bn">{bn}</span>'
            f'</button>'
        )
    if not chips:
        return ""
    return f'<div class="card-tags">{"".join(chips)}</div>'


def _render_card(row: Dict[str, str]) -> str:
    category = row.get("category", "")
    summary = row.get("summary", "") or ""
    summary_bn = row.get("summary_bn", "") or ""
    description = (row.get("description") or row.get("reason") or "").strip()
    description_bn = (row.get("description_bn") or row.get("reason_bn") or "").strip()
    reason = (row.get("reason") or "").strip()
    reason_bn = (row.get("reason_bn") or "").strip()
    source_url = (row.get("source_url") or "").strip()
    date = row.get("date", "") or ""
    tags = row.get("tags") or []

    company, headline_en = _split_company(summary)
    # Bengali summary may or may not carry a ticker prefix; if BN is
    # present, use it as-is for the BN headline (the ticker is a Latin
    # token anyway and reads fine in Bangla copy).
    headline_bn = summary_bn

    company_html = (
        f'<span class="ticker" title="Company / instrument">{_esc(company)}</span>'
        if company else ""
    )
    src_html = (
        f'<a class="src" href="{_esc(source_url)}" target="_blank" '
        f'rel="noopener">{_ui_label("source")}</a>'
        if source_url else ""
    )
    tags_html = _render_tag_pills(tags)
    return (
        f'<article class="card cat-{category.lower()}">'
        f'<header>'
        f'<span class="badge">{_ui_label(category)}</span>'
        f'{company_html}'
        f'<time>{_esc(date)}</time>'
        f'</header>'
        f'{_bilingual(headline_en, headline_bn, tag="h3")}'
        f'{_bilingual(description, description_bn, tag="p", cls="desc")}'
        f'<p class="why">'
        f'<strong>{_ui_label("why")}</strong>'
        f'{_bilingual(reason, reason_bn, tag="span", cls="why-body")}'
        f'</p>'
        f'{tags_html}'
        f'{src_html}'
        f'</article>'
    )


def _render_filter_bar() -> str:
    """The filter UI rendered between stats and the groups.

    Tag chips are generated from the canonical vocabulary (so they stay
    consistent with what Gemini emits). The actual filtering happens in
    JS — see the page's <script> block.
    """
    chips = []
    for entry in tag_vocabulary():
        chips.append(
            f'<button type="button" class="filter-chip" '
            f'data-tag="{_esc(entry["slug"])}" '
            f'data-group="{_esc(entry["group"])}">'
            f'<span data-lang="en">{_esc(entry["en"])}</span>'
            f'<span data-lang="bn">{_esc(entry["bn"])}</span>'
            f'</button>'
        )
    chips_html = "".join(chips)
    return (
        '<section class="filter-bar" aria-label="Filters">'
        '  <div class="filter-row filter-row-top">'
        f'    <label class="filter-field filter-search">'
        f'      <span class="vh">{_ui_label("filters")}</span>'
        f'      <input type="search" id="f-search" autocomplete="off" '
        f'             placeholder="{UI["en"]["search_ph"]}" '
        f'             data-ph-en="{UI["en"]["search_ph"]}" '
        f'             data-ph-bn="{UI["bn"]["search_ph"]}" />'
        '    </label>'
        f'    <label class="filter-field">'
        f'      <span class="lbl">{_ui_label("date_label")}</span>'
        f'      <input type="date" id="f-date" />'
        '    </label>'
        f'    <label class="filter-field">'
        f'      <span class="vh">{_ui_label("ticker_ph")}</span>'
        f'      <input type="text" id="f-ticker" autocomplete="off" '
        f'             placeholder="{UI["en"]["ticker_ph"]}" '
        f'             data-ph-en="{UI["en"]["ticker_ph"]}" '
        f'             data-ph-bn="{UI["bn"]["ticker_ph"]}" />'
        '    </label>'
        f'    <button type="button" class="filter-reset" id="f-reset">'
        f'      {_ui_label("reset")}'
        '    </button>'
        '  </div>'
        '  <div class="filter-row filter-row-cat">'
        f'    <button type="button" class="cat-chip is-active" '
        f'            data-category="">'
        f'      {_ui_label("all")}'
        '    </button>'
        f'    <button type="button" class="cat-chip cat-good-chip" '
        f'            data-category="Good">{_ui_label("Good")}</button>'
        f'    <button type="button" class="cat-chip cat-bad-chip" '
        f'            data-category="Bad">{_ui_label("Bad")}</button>'
        f'    <button type="button" class="cat-chip cat-ugly-chip" '
        f'            data-category="Ugly">{_ui_label("Ugly")}</button>'
        '  </div>'
        '  <div class="filter-row filter-row-tags">'
        f'    <span class="filter-row-label">{_ui_label("tags_label")}</span>'
        f'    <div class="filter-chips">{chips_html}</div>'
        '  </div>'
        '  <p class="filter-status" id="f-status" hidden></p>'
        '</section>'
    )


def _render_section(category: str, rows: List[Dict[str, str]]) -> str:
    cards = "\n".join(_render_card(r) for r in rows)
    blurb_en = UI["en"]["blurbs"][category]
    blurb_bn = UI["bn"]["blurbs"][category]
    return (
        f'<section class="group group-{category.lower()}">'
        f'  <header class="group-head">'
        f'    <h2><span class="group-name">{_ui_label(category)}</span>'
        f'        <span class="count">{_count_label(len(rows))}</span></h2>'
        f'    {_bilingual(blurb_en, blurb_bn, tag="p", cls="group-blurb")}'
        f'  </header>'
        f'  <div class="cards">{cards}</div>'
        f'</section>'
    )


def write_html(items: List[Dict[str, str]], output_path: Path) -> Path:
    # Stat row reflects the latest day's activity, not the whole feed window.
    # Before, "Total updates" mirrored the page slice (capped at MAX_FEED_ITEMS
    # = 50) so it read a flat 50 every refresh regardless of how much news
    # actually broke that day. Now it shows today's bullish+bearish+crisis.
    latest_date = max((r.get("date") or "" for r in items), default="")
    today_rows = [r for r in items if (r.get("date") or "") == latest_date] if latest_date else []
    counts = Counter((r.get("category") or "") for r in today_rows)
    total = sum(counts.values())

    by_cat: Dict[str, List[Dict[str, str]]] = {c: [] for c in CATEGORY_ORDER}
    for r in items:
        cat = r.get("category", "")
        if cat in by_cat:
            by_cat[cat].append(r)

    last_updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rendered_groups = [
        _render_section(c, by_cat[c]) for c in CATEGORY_ORDER if by_cat[c]
    ]
    if rendered_groups:
        sections = '<div class="groups">' + "\n".join(rendered_groups) + "</div>"
    else:
        sections = (
            f'<p class="empty">'
            f'<span data-lang="en">{UI["en"]["empty"]}</span>'
            f'<span data-lang="bn">{UI["bn"]["empty"]}</span>'
            f'</p>'
        )

    head_title = f'{UI["en"]["title"]} | {UI["bn"]["title"]}'

    # Bilingual labels & tag metadata that the page JS needs at runtime.
    # JSON-embedded to avoid string-escaping headaches inside the template.
    js_payload = {
        "ui": {
            "en": {k: UI["en"][k] for k in (
                "Good", "Bad", "Ugly", "why", "source", "showing", "of",
                "results", "result", "no_match", "loading", "empty",
            )},
            "bn": {k: UI["bn"][k] for k in (
                "Good", "Bad", "Ugly", "why", "source", "showing", "of",
                "results", "result", "no_match", "loading", "empty",
            )},
        },
        "tag_vocabulary": tag_vocabulary(),
    }
    js_payload_json = json.dumps(js_payload, ensure_ascii=False)

    gc_head, gc_widget = _goatcounter_snippets(GOATCOUNTER_CODE)
    doc = _PAGE_TEMPLATE.format(
        head_title=_esc(head_title),
        title_en=_esc(UI["en"]["title"]),
        title_bn=_esc(UI["bn"]["title"]),
        lede_en=_esc(UI["en"]["lede"]),
        lede_bn=_esc(UI["bn"]["lede"]),
        last_updated_label=_ui_label("last_updated"),
        last_updated=_esc(last_updated),
        rss_label=_ui_label("rss"),
        total_label=_ui_label("total"),
        good_label=_ui_label("Good"),
        bad_label=_ui_label("Bad"),
        ugly_label=_ui_label("Ugly"),
        footer_gen=_ui_label("footer_gen"),
        footer_src=_ui_label("footer_src"),
        total=total,
        good=counts.get("Good", 0),
        bad=counts.get("Bad", 0),
        ugly=counts.get("Ugly", 0),
        filter_bar=_render_filter_bar(),
        sections=sections,
        goatcounter_head=gc_head,
        visitor_widget=gc_widget,
    )
    # JSON payload has literal {}s that would break str.format(); substitute
    # via sentinel after formatting. The </script> escape is a defense in
    # depth in case any field ever contains the literal string.
    safe_json = js_payload_json.replace("</", "<\\/")
    doc = doc.replace("__JS_PAYLOAD__", safe_json)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(doc, encoding="utf-8")
    log.info(
        "HTML dashboard written to %s (%d items rendered, %d today)",
        output_path, len(items), total,
    )
    return output_path


# Doubled braces `{{` / `}}` are literal CSS braces inside .format(...).
_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{head_title}</title>
  <link rel="alternate" type="application/rss+xml" title="{title_en}" href="feed.xml" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&amp;family=Geist:wght@300;400;500;600;700&amp;family=Noto+Serif+Bengali:wght@400;600;700&amp;family=Hind+Siliguri:wght@400;500;600;700&amp;display=swap" />
  {goatcounter_head}
  <style>
    /* ====================================================================
       Palette — warm editorial cream by day, deep ink at night. The brand
       leans on a single restrained accent (oxblood) and lets typography do
       the heavy lifting.
       ==================================================================== */
    :root {{
      --bg:        #f6f1e6;
      --bg-2:      #efe7d6;
      --paper:     #ffffff;
      --ink:       #1a140d;
      --ink-soft:  #3d3326;
      --muted:     #847559;
      --line:      #ddd0b3;
      --line-soft: #e9dec6;
      --good:      #146346;
      --good-soft: #d6ead9;
      --bad:       #a55408;
      --bad-soft:  #f3e0c4;
      --ugly:      #921a1a;
      --ugly-soft: #f3d2c8;
      --accent:    #1a140d;
      --rule:      rgba(26, 20, 13, .08);
      --shadow-sm: 0 1px 2px rgba(26,20,13,.05);
      --shadow-md: 0 1px 2px rgba(26,20,13,.05), 0 12px 28px -12px rgba(26,20,13,.15);
      --shadow-lg: 0 8px 40px -10px rgba(26,20,13,.25);
      color-scheme: light;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg:        #0d0a06;
        --bg-2:      #15110a;
        --paper:     #181308;
        --ink:       #f4e9d4;
        --ink-soft:  #d5c5a3;
        --muted:     #8d806a;
        --line:      #2a2113;
        --line-soft: #1d160c;
        --good:      #5ed099;
        --good-soft: #1a2e25;
        --bad:       #f6a559;
        --bad-soft:  #2d2114;
        --ugly:      #f08585;
        --ugly-soft: #2d1818;
        --accent:    #f4e9d4;
        --rule:      rgba(244,233,212,.07);
        --shadow-sm: none;
        --shadow-md: 0 12px 40px rgba(0,0,0,.5);
        --shadow-lg: 0 24px 80px rgba(0,0,0,.6);
        color-scheme: dark;
      }}
    }}

    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    html, body {{ margin:0; padding:0; }}

    body {{
      font-family: 'Geist', -apple-system, BlinkMacSystemFont, 'Segoe UI',
                   Roboto, sans-serif;
      font-size: 16.5px;
      line-height: 1.6;
      color: var(--ink);
      background:
        radial-gradient(ellipse 90rem 36rem at 100% -10%,
                        color-mix(in srgb, var(--bad) 8%, transparent),
                        transparent 60%),
        radial-gradient(ellipse 80rem 40rem at -10% 50%,
                        color-mix(in srgb, var(--good) 5%, transparent),
                        transparent 60%),
        var(--bg);
      background-attachment: fixed;
      min-height: 100vh;
      padding: 2.5rem 1.5rem 5rem;
      -webkit-font-smoothing: antialiased;
      text-rendering: optimizeLegibility;
    }}
    body[data-lang="bn"] {{
      font-family: 'Hind Siliguri', 'Noto Sans Bengali', system-ui, sans-serif;
    }}

    /* Bilingual show/hide — `any` rows stay visible in both modes. */
    body[data-lang="en"] [data-lang="bn"] {{ display: none !important; }}
    body[data-lang="bn"] [data-lang="en"] {{ display: none !important; }}

    .wrap {{ max-width: 1280px; margin: 0 auto; }}

    /* ====================================================================
       Masthead
       ==================================================================== */
    .top {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 1.5rem 2rem;
      align-items: end;
      padding-bottom: 1.75rem;
      border-bottom: 1px solid var(--line);
      position: relative;
    }}
    .top::after {{
      content: '';
      position: absolute;
      bottom: -3px; left: 0;
      width: 64px; height: 5px;
      background: var(--ugly);
      border-radius: 2px;
    }}
    @media (max-width: 720px) {{
      .top {{ grid-template-columns: 1fr; }}
    }}

    .brand h1 {{
      font-family: 'Instrument Serif', Georgia, 'Times New Roman', serif;
      font-weight: 400;
      font-size: clamp(2.1rem, 5.5vw, 3.8rem);
      line-height: 1;
      letter-spacing: -.025em;
      margin: 0;
      color: var(--ink);
    }}
    body[data-lang="bn"] .brand h1 {{
      font-family: 'Noto Serif Bengali', 'Hind Siliguri', serif;
      font-size: clamp(1.6rem, 4.5vw, 2.8rem);
      line-height: 1.25;
      letter-spacing: -.01em;
    }}
    .brand .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: .5rem;
      color: var(--muted);
      font-size: .68rem;
      font-weight: 700;
      letter-spacing: .22em;
      text-transform: uppercase;
      font-variant-numeric: tabular-nums;
      margin: 0 0 .85rem;
    }}
    .brand .sub {{
      margin: 1rem 0 0;
      color: var(--muted);
      font-size: .78rem;
      letter-spacing: .04em;
      font-variant-numeric: tabular-nums;
      display: inline-flex;
      align-items: center;
      gap: .55rem;
    }}
    .live-dot {{
      display: inline-block;
      width: 8px; height: 8px;
      background: var(--good);
      border-radius: 50%;
      position: relative;
      flex: 0 0 auto;
    }}
    .live-dot::after {{
      content: '';
      position: absolute;
      inset: -3px;
      border-radius: 50%;
      background: var(--good);
      opacity: .4;
      animation: pulse 2.4s cubic-bezier(.4,0,.6,1) infinite;
    }}
    @keyframes pulse {{
      0%, 100% {{ transform: scale(1); opacity: .4; }}
      70%      {{ transform: scale(2.6); opacity: 0; }}
    }}

    .top-actions {{
      display: flex;
      gap: .45rem;
      align-items: center;
      flex-wrap: wrap;
    }}

    /* Sliding language switch */
    .lang-switch {{
      display: inline-flex;
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 3px;
      position: relative;
      box-shadow: var(--shadow-sm);
    }}
    .lang-switch::before {{
      content: '';
      position: absolute;
      top: 3px; bottom: 3px;
      left: 3px;
      width: calc(50% - 3px);
      background: var(--ink);
      border-radius: 999px;
      transition: transform .35s cubic-bezier(.65,.05,.36,1);
      z-index: 0;
    }}
    body[data-lang="bn"] .lang-switch::before {{ transform: translateX(100%); }}
    .lang-switch button {{
      position: relative;
      z-index: 1;
      background: transparent;
      color: var(--muted);
      border: 0;
      padding: .35rem .9rem;
      font: inherit;
      font-family: 'Geist', sans-serif;
      font-size: .76rem;
      font-weight: 700;
      letter-spacing: .04em;
      cursor: pointer;
      border-radius: 999px;
      transition: color .35s ease;
      line-height: 1;
    }}
    body[data-lang="en"] .lang-switch [data-set-lang="en"],
    body[data-lang="bn"] .lang-switch [data-set-lang="bn"] {{
      color: var(--bg);
    }}

    .rss {{
      display: inline-flex;
      align-items: center;
      gap: .4rem;
      font-size: .78rem;
      font-weight: 500;
      padding: .5rem .95rem;
      border-radius: 999px;
      border: 1px solid var(--line);
      text-decoration: none;
      color: var(--ink);
      background: var(--paper);
      box-shadow: var(--shadow-sm);
      transition: all .2s ease;
    }}
    .rss::before {{
      content: '';
      display: inline-block;
      width: 9px; height: 9px;
      border-radius: 50%;
      background: var(--bad);
      box-shadow:
        inset 0 0 0 2px var(--paper),
        0 0 0 1px var(--bad);
    }}
    .rss:hover {{
      transform: translateY(-1px);
      box-shadow: var(--shadow-md);
      border-color: var(--ink);
    }}

    .visitor-stats {{
      display: inline-flex;
      align-items: center;
      gap: .5rem;
      font-size: .76rem;
      color: var(--muted);
      padding: .5rem .9rem;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--paper);
      font-variant-numeric: tabular-nums;
      line-height: 1;
      box-shadow: var(--shadow-sm);
    }}
    .visitor-stats strong {{ color: var(--ink); font-weight: 700; }}
    .visitor-stats .vs-sep {{ opacity: .35; }}

    .lede {{
      margin: 1.6rem 0 0;
      max-width: 58ch;
      color: var(--ink-soft);
      font-size: 1.08rem;
      line-height: 1.55;
    }}

    /* ====================================================================
       Stat strip — four cards sharing a single hairline grid
       ==================================================================== */
    .stats {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 1px;
      background: var(--line);
      border: 1px solid var(--line);
      border-radius: 18px;
      overflow: hidden;
      margin: 2.5rem 0 2.25rem;
      box-shadow: var(--shadow-md);
    }}
    @media (max-width: 720px) {{
      .stats {{ grid-template-columns: repeat(2, 1fr); }}
    }}
    .stat {{
      background: var(--paper);
      padding: 1.5rem 1.4rem 1.4rem;
      position: relative;
      overflow: hidden;
      transition: background .25s ease;
    }}
    .stat::before {{
      content: '';
      position: absolute;
      top: 0; left: 0; right: 0;
      height: 3px;
      background: var(--ink);
      transform: scaleX(0);
      transform-origin: left center;
      transition: transform .35s cubic-bezier(.65,.05,.36,1);
    }}
    .stat.good::before {{ background: var(--good); }}
    .stat.bad::before  {{ background: var(--bad); }}
    .stat.ugly::before {{ background: var(--ugly); }}
    .stat:hover::before {{ transform: scaleX(1); }}
    .stat:hover {{ background: color-mix(in srgb, var(--paper) 92%, var(--bg)); }}

    .stat .num {{
      display: block;
      font-family: 'Instrument Serif', Georgia, serif;
      font-size: clamp(2.6rem, 5.5vw, 3.6rem);
      line-height: .95;
      letter-spacing: -.045em;
      font-weight: 400;
      font-variant-numeric: tabular-nums;
      color: var(--ink);
    }}
    .stat.good .num {{ color: var(--good); }}
    .stat.bad  .num {{ color: var(--bad); }}
    .stat.ugly .num {{ color: var(--ugly); }}

    .stat .label {{
      margin-top: .65rem;
      font-size: .67rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .16em;
      font-weight: 700;
      font-family: 'Geist', sans-serif;
    }}
    .stat .glyph {{
      position: absolute;
      top: 1.2rem; right: 1.3rem;
      width: 6px; height: 6px;
      border-radius: 50%;
      background: currentColor;
      opacity: .2;
    }}
    .stat.good {{ color: var(--good); }}
    .stat.bad  {{ color: var(--bad); }}
    .stat.ugly {{ color: var(--ugly); }}

    /* ====================================================================
       Filter bar — sticky, with backdrop blur
       ==================================================================== */
    .filter-bar {{
      position: sticky;
      top: .85rem;
      z-index: 30;
      background: color-mix(in srgb, var(--paper) 88%, transparent);
      backdrop-filter: saturate(140%) blur(10px);
      -webkit-backdrop-filter: saturate(140%) blur(10px);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: .9rem 1rem;
      margin: 0 0 2rem;
      display: flex;
      flex-direction: column;
      gap: .7rem;
      box-shadow: var(--shadow-sm);
      transition: box-shadow .25s ease, border-color .25s ease;
    }}
    .filter-bar.is-active {{
      border-color: var(--ink);
      box-shadow: var(--shadow-md);
    }}

    .filter-row {{
      display: flex;
      flex-wrap: wrap;
      gap: .45rem;
      align-items: center;
    }}
    .filter-row-top {{ gap: .5rem; }}
    .filter-row-label {{
      font-size: .62rem;
      color: var(--muted);
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .14em;
      font-family: 'Geist', sans-serif;
      margin-right: .2rem;
    }}

    .filter-field {{
      display: flex;
      flex-direction: column;
      gap: .15rem;
      flex: 0 0 auto;
    }}
    .filter-field.filter-search {{
      flex: 1 1 240px;
      min-width: 180px;
      position: relative;
    }}
    .filter-field.filter-search::before {{
      content: '';
      position: absolute;
      left: .9rem;
      top: 50%;
      width: 13px; height: 13px;
      transform: translateY(-50%);
      border: 1.5px solid var(--muted);
      border-radius: 50%;
      pointer-events: none;
    }}
    .filter-field.filter-search::after {{
      content: '';
      position: absolute;
      left: 1.55rem;
      top: 58%;
      width: 5px; height: 1.5px;
      background: var(--muted);
      transform: rotate(45deg);
      pointer-events: none;
    }}
    .filter-search input {{ padding-left: 2.25rem !important; }}

    .filter-field .lbl {{
      font-size: .58rem;
      color: var(--muted);
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .14em;
      font-family: 'Geist', sans-serif;
    }}
    .vh {{
      position: absolute !important;
      width: 1px; height: 1px; padding: 0; margin: -1px;
      overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0;
    }}

    .filter-bar input[type="search"],
    .filter-bar input[type="text"],
    .filter-bar input[type="date"] {{
      font: inherit;
      font-family: 'Geist', sans-serif;
      font-size: .9rem;
      padding: .55rem .85rem;
      border-radius: 10px;
      border: 1px solid var(--line);
      background: var(--bg);
      color: var(--ink);
      min-width: 9rem;
      transition: all .2s ease;
    }}
    .filter-bar input:focus {{
      outline: none;
      border-color: var(--ink);
      background: var(--paper);
      box-shadow: 0 0 0 3px color-mix(in srgb, var(--ink) 12%, transparent);
    }}

    .filter-reset {{
      font: inherit;
      font-family: 'Geist', sans-serif;
      font-size: .72rem;
      font-weight: 700;
      padding: .55rem .9rem;
      border-radius: 10px;
      cursor: pointer;
      background: transparent;
      color: var(--muted);
      border: 1px solid var(--line);
      margin-left: auto;
      transition: all .2s ease;
      text-transform: uppercase;
      letter-spacing: .1em;
    }}
    .filter-reset:hover {{ color: var(--ink); border-color: var(--ink); }}

    .cat-chip, .filter-chip {{
      font: inherit;
      font-family: 'Geist', sans-serif;
      font-size: .76rem;
      font-weight: 600;
      padding: .4rem .9rem;
      border-radius: 999px;
      cursor: pointer;
      background: transparent;
      color: var(--ink);
      border: 1px solid var(--line);
      line-height: 1;
      transition: all .18s cubic-bezier(.4,0,.2,1);
    }}
    .cat-chip:hover, .filter-chip:hover {{
      border-color: var(--ink);
      transform: translateY(-1px);
    }}
    .cat-chip.is-active {{
      background: var(--ink); color: var(--bg); border-color: var(--ink);
    }}
    .cat-chip.cat-good-chip.is-active {{ background: var(--good); border-color: var(--good); color: var(--paper); }}
    .cat-chip.cat-bad-chip.is-active  {{ background: var(--bad);  border-color: var(--bad);  color: var(--paper); }}
    .cat-chip.cat-ugly-chip.is-active {{ background: var(--ugly); border-color: var(--ugly); color: var(--paper); }}
    .filter-chip.is-active {{
      background: var(--ink); color: var(--bg); border-color: var(--ink);
    }}
    .filter-chips {{ display: flex; flex-wrap: wrap; gap: .35rem; }}

    .filter-status {{
      margin: .25rem 0 0;
      font-size: .78rem;
      color: var(--muted);
      font-style: italic;
    }}

    .kbd {{
      display: inline-block;
      padding: 1px 6px;
      border: 1px solid var(--line);
      border-bottom-width: 2px;
      border-radius: 4px;
      background: var(--bg);
      font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
      font-size: .68rem;
      line-height: 1.2;
      color: var(--muted);
      font-weight: 600;
    }}

    /* ====================================================================
       Default 3-column groups view
       ==================================================================== */
    .groups {{
      display: grid;
      gap: 1.5rem;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      align-items: start;
    }}
    @media (max-width: 1100px) {{
      .groups {{ grid-template-columns: repeat(2, 1fr); }}
    }}
    @media (max-width: 720px) {{
      .groups {{ grid-template-columns: 1fr; }}
    }}
    .group {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 1.2rem 1.2rem 1.4rem;
      display: flex;
      flex-direction: column;
      min-width: 0;
      box-shadow: var(--shadow-sm);
    }}
    .group-head {{
      display: flex;
      align-items: baseline;
      gap: .6rem;
      flex-wrap: wrap;
      padding: 0 0 .9rem;
      margin: 0 0 1rem;
      border-bottom: 1px solid var(--line-soft);
      position: relative;
    }}
    .group-head::after {{
      content: '';
      position: absolute;
      bottom: -1px; left: 0;
      width: 48px; height: 2px;
      border-radius: 1px;
    }}
    .group-good .group-head::after {{ background: var(--good); }}
    .group-bad  .group-head::after {{ background: var(--bad); }}
    .group-ugly .group-head::after {{ background: var(--ugly); }}

    .group-head h2 {{
      margin: 0;
      display: inline-flex;
      align-items: baseline;
      gap: .55rem;
      font-size: 1rem;
      font-weight: 600;
      font-family: 'Geist', sans-serif;
    }}
    .group-name {{
      font-family: 'Instrument Serif', Georgia, serif;
      font-style: italic;
      font-weight: 400;
      font-size: 1.75rem;
      letter-spacing: -.01em;
      line-height: 1;
    }}
    body[data-lang="bn"] .group-name {{
      font-family: 'Noto Serif Bengali', 'Hind Siliguri', serif;
      font-style: normal;
      font-size: 1.4rem;
    }}
    .group-good .group-name {{ color: var(--good); }}
    .group-bad  .group-name {{ color: var(--bad); }}
    .group-ugly .group-name {{ color: var(--ugly); }}

    .count {{
      color: var(--muted);
      font-weight: 500;
      font-size: .75rem;
      font-variant-numeric: tabular-nums;
      font-family: 'Geist', sans-serif;
    }}
    .group-blurb {{
      color: var(--muted);
      margin: 0;
      font-size: .78rem;
      flex: 1 1 100%;
      line-height: 1.5;
    }}

    /* ====================================================================
       Card
       ==================================================================== */
    .cards {{ display: flex; flex-direction: column; gap: .9rem; }}
    .card {{
      background: var(--bg-2);
      border: 1px solid var(--line-soft);
      border-radius: 12px;
      padding: 1rem 1.1rem 1.05rem;
      display: flex;
      flex-direction: column;
      gap: .55rem;
      transition: transform .25s cubic-bezier(.4,0,.2,1),
                  box-shadow .25s cubic-bezier(.4,0,.2,1),
                  border-color .25s ease,
                  background .25s ease;
      position: relative;
    }}
    @media (prefers-color-scheme: dark) {{
      .card {{ background: color-mix(in srgb, var(--paper) 85%, var(--bg)); }}
    }}
    .card:hover {{
      transform: translateY(-2px);
      box-shadow: var(--shadow-md);
      border-color: var(--ink);
      background: var(--paper);
    }}
    .cat-good {{ box-shadow: inset 3px 0 0 var(--good); }}
    .cat-bad  {{ box-shadow: inset 3px 0 0 var(--bad);  }}
    .cat-ugly {{ box-shadow: inset 3px 0 0 var(--ugly); }}
    .cat-good:hover {{ box-shadow: inset 3px 0 0 var(--good), var(--shadow-md); }}
    .cat-bad:hover  {{ box-shadow: inset 3px 0 0 var(--bad),  var(--shadow-md); }}
    .cat-ugly:hover {{ box-shadow: inset 3px 0 0 var(--ugly), var(--shadow-md); }}

    .card header {{
      display: flex;
      align-items: center;
      gap: .45rem;
      flex-wrap: wrap;
      margin-bottom: 0;
    }}
    .badge {{
      font-size: .58rem;
      font-weight: 800;
      padding: .15rem .5rem;
      border-radius: 4px;
      text-transform: uppercase;
      letter-spacing: .12em;
      color: var(--paper);
      font-family: 'Geist', sans-serif;
      line-height: 1.4;
    }}
    .cat-good .badge {{ background: var(--good); }}
    .cat-bad  .badge {{ background: var(--bad); }}
    .cat-ugly .badge {{ background: var(--ugly); }}
    .ticker {{
      font-family: 'Geist', ui-monospace, monospace;
      font-weight: 700;
      font-size: .7rem;
      padding: .12rem .5rem;
      border-radius: 4px;
      background: var(--paper);
      color: var(--ink);
      border: 1px solid var(--line);
      letter-spacing: .04em;
    }}
    .card time {{
      margin-left: auto;
      color: var(--muted);
      font-size: .7rem;
      font-variant-numeric: tabular-nums;
      font-family: 'Geist', sans-serif;
    }}
    .card h3 {{
      margin: .1rem 0 0;
      font-family: 'Instrument Serif', Georgia, serif;
      font-weight: 400;
      font-size: 1.25rem;
      line-height: 1.25;
      letter-spacing: -.012em;
      color: var(--ink);
    }}
    body[data-lang="bn"] .card h3 {{
      font-family: 'Noto Serif Bengali', 'Hind Siliguri', serif;
      font-size: 1.1rem;
      line-height: 1.5;
      letter-spacing: 0;
    }}
    .desc {{
      margin: 0;
      font-size: .9rem;
      line-height: 1.6;
      color: var(--ink-soft);
    }}
    .why {{
      margin: .15rem 0 0;
      padding: .65rem .8rem;
      border-radius: 8px;
      font-size: .82rem;
      line-height: 1.55;
      background: var(--paper);
      border-left: 2px solid var(--accent);
    }}
    .cat-good .why {{ border-left-color: var(--good); background: var(--good-soft); }}
    .cat-bad  .why {{ border-left-color: var(--bad);  background: var(--bad-soft); }}
    .cat-ugly .why {{ border-left-color: var(--ugly); background: var(--ugly-soft); }}
    .why strong {{
      display: block;
      font-size: .56rem;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: .16em;
      color: var(--muted);
      margin-bottom: .3rem;
      font-family: 'Geist', sans-serif;
    }}
    .why-body {{ color: var(--ink); }}

    .card-tags {{
      display: flex;
      flex-wrap: wrap;
      gap: .3rem;
      margin: .1rem 0 0;
    }}
    .tag-pill {{
      font: inherit;
      font-family: 'Geist', sans-serif;
      font-size: .68rem;
      font-weight: 500;
      line-height: 1;
      padding: .28rem .55rem;
      border-radius: 999px;
      cursor: pointer;
      background: transparent;
      color: var(--muted);
      border: 1px solid var(--line);
      white-space: nowrap;
      transition: all .15s ease;
    }}
    .tag-pill:hover {{
      color: var(--ink);
      border-color: var(--ink);
      transform: translateY(-1px);
    }}

    .src {{
      margin-top: .25rem;
      font-size: .76rem;
      color: var(--muted);
      text-decoration: none;
      font-weight: 600;
      align-self: flex-start;
      transition: all .15s ease;
      font-family: 'Geist', sans-serif;
      display: inline-flex;
      align-items: center;
      gap: .25rem;
    }}
    .src::after {{
      content: '→';
      transition: transform .2s ease;
      display: inline-block;
      font-weight: 400;
    }}
    .src:hover {{ color: var(--ink); }}
    .src:hover::after {{ transform: translateX(3px); }}

    /* ====================================================================
       Filtered view (JS-rendered, flat 3-col grid)
       ==================================================================== */
    #filtered-view {{
      display: grid;
      gap: 1rem;
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }}
    @media (max-width: 1100px) {{ #filtered-view {{ grid-template-columns: repeat(2, 1fr); }} }}
    @media (max-width: 720px) {{ #filtered-view {{ grid-template-columns: 1fr; }} }}
    #filtered-view .card {{ background: var(--paper); }}
    #filtered-empty {{
      grid-column: 1 / -1;
      background: var(--paper);
      border: 1px dashed var(--line);
      padding: 3rem 2rem;
      border-radius: 16px;
      text-align: center;
      color: var(--muted);
      font-style: italic;
    }}

    .empty {{
      background: var(--paper);
      border: 1px dashed var(--line);
      padding: 3rem 2rem;
      border-radius: 16px;
      text-align: center;
      color: var(--muted);
      font-style: italic;
    }}

    /* ====================================================================
       Footer
       ==================================================================== */
    footer.foot {{
      text-align: center;
      color: var(--muted);
      margin-top: 4rem;
      padding-top: 2rem;
      border-top: 1px solid var(--line);
      font-size: .82rem;
      font-family: 'Geist', sans-serif;
    }}
    footer.foot a {{
      color: var(--ink);
      text-decoration: none;
      border-bottom: 1px dashed var(--muted);
    }}
    footer.foot a:hover {{ border-bottom-style: solid; }}

    /* ====================================================================
       Entrance animations — once, fast, polite
       ==================================================================== */
    @keyframes riseIn {{
      from {{ opacity: 0; transform: translateY(10px); }}
      to   {{ opacity: 1; transform: translateY(0); }}
    }}
    .stats .stat,
    .groups .group,
    .cards .card {{
      animation: riseIn .55s cubic-bezier(.2,.7,.2,1) backwards;
    }}
    .stats .stat:nth-child(1) {{ animation-delay: .02s; }}
    .stats .stat:nth-child(2) {{ animation-delay: .08s; }}
    .stats .stat:nth-child(3) {{ animation-delay: .14s; }}
    .stats .stat:nth-child(4) {{ animation-delay: .20s; }}
    .groups .group:nth-child(1) {{ animation-delay: .22s; }}
    .groups .group:nth-child(2) {{ animation-delay: .28s; }}
    .groups .group:nth-child(3) {{ animation-delay: .34s; }}
    .cards .card:nth-child(1) {{ animation-delay: .30s; }}
    .cards .card:nth-child(2) {{ animation-delay: .35s; }}
    .cards .card:nth-child(3) {{ animation-delay: .40s; }}
    .cards .card:nth-child(n+4) {{ animation-delay: .45s; }}

    @media (prefers-reduced-motion: reduce) {{
      *, *::before, *::after {{
        animation-duration: .01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: .01ms !important;
      }}
      html {{ scroll-behavior: auto; }}
    }}
  </style>
</head>
<body data-lang="en">
  <div class="wrap">
    <header class="top">
      <div class="brand">
        <p class="eyebrow">
          <span data-lang="en">Bangladesh &middot; Daily Pulse</span>
          <span data-lang="bn">বাংলাদেশ &middot; দৈনিক পাল্স</span>
        </p>
        <h1>
          <span data-lang="en">{title_en}</span>
          <span data-lang="bn">{title_bn}</span>
        </h1>
        <p class="sub">
          <span class="live-dot" aria-hidden="true"></span>
          {last_updated_label} {last_updated}
        </p>
      </div>
      <div class="top-actions">
        <div class="lang-switch" role="group" aria-label="Language">
          <button type="button" data-set-lang="en">EN</button>
          <button type="button" data-set-lang="bn">বাং</button>
        </div>
        <a class="rss" href="feed.xml">{rss_label}</a>
        {visitor_widget}
      </div>
    </header>

    <p class="lede">
      <span data-lang="en">{lede_en}</span>
      <span data-lang="bn">{lede_bn}</span>
    </p>

    <div class="stats">
      <div class="stat total">
        <span class="glyph" aria-hidden="true"></span>
        <span class="num" data-count="{total}">{total}</span>
        <div class="label">{total_label}</div>
      </div>
      <div class="stat good">
        <span class="glyph" aria-hidden="true"></span>
        <span class="num" data-count="{good}">{good}</span>
        <div class="label">{good_label}</div>
      </div>
      <div class="stat bad">
        <span class="glyph" aria-hidden="true"></span>
        <span class="num" data-count="{bad}">{bad}</span>
        <div class="label">{bad_label}</div>
      </div>
      <div class="stat ugly">
        <span class="glyph" aria-hidden="true"></span>
        <span class="num" data-count="{ugly}">{ugly}</span>
        <div class="label">{ugly_label}</div>
      </div>
    </div>

    {filter_bar}

    <div id="default-view">
      {sections}
    </div>
    <div id="filtered-view" hidden></div>

    <footer class="foot">
      {footer_gen} &middot; <a href="feed.xml">RSS</a> &middot;
      <a href="https://github.com/alaminmain/BdCapitalMarketNews">{footer_src}</a>
      &middot; <span class="kbd">/</span> to search &middot;
      <span class="kbd">Esc</span> to reset
    </footer>
  </div>
  <script type="application/json" id="ui-payload">__JS_PAYLOAD__</script>
  <script>
    (function() {{
      var payload = JSON.parse(document.getElementById('ui-payload').textContent);
      var UI = payload.ui;
      var TAG_VOCAB = payload.tag_vocabulary;
      var TAG_LABELS = {{}};
      TAG_VOCAB.forEach(function(t) {{ TAG_LABELS[t.slug] = t; }});

      // --- Count-up animation for stat numbers ---
      // Cubic ease-out from 0 → target over ~900ms. Runs once on load.
      // Honors prefers-reduced-motion by snapping to the final value.
      var reduceMotion = window.matchMedia
        && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      function animateCount(el) {{
        var target = parseInt(el.dataset.count || el.textContent || '0', 10);
        if (!target || target < 1 || reduceMotion) {{
          el.textContent = target || 0;
          return;
        }}
        el.textContent = '0';
        var duration = 900;
        var start = performance.now();
        function tick(now) {{
          var t = Math.min(1, (now - start) / duration);
          var eased = 1 - Math.pow(1 - t, 3);
          el.textContent = Math.round(target * eased);
          if (t < 1) requestAnimationFrame(tick);
        }}
        requestAnimationFrame(tick);
      }}
      document.querySelectorAll('.stat .num').forEach(animateCount);

      // --- Language switching ---
      var LANG_KEY = 'bd-pulse-lang';
      var saved = null;
      try {{ saved = localStorage.getItem(LANG_KEY); }} catch (e) {{}}
      var initialLang = saved
        || ((navigator.language || '').toLowerCase().indexOf('bn') === 0 ? 'bn' : 'en');
      setLang(initialLang);

      function setLang(l) {{
        document.body.dataset.lang = l;
        document.documentElement.lang = l;
        // Swap placeholders on filter inputs to match the active language.
        document.querySelectorAll('input[data-ph-en]').forEach(function(el) {{
          el.placeholder = el.getAttribute('data-ph-' + l) || el.placeholder;
        }});
      }}

      // --- Filter state ---
      var state = {{
        q: '', date: '', category: '', tags: new Set(), ticker: ''
      }};
      var historyCache = null;
      var historyLoading = null;

      var $search = document.getElementById('f-search');
      var $date = document.getElementById('f-date');
      var $ticker = document.getElementById('f-ticker');
      var $status = document.getElementById('f-status');
      var $defaultView = document.getElementById('default-view');
      var $filteredView = document.getElementById('filtered-view');
      var $filterBar = document.querySelector('.filter-bar');

      function isActive() {{
        return !!(state.q || state.date || state.category
                  || state.tags.size || state.ticker);
      }}

      function syncFilterBarHighlight() {{
        if ($filterBar) $filterBar.classList.toggle('is-active', isActive());
      }}

      function ensureHistory() {{
        if (historyCache) return Promise.resolve(historyCache);
        if (historyLoading) return historyLoading;
        historyLoading = fetch('history.json', {{ cache: 'no-cache' }})
          .then(function(r) {{
            if (!r.ok) throw new Error('history.json HTTP ' + r.status);
            return r.json();
          }})
          .then(function(j) {{ historyCache = j; return j; }})
          .catch(function(err) {{ historyLoading = null; throw err; }});
        return historyLoading;
      }}

      function setStatus(text) {{
        if (!text) {{ $status.hidden = true; $status.textContent = ''; }}
        else {{ $status.hidden = false; $status.textContent = text; }}
      }}

      function escapeHtml(s) {{
        return String(s == null ? '' : s)
          .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
      }}

      function splitCompany(summary) {{
        var m = (summary || '').match(/^([A-Z0-9][A-Z0-9 .&-]{{1,18}}):\\s*(.+)$/);
        if (!m) return {{ ticker: '', rest: summary || '' }};
        return {{ ticker: m[1].trim(), rest: m[2].trim() }};
      }}

      function bilingual(en, bn, tag, cls) {{
        tag = tag || 'span';
        var c = cls ? ' class="' + cls + '"' : '';
        if (!bn) return '<' + tag + c + ' data-lang="any">' + escapeHtml(en) + '</' + tag + '>';
        return '<' + tag + c + ' data-lang="en">' + escapeHtml(en) + '</' + tag + '>'
             + '<' + tag + c + ' data-lang="bn">' + escapeHtml(bn) + '</' + tag + '>';
      }}

      function uiBoth(key) {{
        // UI strings author-controlled so we don't escape (preserves &rarr; etc).
        return '<span data-lang="en">' + UI.en[key] + '</span>'
             + '<span data-lang="bn">' + UI.bn[key] + '</span>';
      }}

      function renderTagPills(tags) {{
        if (!tags || !tags.length) return '';
        var pills = tags.map(function(slug) {{
          var meta = TAG_LABELS[slug];
          if (!meta) return '';
          return '<button type="button" class="tag-pill" data-tag="'
            + escapeHtml(slug) + '">'
            + '<span data-lang="en">' + escapeHtml(meta.en) + '</span>'
            + '<span data-lang="bn">' + escapeHtml(meta.bn) + '</span>'
            + '</button>';
        }}).join('');
        return pills ? '<div class="card-tags">' + pills + '</div>' : '';
      }}

      function renderCard(r) {{
        var cat = r.category || '';
        var split = splitCompany(r.summary || '');
        var company = split.ticker || r.ticker || '';
        var headlineEn = split.rest;
        var headlineBn = r.summary_bn || '';
        var desc = r.description || r.reason || '';
        var descBn = r.description_bn || r.reason_bn || '';
        var reason = r.reason || '';
        var reasonBn = r.reason_bn || '';
        var source = r.source_url || '';
        var date = r.date || '';

        var srcHtml = source
          ? '<a class="src" href="' + escapeHtml(source)
            + '" target="_blank" rel="noopener">'
            + '<span data-lang="en">' + UI.en.source + '</span>'
            + '<span data-lang="bn">' + UI.bn.source + '</span></a>'
          : '';

        return '<article class="card cat-' + cat.toLowerCase() + '">'
          + '<header>'
          +   '<span class="badge">' + uiBoth(cat) + '</span>'
          +   (company ? '<span class="ticker">' + escapeHtml(company) + '</span>' : '')
          +   '<time>' + escapeHtml(date) + '</time>'
          + '</header>'
          + bilingual(headlineEn, headlineBn, 'h3')
          + bilingual(desc, descBn, 'p', 'desc')
          + '<p class="why"><strong>' + uiBoth('why') + '</strong>'
          +   bilingual(reason, reasonBn, 'span', 'why-body')
          + '</p>'
          + renderTagPills(r.tags)
          + srcHtml
          + '</article>';
      }}

      function rowMatches(r) {{
        if (state.category && r.category !== state.category) return false;
        if (state.date && r.date !== state.date) return false;
        if (state.ticker) {{
          var tk = (r.ticker || '').toLowerCase();
          if (tk.indexOf(state.ticker.toLowerCase()) === -1) return false;
        }}
        if (state.tags.size) {{
          var hit = false;
          for (var i = 0; i < r.tags.length; i++) {{
            if (state.tags.has(r.tags[i])) {{ hit = true; break; }}
          }}
          if (!hit) return false;
        }}
        if (state.q) {{
          var lang = document.body.dataset.lang || 'en';
          var bag = lang === 'bn'
            ? [r.summary_bn, r.description_bn, r.reason_bn, r.ticker, r.summary]
            : [r.summary, r.description, r.reason, r.ticker, r.summary_bn];
          if (bag.join(' ').toLowerCase().indexOf(state.q.toLowerCase()) === -1)
            return false;
        }}
        return true;
      }}

      function apply() {{
        syncFilterBarHighlight();
        if (!isActive()) {{
          $defaultView.hidden = false;
          $filteredView.hidden = true;
          $filteredView.innerHTML = '';
          setStatus('');
          return;
        }}
        $defaultView.hidden = true;
        $filteredView.hidden = false;
        var lang = document.body.dataset.lang || 'en';
        setStatus(UI[lang].loading);
        ensureHistory().then(function(h) {{
          var rows = h.rows.filter(rowMatches);
          renderResults(rows, h.rows.length, lang);
        }}).catch(function(err) {{
          $filteredView.innerHTML =
            '<p id="filtered-empty">' + escapeHtml(String(err.message || err)) + '</p>';
          setStatus('');
        }});
      }}

      function renderResults(rows, total, lang) {{
        var t = UI[lang];
        if (!rows.length) {{
          $filteredView.innerHTML =
            '<p id="filtered-empty">' + escapeHtml(t.no_match) + '</p>';
          setStatus('');
          return;
        }}
        $filteredView.innerHTML = rows.map(renderCard).join('');
        var noun = rows.length === 1 ? t.result : t.results;
        setStatus(t.showing + ' ' + rows.length + ' ' + t.of + ' ' + total
                  + ' ' + noun);
      }}

      function syncTagChips() {{
        document.querySelectorAll('.filter-chip').forEach(function(el) {{
          el.classList.toggle('is-active', state.tags.has(el.dataset.tag));
        }});
      }}

      function syncCatChips() {{
        document.querySelectorAll('.cat-chip').forEach(function(el) {{
          el.classList.toggle('is-active',
            (el.dataset.category || '') === state.category);
        }});
      }}

      function toggleTag(slug) {{
        if (!slug) return;
        if (state.tags.has(slug)) state.tags.delete(slug);
        else state.tags.add(slug);
        syncTagChips();
        apply();
      }}

      function setCategory(cat) {{
        state.category = cat || '';
        syncCatChips();
        apply();
      }}

      function resetAll() {{
        state.q = ''; state.date = ''; state.category = '';
        state.ticker = ''; state.tags.clear();
        $search.value = ''; $date.value = ''; $ticker.value = '';
        syncTagChips(); syncCatChips();
        apply();
      }}

      function debounce(fn, ms) {{
        var t;
        return function() {{
          clearTimeout(t);
          var args = arguments, ctx = this;
          t = setTimeout(function() {{ fn.apply(ctx, args); }}, ms);
        }};
      }}

      // --- Event wiring ---
      document.addEventListener('click', function(e) {{
        var langBtn = e.target.closest && e.target.closest('[data-set-lang]');
        if (langBtn) {{
          var l = langBtn.dataset.setLang;
          setLang(l);
          try {{ localStorage.setItem(LANG_KEY, l); }} catch (e) {{}}
          if (isActive()) apply();
          return;
        }}
        var pill = e.target.closest && e.target.closest('.tag-pill');
        if (pill) {{ toggleTag(pill.dataset.tag); return; }}
        var chip = e.target.closest && e.target.closest('.filter-chip');
        if (chip) {{ toggleTag(chip.dataset.tag); return; }}
        var catChip = e.target.closest && e.target.closest('.cat-chip');
        if (catChip) {{ setCategory(catChip.dataset.category); return; }}
        if (e.target && e.target.id === 'f-reset') {{ resetAll(); return; }}
      }});

      $search.addEventListener('input', debounce(function(e) {{
        state.q = (e.target.value || '').trim();
        apply();
      }}, 120));
      $date.addEventListener('change', function(e) {{
        state.date = e.target.value || '';
        apply();
      }});
      $ticker.addEventListener('input', debounce(function(e) {{
        state.ticker = (e.target.value || '').trim();
        apply();
      }}, 120));

      // --- Keyboard shortcuts: '/' focuses search, Esc resets filters ---
      document.addEventListener('keydown', function(e) {{
        var inField = e.target
          && /^(input|textarea|select)$/i.test(e.target.tagName);
        if (e.key === '/' && !inField && !e.metaKey && !e.ctrlKey) {{
          e.preventDefault();
          $search.focus();
          $search.select();
          return;
        }}
        if (e.key === 'Escape' && isActive()) {{
          resetAll();
          if (document.activeElement && document.activeElement.blur) {{
            document.activeElement.blur();
          }}
        }}
      }});
    }})();
  </script>
</body>
</html>
"""
